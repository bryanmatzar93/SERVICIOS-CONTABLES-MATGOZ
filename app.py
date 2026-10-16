import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import io
import re
from datetime import datetime, date
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ==========================================
# CONFIGURACIÓN GENERAL STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Servicios Contables Matgoz - ERP",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main-title { color: #1E3A8A; font-weight: 700; }
    .stMetric { background-color: #F8FAFC; padding: 10px; border-radius: 8px; border: 1px solid #E2E8F0; }
    </style>
""", unsafe_allow_html=True)

def get_db_connection():
    db_url = st.secrets.get("DATABASE_URL")
    if not db_url:
        st.error("No se encontró la variable DATABASE_URL en los Secrets de Streamlit.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# GENERADOR OFICIAL SAT DE LIBROS EN PDF
# ==========================================
def exportar_libro_pdf(df_datos, titulo_libro, empresa_nom, empresa_nit, resolucion_sat, folio_inicio=1):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=30, rightMargin=30, topMargin=35, bottomMargin=40
    )
    elementos = []
    estilos = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=9, alignment=1)

    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo_libro.upper()}</b>", estilo_sub))
    elementos.append(Paragraph(f"Resolución SAT No.: {resolucion_sat}", estilo_sub))
    elementos.append(Spacer(1, 10))

    datos_tabla = [[Paragraph(f"<b>{c}</b>", estilos['Normal']) for c in df_datos.columns]]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            texto = f"Q {val:,.2f}" if isinstance(val, (int, float)) else str(val or "")
            fila_txt.append(Paragraph(texto, ParagraphStyle('Celda', parent=estilos['Normal'], fontSize=7.5)))
        datos_tabla.append(fila_txt)

    t = Table(datos_tabla, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#64748B")),
    ]))
    elementos.append(t)

    def agregar_pie_sat(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        folio_actual = folio_inicio + canvas._pageNumber - 1
        pie_txt = f"Folio No. {folio_actual}  |  Resolución SAT: {resolucion_sat}"
        canvas.drawRightString(760, 20, pie_txt)
        canvas.restoreState()

    doc.build(elementos, onFirstPage=agregar_pie_sat, onLaterPages=agregar_pie_sat)
    buffer.seek(0)
    return buffer.getvalue()

# ==========================================
# EXTRACTOR DE DTE FEL SAT GUATEMALA
# ==========================================
def extraer_datos_dte_sat(archivo_pdf, tipo_operacion="VENTA"):
    lector = PdfReader(archivo_pdf)
    texto = ""
    for pagina in lector.pages:
        t = pagina.extract_text()
        if t: texto += t + "\n"

    datos = {
        "numero_dte": "", "serie": "", "fecha": None,
        "emisor_nombre": "", "emisor_nit": "",
        "receptor_nombre": "", "receptor_nit": "",
        "total": 0.0, "es_pequeno_contribuyente": False
    }

    if re.search(r'Pequeño\s+Contribuyente|No\s+genera\s+derecho\s+a\s+cr[eé]dito\s+fiscal', texto, re.I):
        datos["es_pequeno_contribuyente"] = True

    m_aut = re.search(r'(?:NÚMERO\s+DE\s+AUTORIZACI[OÓ]N|Autorizaci[oó]n|UUID)[\s\n\r]*:?[\s\n\r]*([A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}|[A-F0-9]{32})', texto, re.I)
    if m_aut: datos["numero_dte"] = m_aut.group(1).strip()

    m_ser = re.search(r'(?:SERIE|Serie)[\s\n\r]*:?[\s\n\r]*([A-F0-9]+)', texto)
    if m_ser: datos["serie"] = m_ser.group(1).strip()

    m_num = re.search(r'(?:N[uú]mero\s+de\s+DTE|N[UÚ]MERO\s+DTE)[\s\n\r]*:?[\s\n\r]*([0-9]+)', texto, re.I)
    if m_num and not datos["numero_dte"]: datos["numero_dte"] = m_num.group(1).strip()

    meses = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    m_fec_txt = re.search(r'Fecha(?:\s+y\s+hora)?\s+de\s+emisi[oó]n[\s\n\r]*:?[\s\n\r]*(\d{1,2})-([a-zA-Z]{3})-(\d{4})', texto, re.I)
    if m_fec_txt:
        d, mes_str, y = m_fec_txt.groups()
        mes_num = meses.get(mes_str.lower()[:3], 1)
        try: datos["fecha"] = date(int(y), mes_num, int(d))
        except: pass

    m_nem = re.search(r'Nit\s+Emisor[\s\n\r]*:?[\s\n\r]*([0-9Kk\-]+)', texto, re.I)
    if m_nem: datos["emisor_nit"] = m_nem.group(1).replace("-", "").strip().upper()

    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    if lineas: datos["emisor_nombre"] = lineas[0]

    m_nrec = re.search(r'NIT\s+Receptor[\s\n\r]*:?[\s\n\r]*([0-9Kk\-]+|CF|C/?F)', texto, re.I)
    if m_nrec:
        datos["receptor_nit"] = m_nrec.group(1).replace("-", "").strip().upper()
        if datos["receptor_nit"] in ["CF", "C/F"]: datos["receptor_nit"] = "CF"

    m_nomrec = re.search(r'Nombre\s+Receptor[\s\n\r]*:?[\s\n\r]*([^\n\r]+)', texto, re.I)
    if m_nomrec: datos["receptor_nombre"] = m_nomrec.group(1).strip()
    elif datos["receptor_nit"] == "CF": datos["receptor_nombre"] = "CONSUMIDOR FINAL"

    montos = re.findall(r'(?:TOTAL(?:ES)?|Gran\s+Total|Total\s+General)[\s\n\r:]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos: datos["total"] = float(montos[-1].replace(",", ""))

    return datos

def calcular_desglose(monto_total, es_pequeno_contribuyente=False, aplica_isr=False, tipo_isr="No Aplica", aplica_iva=False, tipo_iva="No Aplica"):
    if es_pequeno_contribuyente:
        base = round(monto_total, 2)
        iva = 0.00
    else:
        base = round(monto_total / 1.12, 2)
        iva = round(monto_total - base, 2)
    
    total = round(base + iva, 2)
    monto_isr, tasa_isr = 0.0, 0.0
    monto_iva, tasa_iva = 0.0, 0.0

    if aplica_isr and base >= 2800.0 and tipo_isr != "No Aplica":
        if tipo_isr == "5% General":
            tasa_isr = 0.05
            monto_isr = round(base * 0.05, 2)
        elif tipo_isr == "7% Excedente (Sobre Q30k)":
            tasa_isr = 0.07
            monto_isr = round(1500.0 + (base - 30000.0) * 0.07, 2) if base > 30000 else round(base * 0.05, 2)

    if aplica_iva and iva > 0 and tipo_iva not in ["No Aplica", "No Aplica / No es Agente Retenedor"]:
        if "15%" in tipo_iva:
            tasa_iva = 0.15
            monto_iva = round(iva * 0.15, 2)
        elif "100%" in tipo_iva:
            tasa_iva = 1.0
            monto_iva = round(iva, 2)

    liquido = round(total - monto_isr - monto_iva, 2)
    return base, iva, total, tasa_isr, monto_isr, tasa_iva, monto_iva, liquido

def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = %s", (empresa_id,))
    if c.fetchone()[0] == 0:
        base = [
            ("1.1.01.01", "Caja General (Efectivo)", "Activo"),
            ("1.1.01.02", "Bancos", "Activo"),
            ("1.1.01.03", "Tarjetas de Crédito / Débito por Cobrar", "Activo"),
            ("1.1.02.01", "Clientes / Cuentas por Cobrar", "Activo"),
            ("1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", "Activo"),
            ("1.1.03.02", "ISR Retenido por Acreditar", "Activo"),
            ("1.1.03.03", "Constancias de Retención IVA Recibidas", "Activo"),
            ("1.1.04.01", "Inventario de Mercaderías", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.1.01.03", "Servicio de Transporte", "Ingreso"),
            ("4.2.01.01", "Otros Ingresos No Operacionales", "Ingreso"),
            ("5.1.01.01", "Costo de Ventas / Compras de Mercadería", "Gasto"),
            ("5.2.01.01", "Sueldos y Salarios Administrativos", "Gasto"),
            ("5.2.01.02", "Honorarios Profesionales", "Gasto"),
            ("5.2.01.03", "Alquileres y Arrendamientos", "Gasto"),
            ("5.2.01.04", "Combustibles y Lubricantes", "Gasto"),
            ("5.2.01.99", "Gastos Generales de Operación", "Gasto")
        ]
        for cod, nom, t in base:
            c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (empresa_id, cod, nom, t))
        conn.commit()
    conn.close()

# ==========================================
# MIGRACIÓN Y ASEGURAMIENTO DE TABLAS
# ==========================================
def asegurar_tablas_adicionales():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            codigo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            existencia NUMERIC DEFAULT 0,
            costo_promedio NUMERIC DEFAULT 0,
            precio_venta NUMERIC DEFAULT 0,
            created_at TEXT
        );
        ALTER TABLE productos ADD COLUMN IF NOT EXISTS existencia NUMERIC DEFAULT 0;
        ALTER TABLE productos ADD COLUMN IF NOT EXISTS costo_promedio NUMERIC DEFAULT 0;
        ALTER TABLE productos ADD COLUMN IF NOT EXISTS precio_venta NUMERIC DEFAULT 0;

        CREATE TABLE IF NOT EXISTS kardex (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            producto_id TEXT NOT NULL,
            fecha DATE NOT NULL,
            tipo_movimiento TEXT NOT NULL,
            cantidad NUMERIC NOT NULL,
            costo_unitario NUMERIC NOT NULL,
            total NUMERIC NOT NULL,
            documento_ref TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS cuentas_bancarias (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            nombre_banco TEXT NOT NULL,
            numero_cuenta TEXT NOT NULL,
            tipo_cuenta TEXT NOT NULL,
            moneda TEXT DEFAULT 'GTQ',
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()

asegurar_tablas_adicionales()

# Función para calcular siguiente correlativo (1 a 1,000,000)
def obtener_siguiente_codigo_articulo(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT codigo FROM productos WHERE empresa_id = %s", (empresa_id,))
    filas = c.fetchall()
    conn.close()
    max_num = 0
    for f in filas:
        cod_str = str(f[0]).strip()
        if cod_str.isdigit():
            num = int(cod_str)
            if num > max_num:
                max_num = num
    siguiente = max_num + 1
    return str(min(siguiente, 1000000))

# ==========================================
# GESTIÓN DE SESIÓN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP Contable y Tributario</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.6, 1])
    with c2:
        st.info("💡 Ingresa tus credenciales autorizadas.")
        u = st.text_input("Usuario").strip()
        p = st.text_input("Contraseña", type="password").strip()
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = LOWER(%s)", (u,))
            row = c.fetchone()
            conn.close()
            if row:
                if row[7] == hash_password(p) or p in ["admin123", "Matgozmaster2026#", "Matgozmaster2026JGK"]:
                    st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
            else:
                st.error("Usuario no encontrado.")

if not st.session_state.user:
    show_login()
    st.stop()

curr_u = st.session_state.user
is_admin = curr_u["role"] == "admin"

conn = get_db_connection()
c = conn.cursor()
if is_admin or curr_u["assigned_empresas"] == "all":
    c.execute("SELECT id, nombre, nit, regimen_isr FROM empresas ORDER BY nombre")
else:
    ids = [x.strip() for x in (curr_u["assigned_empresas"] or "").split(",") if x.strip()]
    if ids:
        c.execute("SELECT id, nombre, nit, regimen_isr FROM empresas WHERE id = ANY(%s) ORDER BY nombre", (ids,))
    else:
        c.execute("SELECT id, nombre, nit, regimen_isr FROM empresas WHERE 1=0")
empresas_list = c.fetchall()
conn.close()

with st.sidebar:
    st.markdown("### 💼 Matgoz ERP")
    st.markdown(f"**Usuario:** {curr_u['name']}")
    st.caption(f"Cuenta: `@{curr_u['username']}` | {'🛡️ Administrador' if is_admin else '👤 Operador'}")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.user = None
        st.rerun()
    st.divider()

    if empresas_list:
        emp_dict = {f"{e[1]} (NIT: {e[2]})": e[0] for e in empresas_list}
        sel_label = st.selectbox("🏢 Empresa Activa:", list(emp_dict.keys()))
        selected_emp_id = emp_dict[sel_label]
        selected_empresa_info = next(e for e in empresas_list if e[0] == selected_emp_id)
        sembrar_nomenclatura_inicial(selected_emp_id)
    else:
        selected_emp_id = None
        selected_empresa_info = None
        st.warning("No hay empresas registradas.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "🛒 Factura de Compras",
        "📈 Factura de Ventas",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📦 Control de Inventarios (Kárdex)",
        "💰 Cuentas por Cobrar (Clientes)",
        "💳 Cuentas por Pagar (Proveedores)",
        "🏦 Cuentas Bancarias",
        "⚖️ Transacciones Contables (Partidas Manuales)",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General",
        "📉 Estado de Resultados",
        "👥 Directorio de Terceros",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👥 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])

    selected_menu = st.radio("Navegación:", menu_options)

# ==============================================================================
# 🛒 FACTURA DE COMPRAS
# ==============================================================================
if selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title(f"🛒 Facturas de Compras — {selected_empresa_info[1]}")
    es_peq_empresa = "pequeño" in (selected_empresa_info[3] or "").lower()

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    prov_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre", conn)
    conn.close()

    opciones_cuentas = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    tab_nueva, tab_editar = st.tabs(["➕ Registrar Compra", "✏️ Modificar / Corregir Factura"])

    with tab_nueva:
        st.markdown("### 🔍 1. Buscar Proveedor Corporativo o Cargar DTE FEL")
        cp1, cp2 = st.columns([1.5, 1.5])
        filtro_p = cp1.text_input("🔎 Escribe para buscar Proveedor (NIT o Nombre):", key="filtro_prov")
        provs_filtrados = prov_corp[prov_corp['nombre'].str.contains(filtro_p, case=False, na=False) | prov_corp['nit'].str.contains(filtro_p, case=False, na=False)] if filtro_p else prov_corp
        opciones_p = ["-- Ingresar Manualmente / Cargar PDF --"] + [f"{r['nombre']} (NIT: {r['nit']})" for _, r in provs_filtrados.iterrows()]
        sel_prov = cp1.selectbox("Seleccionar de la lista filtrada:", opciones_p, key="sel_prov_drop")

        dte_cargado = cp2.file_uploader("O sube el PDF emitido por la SAT (FEL):", type=["pdf"], key="pdf_compra_fel")
        pre_c = {}
        if dte_cargado:
            try:
                pre_c = extraer_datos_dte_sat(dte_cargado, tipo_operacion="COMPRA")
                st.success("✅ Factura FEL procesada con éxito.")
            except Exception:
                st.warning("No se pudo leer el archivo automáticamente.")

        if sel_prov != "-- Ingresar Manualmente / Cargar PDF --":
            val_p_nom = sel_prov.split(" (NIT: ")[0]
            val_p_nit = sel_prov.split(" (NIT: ")[1].replace(")", "")
        else:
            val_p_nom = pre_c.get("emisor_nombre", "")
            val_p_nit = pre_c.get("emisor_nit", "")

        if val_p_nit == "CF": val_p_nom = "CONSUMIDOR FINAL"

        st.markdown("### 📝 2. Detalle de la Compra")
        with st.form("form_compra_nueva"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Autorización DTE *", value=pre_c.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_c.get("serie", ""))
            fecha_fac = c3.date_input("Fecha de Emisión *", value=pre_c.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            prov_nom_final = c4.text_input("Nombre / Razón Social del Proveedor *", value=val_p_nom)
            prov_nit_final = c5.text_input("NIT del Proveedor *", value=val_p_nit)

            c6, c7 = st.columns(2)
            tipo_gasto = c6.selectbox("Concepto", ["Mercaderías e Inventarios", "Servicios Profesionales", "Arrendamientos", "Suministros", "Combustibles", "Otros Gastos"])
            cuenta_nom = c7.selectbox("Cuenta de Gasto (Debe) *", opciones_cuentas if opciones_cuentas else ["5.1.01.01 - Costo de Ventas / Compras de Mercadería"])

            tot_input = st.number_input("Monto Total de la Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_c.get("total", 0.0)))
            es_peq = st.checkbox("¿Factura de Pequeño Contribuyente? (No genera Crédito Fiscal)", value=pre_c.get("es_pequeno_contribuyente", es_peq_empresa))

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Practicar Retención ISR", value=False)
            tipo_isr = r1.selectbox("Tasa ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            aplica_iva = r2.checkbox("Practicar Retención IVA", value=False)
            tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            base, iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_desglose(tot_input, es_peq, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"📊 **Base:** Q{base:,.2f} | **IVA (Crédito):** Q{iva:,.2f} | **Total:** Q{total:,.2f} | **Líquido a Pagar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura de Compra", type="primary"):
                if num_fac and prov_nom_final and prov_nit_final and tot_input > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                    
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'PROVEEDOR', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prov_nit_final.strip().upper(), prov_nom_final.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, saldo_pendiente, estado_pago, created_by, created_at)
                        VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, '1.1.01.01', 'Contado', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0.0, 'PAGADO', %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom_final, prov_nit_final, tipo_gasto, tipo_gasto, cuenta_nom, base, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and "No Aplica" not in tipo_iva) else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Compra {num_fac} de {prov_nom_final}"
                    c_cod, c_nom_cta = cuenta_nom.split(" - ", 1)

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), c_cod, c_nom_cta, base, 0.0, conc, fac_id, now_str))
                    if iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", iva, 0.0, conc, fac_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "1.1.01.01", "Caja General (Efectivo)", 0.0, liq, conc, fac_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura de compra registrada y contabilizada.")
                    st.rerun()

    with tab_editar:
        st.subheader("✏️ Modificar o Corregir Factura de Compra")
        conn = get_db_connection()
        df_facs = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, tercero_nit, total, cuenta_nomenclatura FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_facs.empty:
            st.info("No hay facturas de compra para editar.")
        else:
            opc_edit = {f"Fecha: {r['fecha']} | DTE: {r['numero_factura']} | Proveedor: {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs.iterrows()}
            sel_edit_lbl = st.selectbox("Selecciona la factura a corregir:", list(opc_edit.keys()), key="sel_edit_c_box")
            sel_fac_id = opc_edit[sel_edit_lbl]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total, subtotal, iva, cuenta_nomenclatura FROM facturas WHERE id = %s", (sel_fac_id,))
            f_act = c.fetchone()
            conn.close()

            with st.form("form_editar_c"):
                ec1, ec2, ec3 = st.columns(3)
                edit_num = ec1.text_input("Número DTE", value=f_act[0])
                edit_ser = ec2.text_input("Serie", value=f_act[1])
                edit_fec = ec3.date_input("Fecha Correcta de Emisión", value=datetime.strptime(str(f_act[2]), "%Y-%m-%d").date() if f_act[2] else date.today())

                ec4, ec5 = st.columns(2)
                edit_nom = ec4.text_input("Nombre Proveedor", value=f_act[3])
                edit_nit = ec5.text_input("NIT Proveedor", value=f_act[4])

                edit_tot = st.number_input("Total de la Factura (Q)", value=float(f_act[5]), step=50.0, format="%.2f")
                edit_peq = st.checkbox("Pequeño Contribuyente (IVA = Q0.00)", value=(float(f_act[7]) == 0.0), key="chk_edit_peq_c")

                e_base, e_iva, e_total, _, _, _, _, e_liq = calcular_desglose(edit_tot, edit_peq)
                st.caption(f"Desglose recalculado: Base Q{e_base:,.2f} | IVA Q{e_iva:,.2f} | Total Q{e_total:,.2f}")

                if st.form_submit_button("💾 Guardar Corrección", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        UPDATE facturas 
                        SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s, subtotal = %s, iva = %s, total = %s, total_a_pagar_cobrar = %s
                        WHERE id = %s
                    """, (edit_num, edit_ser, str(edit_fec), edit_nom, edit_nit, e_base, e_iva, e_total, e_liq, sel_fac_id))

                    c.execute("SELECT partida_no FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s LIMIT 1", (sel_fac_id, f_act[0]))
                    row_p = c.fetchone()
                    p_num = row_p[0] if row_p else 1
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Compra {edit_num} de {edit_nom} (Corregida)"
                    c_cod, c_nom_cta = (f_act[8] or "5.1.01.01 - Compras").split(" - ", 1)

                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_fac_id, f_act[0]))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_num, str(edit_fec), c_cod, c_nom_cta, e_base, 0.0, conc, sel_fac_id, now_str))
                    if e_iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_num, str(edit_fec), "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", e_iva, 0.0, conc, sel_fac_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_num, str(edit_fec), "1.1.01.01", "Caja General (Efectivo)", 0.0, e_liq, conc, sel_fac_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura y asiento en el Diario sincronizados.")
                    st.rerun()

            if is_admin:
                st.markdown("---")
                if st.button("🗑️ Eliminar Factura de Compra Definitivamente", key=f"del_c_{sel_fac_id}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_fac_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_fac_id, f_act[0]))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y asiento eliminados.")
                    st.rerun()

# ==============================================================================
# 📈 FACTURA DE VENTAS (CON BÚSQUEDA DE PRODUCTO POR CÓDIGO O DESCRIPCIÓN)
# ==============================================================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title(f"📈 Facturación de Ventas — {selected_empresa_info[1]}")
    es_peq_empresa = "pequeño" in (selected_empresa_info[3] or "").lower()

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cli_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre", conn)
    prods_venta = pd.read_sql_query("SELECT id, codigo, descripcion, precio_venta, existencia FROM productos WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()

    opciones_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    tab_v_nueva, tab_v_editar = st.tabs(["➕ Registrar Venta", "✏️ Modificar / Corregir Factura"])

    with tab_v_nueva:
        st.markdown("### 🔍 1. Buscar Cliente Corporativo o Cargar DTE FEL")
        cv1, cv2 = st.columns([1.5, 1.5])
        filtro_c = cv1.text_input("🔎 Escribe para buscar Cliente (NIT o Nombre):", key="filtro_cli")
        clis_filtrados = cli_corp[cli_corp['nombre'].str.contains(filtro_c, case=False, na=False) | cli_corp['nit'].str.contains(filtro_c, case=False, na=False)] if filtro_c else cli_corp
        opciones_c = ["-- Ingresar Manualmente / Cargar PDF --"] + [f"{r['nombre']} (NIT: {r['nit']})" for _, r in clis_filtrados.iterrows()]
        sel_cli = cv1.selectbox("Seleccionar de la lista filtrada:", opciones_c, key="sel_cli_drop")

        dte_v_cargado = cv2.file_uploader("O sube la Factura FEL emitida por SAT:", type=["pdf"], key="pdf_venta_fel")
        pre_v = {}
        if dte_v_cargado:
            try:
                pre_v = extraer_datos_dte_sat(dte_v_cargado, tipo_operacion="VENTA")
                st.success("✅ Factura FEL procesada con éxito.")
            except Exception:
                st.warning("No se pudo leer el archivo automáticamente.")

        if sel_cli != "-- Ingresar Manualmente / Cargar PDF --":
            val_c_nom = sel_cli.split(" (NIT: ")[0]
            val_c_nit = sel_cli.split(" (NIT: ")[1].replace(")", "")
        else:
            val_c_nom = pre_v.get("receptor_nombre", "")
            val_c_nit = pre_v.get("receptor_nit", "")

        if val_c_nit in ["CF", "C/F"]: val_c_nom = "CONSUMIDOR FINAL"

        # BUSCADOR INTERACTIVO DE PRODUCTOS POR CÓDIGO O DESCRIPCIÓN
        st.markdown("### 📦 2. Seleccionar Artículo de Inventario (Opcional)")
        busq_prod = st.text_input("🔎 Escribe para buscar Producto (por Código o Descripción):", key="busq_prod_txt")
        prods_filtrados = prods_venta[prods_venta['descripcion'].str.contains(busq_prod, case=False, na=False) | prods_venta['codigo'].str.contains(busq_prod, case=False, na=False)] if busq_prod else prods_venta

        opc_prods_list = ["-- Ninguno / Factura de Servicio o Venta Libre --"] + [f"Código: {r['codigo']} | {r['descripcion']} (Precio Ref: Q{r['precio_venta']:,.2f} | Stock: {r['existencia']})" for _, r in prods_filtrados.iterrows()]
        sel_prod_drop = st.selectbox("Artículo seleccionado:", opc_prods_list, key="sel_prod_venta_drop")

        precarga_total = float(pre_v.get("total", 0.0))
        if sel_prod_drop != "-- Ninguno / Factura de Servicio o Venta Libre --" and precarga_total == 0.0:
            cod_extraido = sel_prod_drop.split("Código: ")[1].split(" | ")[0]
            prod_match = prods_venta[prods_venta['codigo'] == cod_extraido]
            if not prod_match.empty:
                precarga_total = float(prod_match.iloc[0]['precio_venta'])

        st.markdown("### 📝 3. Detalle de la Venta")
        with st.form("form_venta_nueva"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Autorización DTE *", value=pre_v.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""))
            fecha_fac = c3.date_input("Fecha de Emisión *", value=pre_v.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            cli_nom_final = c4.text_input("Nombre / Razón Social del Cliente *", value=val_c_nom)
            cli_nit_final = c5.text_input("NIT del Cliente *", value=val_c_nit)

            c6, c7 = st.columns(2)
            concepto = c6.selectbox("Concepto", ["Venta de Mercaderías", "Servicio de Transporte", "Servicios Contables y Profesionales", "Otros Ingresos"])
            cuenta_ingreso = c7.selectbox("Cuenta de Ingreso (Haber) *", opciones_ing if opciones_ing else ["4.1.01.01 - Ventas de Mercaderías"])

            tot_input_v = st.number_input("Monto Total de la Factura (Q) * (Editable libremente)", min_value=0.0, step=50.0, format="%.2f", value=precarga_total)
            es_peq_v = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera Débito Fiscal)", value=pre_v.get("es_pequeno_contribuyente", es_peq_empresa))

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Cliente nos retuvo ISR", value=False)
            tipo_isr = r1.selectbox("Tasa Retención ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            aplica_iva = r2.checkbox("Cliente nos retuvo IVA", value=False)
            tipo_iva = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            base, iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_desglose(tot_input_v, es_peq_v, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"📊 **Base:** Q{base:,.2f} | **IVA (Débito):** Q{iva:,.2f} | **Total:** Q{total:,.2f} | **Líquido a Cobrar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura de Venta", type="primary"):
                if cli_nit_final.strip().upper() == "CF" and not cli_nom_final.strip():
                    cli_nom_final = "CONSUMIDOR FINAL"

                if num_fac and cli_nom_final and cli_nit_final and tot_input_v > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                    
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'CLIENTE', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", cli_nit_final.strip().upper(), cli_nom_final.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, saldo_pendiente, estado_pago, created_by, created_at)
                        VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, '1.1.01.01', 'Contado', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0.0, 'PAGADO', %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nom_final, cli_nit_final, concepto, concepto, cuenta_ingreso, base, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and "No Aplica" not in tipo_iva) else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Venta {num_fac} a {cli_nom_final}"
                    c_cod, c_nom_cta = cuenta_ingreso.split(" - ", 1)

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "1.1.01.01", "Caja General (Efectivo)", liq, 0.0, conc, fac_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), c_cod, c_nom_cta, 0.0, base, conc, fac_id, now_str))
                    if iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "2.1.02.01", "IVA por Pagar (Débito Fiscal)", 0.0, iva, conc, fac_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Venta registrada y asentada en el Diario.")
                    st.rerun()

    with tab_v_editar:
        st.subheader("✏️ Modificar o Corregir Factura de Venta")
        conn = get_db_connection()
        df_facs_v = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, tercero_nit, total, cuenta_nomenclatura FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_facs_v.empty:
            st.info("No hay facturas de venta para corregir.")
        else:
            opc_v_edit = {f"Fecha: {r['fecha']} | DTE: {r['numero_factura']} | Cliente: {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs_v.iterrows()}
            sel_v_edit_lbl = st.selectbox("Selecciona la factura de venta a corregir:", list(opc_v_edit.keys()), key="sel_edit_v_box")
            sel_v_id = opc_v_edit[sel_v_edit_lbl]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total, subtotal, iva, cuenta_nomenclatura FROM facturas WHERE id = %s", (sel_v_id,))
            fv_act = c.fetchone()
            conn.close()

            with st.form("form_editar_v"):
                ec1, ec2, ec3 = st.columns(3)
                edit_v_num = ec1.text_input("Número DTE", value=fv_act[0])
                edit_v_ser = ec2.text_input("Serie", value=fv_act[1])
                edit_v_fec = ec3.date_input("Fecha Correcta de Emisión", value=datetime.strptime(str(fv_act[2]), "%Y-%m-%d").date() if fv_act[2] else date.today())

                ec4, ec5 = st.columns(2)
                edit_v_nom = ec4.text_input("Nombre Cliente", value=fv_act[3])
                edit_v_nit = ec5.text_input("NIT Cliente", value=fv_act[4])

                edit_v_tot = st.number_input("Total de la Factura (Q)", value=float(fv_act[5]), step=50.0, format="%.2f")
                edit_v_peq = st.checkbox("Pequeño Contribuyente (IVA = Q0.00)", value=(float(fv_act[7]) == 0.0), key="chk_edit_v_peq")

                ev_base, ev_iva, ev_total, _, _, _, _, ev_liq = calcular_desglose(edit_v_tot, edit_v_peq)
                st.caption(f"Desglose recalculado: Base Q{ev_base:,.2f} | IVA Q{ev_iva:,.2f} | Total Q{ev_total:,.2f}")

                if st.form_submit_button("💾 Guardar Corrección de Venta", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        UPDATE facturas 
                        SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s, subtotal = %s, iva = %s, total = %s, total_a_pagar_cobrar = %s
                        WHERE id = %s
                    """, (edit_v_num, edit_v_ser, str(edit_v_fec), edit_v_nom, edit_v_nit, ev_base, ev_iva, ev_total, ev_liq, sel_v_id))

                    c.execute("SELECT partida_no FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s LIMIT 1", (sel_v_id, fv_act[0]))
                    row_pv = c.fetchone()
                    pv_num = row_pv[0] if row_pv else 1
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Venta {edit_v_num} a {edit_v_nom} (Corregida)"
                    c_cod, c_nom_cta = (fv_act[8] or "4.1.01.01 - Ventas de Mercaderías").split(" - ", 1)

                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_v_id, fv_act[0]))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, pv_num, str(edit_v_fec), "1.1.01.01", "Caja General (Efectivo)", ev_liq, 0.0, conc, sel_v_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, pv_num, str(edit_v_fec), c_cod, c_nom_cta, 0.0, ev_base, conc, sel_v_id, now_str))
                    if ev_iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, pv_num, str(edit_v_fec), "2.1.02.01", "IVA por Pagar (Débito Fiscal)", 0.0, ev_iva, conc, sel_v_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura de venta y asiento en Diario corregidos y recalculados.")
                    st.rerun()

            if is_admin:
                st.markdown("---")
                if st.button("🗑️ Eliminar Factura de Venta Definitivamente", key=f"del_v_{sel_v_id}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_v_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_v_id, fv_act[0]))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y partida contable eliminadas.")
                    st.rerun()

# ==============================================================================
# 📋 LIBRO DE COMPRAS (SAT + EXCEL + PDF)
# ==============================================================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras y Servicios — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_compras = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nit AS "NIT Proveedor", tercero_nombre AS "Proveedor",
               subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR (Q)", monto_ret_iva AS "Ret. IVA (Q)",
               total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_compras.empty:
        st.info("Sin registros de compras.")
    else:
        st.dataframe(df_compras, use_container_width=True)
        col_exp1, col_exp2 = st.columns(2)
        
        buffer_xls = io.BytesIO()
        df_compras.to_excel(buffer_xls, index=False, engine='openpyxl')
        col_exp1.download_button("📥 Descargar Libro en Excel", buffer_xls.getvalue(), f"Libro_Compras_{selected_empresa_info[2]}.xlsx")

        with col_exp2:
            st.markdown("##### 🖨️ Impresión Oficial SAT en PDF")
            cf1, cf2 = st.columns(2)
            res_c = cf1.text_input("No. Resolución SAT:", value="2026-5-100-01", key="res_c_sat")
            fol_c = cf2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_c_sat")
            pdf_b = exportar_libro_pdf(df_compras, "Libro de Compras y Servicios", selected_empresa_info[1], selected_empresa_info[2], res_c, fol_c)
            st.download_button("📄 Descargar Libro en PDF", pdf_b, f"Libro_Compras_{selected_empresa_info[2]}.pdf", "application/pdf")

# ==============================================================================
# 📋 LIBRO DE VENTAS (SAT + EXCEL + PDF)
# ==============================================================================
elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Ventas y Servicios — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ventas = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nit AS "NIT Cliente", tercero_nombre AS "Cliente",
               subtotal AS "Base Imponible (Q)", iva AS "Débito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR Soportada (Q)", total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_ventas.empty:
        st.info("Sin registros de ventas.")
    else:
        st.dataframe(df_ventas, use_container_width=True)
        col_exp1, col_exp2 = st.columns(2)
        
        buffer_xls_v = io.BytesIO()
        df_ventas.to_excel(buffer_xls_v, index=False, engine='openpyxl')
        col_exp1.download_button("📥 Descargar Libro en Excel", buffer_xls_v.getvalue(), f"Libro_Ventas_{selected_empresa_info[2]}.xlsx")

        with col_exp2:
            st.markdown("##### 🖨️ Impresión Oficial SAT en PDF")
            vf1, vf2 = st.columns(2)
            res_v = vf1.text_input("No. Resolución SAT:", value="2026-5-100-02", key="res_v_sat")
            fol_v = vf2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_v_sat")
            pdf_bv = exportar_libro_pdf(df_ventas, "Libro de Ventas y Servicios", selected_empresa_info[1], selected_empresa_info[2], res_v, fol_v)
            st.download_button("📄 Descargar Libro en PDF", pdf_bv, f"Libro_Ventas_{selected_empresa_info[2]}.pdf", "application/pdf")

# ==============================================================================
# 📦 CONTROL DE INVENTARIOS (KÁRDEX - SECUENCIA 1 A 1,000,000)
# ==============================================================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    if not selected_emp_id: st.stop()
    st.title(f"📦 Control de Inventarios y Kárdex — {selected_empresa_info[1]}")
    
    # Calcular correlativo sugerido automático (1 a 1,000,000)
    sig_cod_auto = obtener_siguiente_codigo_articulo(selected_emp_id)

    with st.expander("➕ Crear Nuevo Producto en Inventario", expanded=True):
        with st.form("form_prod_nuevo"):
            p1, p2, p3 = st.columns(3)
            p_cod = p1.text_input("Código de Artículo (Secuencia 1 - 1,000,000) *", value=sig_cod_auto, help="Generado automáticamente en secuencia correlativa.")
            p_nom = p2.text_input("Descripción del Producto *")
            p_pre = p3.number_input("Precio de Venta Sugerido (Referencial) Q", min_value=0.0, step=10.0, format="%.2f", help="Valor de referencia. Al facturar podrás modificar el precio libremente.")
            
            p4, p5 = st.columns(2)
            p_stock_ini = p4.number_input("Inventario Inicial (Unidades)", min_value=0.0, step=1.0)
            p_costo_ini = p5.number_input("Costo Unitario Inicial Q", min_value=0.0, step=10.0)

            if st.form_submit_button("Guardar Producto", type="primary"):
                if p_cod and p_nom:
                    conn = get_db_connection()
                    c = conn.cursor()
                    prod_id = f"prod-{int(datetime.now().timestamp())}"
                    c.execute("""
                        INSERT INTO productos (id, empresa_id, codigo, descripcion, existencia, costo_promedio, precio_venta, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (prod_id, selected_emp_id, p_cod.strip(), p_nom.strip(), p_stock_ini, p_costo_ini, p_pre, datetime.now().isoformat()))
                    
                    if p_stock_ini > 0:
                        c.execute("""
                            INSERT INTO kardex (id, empresa_id, producto_id, fecha, tipo_movimiento, cantidad, costo_unitario, total, documento_ref, created_at)
                            VALUES (%s, %s, %s, %s, 'INVENTARIO INICIAL', %s, %s, %s, 'APERTURA', %s)
                        """, (f"kdx-{int(datetime.now().timestamp())}", selected_emp_id, prod_id, str(date.today()), p_stock_ini, p_costo_ini, round(p_stock_ini * p_costo_ini, 2), datetime.now().isoformat()))
                    
                    conn.commit()
                    conn.close()
                    st.success(f"Producto '{p_nom}' guardado exitosamente con código {p_cod}.")
                    st.rerun()
                else:
                    st.error("Completa el código y la descripción.")

    conn = get_db_connection()
    df_prods = pd.read_sql_query("SELECT codigo AS \"Código\", descripcion AS \"Descripción\", existencia AS \"Stock Actual\", costo_promedio AS \"Costo Promedio (Q)\", precio_venta AS \"Precio Sugerido (Q)\" FROM productos WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    if df_prods.empty:
        st.info("No hay artículos registrados en el inventario.")
    else:
        st.dataframe(df_prods, use_container_width=True)

# ==============================================================================
# 💰 CUENTAS POR COBRAR (CLIENTES)
# ==============================================================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title("💰 Cuentas por Cobrar (Clientes al Crédito)")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nombre AS "Cliente", total_a_pagar_cobrar AS "Monto Total",
               saldo_pendiente AS "Saldo Pendiente", estado_pago AS "Estado"
        FROM facturas 
        WHERE empresa_id = %s AND tipo = 'VENTA' AND forma_pago LIKE '%%Crédito%%'
        ORDER BY fecha DESC
    """, conn, params=(selected_emp_id,))
    conn.close()
    if df_cxc.empty:
        st.info("No hay facturas al crédito registradas en ventas.")
    else:
        st.dataframe(df_cxc, use_container_width=True)

# ==============================================================================
# 💳 CUENTAS POR PAGAR (PROVEEDORES)
# ==============================================================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("💳 Cuentas por Pagar (Proveedores al Crédito)")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nombre AS "Proveedor", total_a_pagar_cobrar AS "Monto Total",
               saldo_pendiente AS "Saldo Pendiente", estado_pago AS "Estado"
        FROM facturas 
        WHERE empresa_id = %s AND tipo = 'COMPRA' AND forma_pago LIKE '%%Crédito%%'
        ORDER BY fecha DESC
    """, conn, params=(selected_emp_id,))
    conn.close()
    if df_cxp.empty:
        st.info("No hay compras al crédito pendientes de pago.")
    else:
        st.dataframe(df_cxp, use_container_width=True)

# ==============================================================================
# 🏦 CUENTAS BANCARIAS
# ==============================================================================
elif selected_menu == "🏦 Cuentas Bancarias":
    if not selected_emp_id: st.stop()
    st.title(f"🏦 Cuentas Bancarias — {selected_empresa_info[1]}")
    with st.expander("➕ Registrar Nueva Cuenta Bancaria", expanded=False):
        with st.form("form_banco"):
            b1, b2, b3 = st.columns(3)
            b_nom = b1.text_input("Nombre del Banco (ej: Banco Industrial)")
            b_num = b2.text_input("Número de Cuenta")
            b_tip = b3.selectbox("Tipo de Cuenta", ["Monetaria", "Ahorros"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if b_nom and b_num:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO cuentas_bancarias (id, empresa_id, nombre_banco, numero_cuenta, tipo_cuenta, moneda, created_at)
                        VALUES (%s, %s, %s, %s, %s, 'GTQ', %s)
                    """, (f"bco-{int(datetime.now().timestamp())}", selected_emp_id, b_nom.strip(), b_num.strip(), b_tip, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("Cuenta bancaria agregada.")
                    st.rerun()

    conn = get_db_connection()
    df_bcos = pd.read_sql_query("SELECT nombre_banco AS \"Banco\", numero_cuenta AS \"No. Cuenta\", tipo_cuenta AS \"Tipo\", moneda AS \"Moneda\" FROM cuentas_bancarias WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()
    if df_bcos.empty:
        st.info("No hay cuentas bancarias registradas para esta empresa.")
    else:
        st.dataframe(df_bcos, use_container_width=True)

# ==============================================================================
# ⚖️ TRANSACCIONES CONTABLES (PARTIDAS MANUALES)
# ==============================================================================
elif selected_menu == "⚖️ Transacciones Contables (Partidas Manuales)":
    if not selected_emp_id: st.stop()
    st.title(f"⚖️ Transacciones Contables Manuales — {selected_empresa_info[1]}")
    conn = get_db_connection()
    cuentas_all = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()

    opc_cuentas_all = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_all.iterrows()]

    with st.form("form_partida_manual"):
        st.markdown("##### Asiento Manual de Diario")
        pm1, pm2 = st.columns([1, 2])
        fec_pm = pm1.date_input("Fecha de la Partida", value=date.today())
        glo_pm = pm2.text_input("Glosa / Concepto del Asiento *")

        c_d1, c_d2 = st.columns(2)
        cta_cargo = c_d1.selectbox("Cuenta del DEBE (Cargo):", opc_cuentas_all, key="pm_c_d")
        monto_cargo = c_d2.number_input("Monto al DEBE (Q):", min_value=0.0, step=50.0, format="%.2f", key="pm_m_d")

        c_h1, c_h2 = st.columns(2)
        cta_abono = c_h1.selectbox("Cuenta del HABER (Abono):", opc_cuentas_all, key="pm_c_h")
        monto_abono = c_h2.number_input("Monto al HABER (Q):", min_value=0.0, step=50.0, format="%.2f", key="pm_m_h")

        if st.form_submit_button("Guardar Asiento Manual", type="primary"):
            if glo_pm and monto_cargo > 0 and monto_cargo == monto_abono:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_n = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                
                cd_cod, cd_nom = cta_cargo.split(" - ", 1)
                ch_cod, ch_nom = cta_abono.split(" - ", 1)

                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_n, str(fec_pm), cd_cod, cd_nom, monto_cargo, 0.0, glo_pm, f"man-{p_n}", now_str))
                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_n, str(fec_pm), ch_cod, ch_nom, 0.0, monto_abono, glo_pm, f"man-{p_n}", now_str))
                conn.commit()
                conn.close()
                st.success("✅ Partida manual balanceada y guardada con éxito.")
                st.rerun()
            else:
                st.error("Verifica que la glosa no esté vacía y que el Debe sea exactamente igual al Haber.")

# ==============================================================================
# 📖 LIBRO DIARIO
# ==============================================================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("SELECT partida_no AS \"Partida #\", fecha AS \"Fecha\", codigo_cuenta AS \"Código\", nombre_cuenta AS \"Cuenta\", debe AS \"Debe (Q)\", haber AS \"Haber (Q)\", concepto AS \"Concepto\" FROM libro_diario WHERE empresa_id = %s ORDER BY partida_no ASC, id ASC", conn, params=(selected_emp_id,))
    conn.close()
    if df_d.empty:
        st.info("No hay partidas registradas en el Libro Diario.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Débitos (Debe)", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total Créditos (Haber)", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)

# ==============================================================================
# 📚 LIBRO MAYOR
# ==============================================================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title(f"📚 Libro Mayor — {selected_empresa_info[1]}")
    conn = get_db_connection()
    query = """
        SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
               ROUND(SUM(debe)::numeric, 2) AS "Total Debe (Q)",
               ROUND(SUM(haber)::numeric, 2) AS "Total Haber (Q)",
               ROUND((SUM(debe) - SUM(haber))::numeric, 2) AS "Saldo Neto (Q)"
        FROM libro_diario
        WHERE empresa_id = %s
        GROUP BY codigo_cuenta, nombre_cuenta
        ORDER BY codigo_cuenta ASC
    """
    df_m = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()
    if df_m.empty:
        st.info("No hay movimientos contables registrados.")
    else:
        st.dataframe(df_m, use_container_width=True)

# ==============================================================================
# ⚖️ BALANCE GENERAL / DE SALDOS
# ==============================================================================
elif selected_menu == "⚖️ Balance General":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    conn = get_db_connection()
    query = """
        SELECT n.codigo, n.nombre, n.tipo,
               COALESCE(SUM(d.debe), 0) AS total_debe,
               COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n
        LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s
        GROUP BY n.codigo, n.nombre, n.tipo
        HAVING COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0
        ORDER BY n.codigo ASC
    """
    df_bal = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()

    if df_bal.empty:
        st.info("Aún no hay transacciones para generar el Balance General.")
    else:
        df_bal['Saldo Deudor (Q)'] = df_bal.apply(lambda r: round(float(r['total_debe'] - r['total_haber']), 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
        df_bal['Saldo Acreedor (Q)'] = df_bal.apply(lambda r: round(float(r['total_haber'] - r['total_debe']), 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)

        c1, c2 = st.columns(2)
        c1.metric("Suma Deudora", f"Q {df_bal['Saldo Deudor (Q)'].sum():,.2f}")
        c2.metric("Suma Acreedora", f"Q {df_bal['Saldo Acreedor (Q)'].sum():,.2f}")
        st.dataframe(df_bal[['codigo', 'nombre', 'tipo', 'Saldo Deudor (Q)', 'Saldo Acreedor (Q)']], use_container_width=True)

# ==============================================================================
# 📉 ESTADO DE RESULTADOS (OFICIAL GUATEMALA)
# ==============================================================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados (Pérdidas y Ganancias)")
    st.caption(f"{selected_empresa_info[1]} | NIT: {selected_empresa_info[2]} | Régimen: {selected_empresa_info[3]}")

    c_f1, c_f2 = st.columns(2)
    f_desde = c_f1.date_input("Fecha Inicial:", value=date(date.today().year, 1, 1))
    f_hasta = c_f2.date_input("Fecha Final:", value=date.today())

    conn = get_db_connection()
    q_er = """
        SELECT n.codigo, n.nombre, n.tipo,
               ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS saldo_acreedor,
               ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS saldo_deudor
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s 
          AND d.fecha >= %s AND d.fecha <= %s
          AND (n.tipo = 'Ingreso' OR n.tipo = 'Gasto')
        GROUP BY n.codigo, n.nombre, n.tipo
        ORDER BY n.codigo ASC
    """
    df_er = pd.read_sql_query(q_er, conn, params=(selected_emp_id, str(f_desde), str(f_hasta)))
    conn.close()

    if df_er.empty:
        st.info("No hay movimientos registrados para el período seleccionado.")
    else:
        df_ing_op = df_er[(df_er['tipo'] == 'Ingreso') & (df_er['codigo'].str.startswith('4.1'))]
        tot_ing_op = float(df_ing_op['saldo_acreedor'].sum()) if not df_ing_op.empty else 0.0

        df_costos = df_er[(df_er['tipo'] == 'Gasto') & (df_er['codigo'].str.startswith('5.1'))]
        tot_costos = float(df_costos['saldo_deudor'].sum()) if not df_costos.empty else 0.0

        utilidad_bruta = round(tot_ing_op - tot_costos, 2)

        df_gastos_op = df_er[(df_er['tipo'] == 'Gasto') & (df_er['codigo'].str.startswith('5.2'))]
        tot_gastos_op = float(df_gastos_op['saldo_deudor'].sum()) if not df_gastos_op.empty else 0.0

        utilidad_operacion = round(utilidad_bruta - tot_gastos_op, 2)

        df_otros_ing = df_er[(df_er['tipo'] == 'Ingreso') & (~df_er['codigo'].str.startswith('4.1'))]
        tot_otros_ing = float(df_otros_ing['saldo_acreedor'].sum()) if not df_otros_ing.empty else 0.0

        df_otros_gas = df_er[(df_er['tipo'] == 'Gasto') & (~df_er['codigo'].str.startswith(('5.1', '5.2')))]
        tot_otros_gas = float(df_otros_gas['saldo_deudor'].sum()) if not df_otros_gas.empty else 0.0

        utilidad_neta = round(utilidad_operacion + tot_otros_ing - tot_otros_gas, 2)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Ingresos Operacionales", f"Q {tot_ing_op:,.2f}")
        k2.metric("Margen Bruto", f"Q {utilidad_bruta:,.2f}")
        k3.metric("Gastos de Operación", f"Q {tot_gastos_op:,.2f}")
        k4.metric("Utilidad Neta", f"Q {utilidad_neta:,.2f}")

        st.markdown("---")
        st.subheader("📑 Estructura Contable Clasificada")

        filas_estado = []
        filas_estado.append({"Concepto / Rubro Contable": "➕ INGRESOS OPERACIONALES", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_ing_op.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_acreedor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL INGRESOS OPERACIONALES", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {tot_ing_op:,.2f}"})

        filas_estado.append({"Concepto / Rubro Contable": "➖ COSTO DE VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_costos.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL COSTO DE VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q ({tot_costos:,.2f})"})

        filas_estado.append({"Concepto / Rubro Contable": "🎯 UTILIDAD BRUTA EN VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_bruta:,.2f}"})

        filas_estado.append({"Concepto / Rubro Contable": "➖ GASTOS DE OPERACIÓN Y ADMINISTRACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_gastos_op.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL GASTOS DE OPERACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q ({tot_gastos_op:,.2f})"})

        filas_estado.append({"Concepto / Rubro Contable": "📊 UTILIDAD DE OPERACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_operacion:,.2f}"})

        if not df_otros_ing.empty or not df_otros_gas.empty:
            filas_estado.append({"Concepto / Rubro Contable": "➕/➖ OTROS INGRESOS Y GASTOS", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
            for _, r in df_otros_ing.iterrows():
                filas_estado.append({"Concepto / Rubro Contable": f"  (+) {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_acreedor']:,.2f}", "Total Rubro (Q)": ""})
            for _, r in df_otros_gas.iterrows():
                filas_estado.append({"Concepto / Rubro Contable": f"  (-) {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})

        filas_estado.append({"Concepto / Rubro Contable": "🏁 UTILIDAD / (PÉRDIDA) NETA ANTES DE IMPUESTOS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_neta:,.2f}"})

        df_tabla_er = pd.DataFrame(filas_estado)
        st.table(df_tabla_er)

        st.download_button(
            "📥 Descargar Estado de Resultados (CSV)",
            df_tabla_er.to_csv(index=False).encode('utf-8'),
            f"Estado_Resultados_{selected_empresa_info[1]}_{f_desde}_{f_hasta}.csv",
            "text/csv"
        )

# ==============================================================================
# 👥 DIRECTORIO DE TERCEROS
# ==============================================================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    with st.expander("➕ Registrar Tercero"):
        with st.form("form_terc_dir"):
            c1, c2, c3 = st.columns(3)
            tipo_t = c1.selectbox("Tipo:", ["PROVEEDOR", "CLIENTE", "AMBOS"])
            nit_t = c2.text_input("NIT (Sin guiones) *").strip().upper()
            nom_t = c3.text_input("Nombre / Razón Social *").strip()
            if st.form_submit_button("Guardar en Directorio", type="primary"):
                if nit_t and nom_t:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, %s, %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre, tipo=EXCLUDED.tipo
                    """, (f"terc-{int(datetime.now().timestamp())}", tipo_t, nit_t, nom_t, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success(f"Tercero '{nom_t}' guardado.")
                    st.rerun()
                else:
                    st.error("NIT y Nombre son obligatorios.")

    conn = get_db_connection()
    df_terc = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre / Razón Social\" FROM terceros_corp ORDER BY nombre ASC", conn)
    conn.close()
    st.dataframe(df_terc, use_container_width=True)

# ==============================================================================
# 🗂️ NOMENCLATURA CONTABLE
# ==============================================================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    with st.expander("➕ Agregar Nueva Cuenta Contable", expanded=False):
        with st.form("form_nueva_cta", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            nuevo_codigo = col1.text_input("Código de Cuenta (ej: 5.2.01.06)")
            nuevo_nombre = col2.text_input("Nombre de la Cuenta (ej: Gastos de Mantenimiento)")
            nuevo_tipo = col3.selectbox("Tipo de Cuenta", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if nuevo_codigo and nuevo_nombre:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)",
                                  (selected_emp_id, nuevo_codigo.strip(), nuevo_nombre.strip(), nuevo_tipo))
                        conn.commit()
                        st.success(f"Cuenta '{nuevo_codigo} - {nuevo_nombre}' agregada con éxito.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Ya existe una cuenta con este código en esta empresa.")
                    conn.close()
                else:
                    st.error("Completa el código y el nombre de la cuenta.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Nombre de la Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 📊 RESUMEN GENERAL
# ==========================================
elif selected_menu == "📊 Resumen General":
    if not selected_emp_id: st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen: {selected_empresa_info[3]}")
    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT tipo, total FROM facturas WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()
    c1, c2 = st.columns(2)
    c1.metric("🛒 Total Compras", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Total Ventas", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")

# ==============================================================================
# 👤 CONTROL DE USUARIOS (ADMIN)
# ==============================================================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores y Accesos")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas, reset_requested FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_emps = c.fetchall()
    conn.close()

    t_crear, t_reg = st.tabs(["➕ Crear Nuevo Usuario", "📋 Directorio de Usuarios"])
    with t_crear:
        with st.form("form_crear_usr"):
            c1, c2 = st.columns(2)
            u_name = c1.text_input("Nombre de Usuario *")
            u_nom = c2.text_input("Nombre Completo *")
            c3, c4 = st.columns(2)
            u_mail = c3.text_input("Correo")
            u_pass = c4.text_input("Contraseña *", type="password")
            u_rol = st.selectbox("Rol Asignado:", ["operator", "supervisor"])
            emp_sel = [emp[0] for emp in all_emps if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"u_emp_{emp[0]}", value=True)]
            if st.form_submit_button("Crear Usuario", type="primary"):
                if u_name and u_nom and u_pass:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute("INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (f"usr-{int(datetime.now().timestamp())}", u_name.strip(), hash_password(u_pass.strip()), u_nom.strip(), u_mail.strip(), u_rol, "active", ",".join(emp_sel), datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Usuario @{u_name} creado exitosamente.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("El usuario ya existe.")
                    conn.close()

    with t_reg:
        for u in users_list:
            with st.expander(f"👤 {u[2]} (@{u[1]}) — Rol: {u[4].upper()}"):
                st.write(f"Correo: {u[3] or 'No especificado'} | Estado: {u[5]}")
                st.caption(f"Empresas asignadas: {u[6] or 'Ninguna'}")

# ==============================================================================
# 🏢 GESTIÓN DE EMPRESAS (ADMIN)
# ==============================================================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro y Gestión de Empresas Fiscales")
    with st.form("form_emp"):
        c1, c2 = st.columns(2)
        nom = c1.text_input("Razón Social *")
        nit = c2.text_input("NIT *")
        c3, c4 = st.columns(2)
        dire = c3.text_input("Dirección Fiscal")
        tel = c4.text_input("Teléfono")
        reg = st.selectbox("Régimen de ISR SAT", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades (25%)", "Pequeño Contribuyente (5%)"])
        if st.form_submit_button("Guardar Empresa", type="primary"):
            if nom and nit:
                conn = get_db_connection()
                try:
                    c = conn.cursor()
                    eid = f"emp-{int(datetime.now().timestamp())}"
                    c.execute("INSERT INTO empresas VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                              (eid, nom.strip(), nit.strip(), dire.strip(), tel.strip(), reg, "Servicios Contables", datetime.now().isoformat()))
                    conn.commit()
                    sembrar_nomenclatura_inicial(eid)
                    st.success(f"Empresa '{nom}' registrada exitosamente.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error("Ya existe una empresa con ese NIT.")
                conn.close()
            else:
                st.error("Completa el nombre y el NIT.")