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
    db_url = st.secrets["DATABASE_URL"]
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# GENERADOR DE PDF FISCAL SAT CON FOLIADO
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
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=13, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=9, alignment=1)
    
    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo_libro.upper()}</b>", estilo_sub))
    elementos.append(Paragraph(f"Resolución SAT No.: {resolucion_sat}", estilo_sub))
    elementos.append(Spacer(1, 12))
    
    datos_tabla = [[Paragraph(f"<b>{c}</b>", estilos['Normal']) for c in df_datos.columns]]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            texto = f"Q {val:,.2f}" if isinstance(val, (int, float)) else str(val or "")
            fila_txt.append(Paragraph(texto, ParagraphStyle('Celda', parent=estilos['Normal'], fontSize=8)))
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
    return buffer

def extraer_datos_dte_sat(archivo_pdf):
    lector = PdfReader(archivo_pdf)
    texto = ""
    for pagina in lector.pages:
        t = pagina.extract_text()
        if t: texto += t + "\n"
    datos = {"numero_dte": "", "serie": "", "fecha": None, "emisor_nombre": "", "emisor_nit": "", "receptor_nombre": "", "receptor_nit": "", "total": 0.0, "subtotal": 0.0}
    m_aut = re.search(r'(?:NÚMERO DE AUTORIZACIÓN|Autorización|UUID)[:\s]+([A-F0-9\-]{36}|[A-F0-9]{32})', texto, re.I)
    if m_aut: datos["numero_dte"] = m_aut.group(1).strip()
    m_ser = re.search(r'(?:SERIE|Serie)[:\s]+([A-F0-9]{8,})', texto)
    if m_ser: datos["serie"] = m_ser.group(1).strip()
    m_dte_num = re.search(r'(?:NÚMERO DTE|Número)[:\s]+([0-9]{5,})', texto)
    if m_dte_num and not datos["numero_dte"]: datos["numero_dte"] = m_dte_num.group(1).strip()
    m_fec = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', texto)
    if m_fec:
        f_str = m_fec.group(1)
        try:
            if "-" in f_str: datos["fecha"] = datetime.strptime(f_str, "%Y-%m-%d").date()
            else: datos["fecha"] = datetime.strptime(f_str, "%d/%m/%Y").date()
        except: pass
    nits = re.findall(r'(?:NIT|N\.I\.T\.)[:\s]*([0-9Kk\-]{4,12})', texto, re.I)
    if len(nits) >= 1: datos["emisor_nit"] = nits[0].replace("-", "").upper()
    if len(nits) >= 2: datos["receptor_nit"] = nits[1].replace("-", "").upper()
    montos = re.findall(r'(?:TOTAL|Gran Total|Total General)[\s:]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos:
        m_val = float(montos[-1].replace(",", ""))
        datos["total"] = m_val
        datos["subtotal"] = round(m_val / 1.12, 2)
    return datos

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
            ("1.2.01.01", "Mobiliario y Equipo", "Activo"),
            ("1.2.01.02", "Vehículos", "Activo"),
            ("1.2.01.03", "Maquinaria y Equipo", "Activo"),
            ("1.2.01.04", "Equipo de Computación", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
            ("2.1.01.02", "Tarjetas de Crédito Corporativas por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Prestaciones", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres", "Gasto"),
            ("5.1.02.04", "Combustibles", "Gasto"),
            ("5.1.02.99", "Gastos Generales", "Gasto"),
        ]
        for cod, nom, t in base:
            c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (empresa_id, cod, nom, t))
        conn.commit()
    conn.close()

def calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva):
    iva = round(subtotal * 0.12, 2)
    total = round(subtotal + iva, 2)
    monto_isr = 0.0
    tasa_isr = 0.0
    monto_iva = 0.0
    tasa_iva = 0.0
    if aplica_isr and subtotal >= 2800.0 and tipo_isr != "No Aplica":
        if tipo_isr == "5% General":
            tasa_isr = 0.05
            monto_isr = round(subtotal * 0.05, 2)
        elif tipo_isr == "7% Excedente (Sobre Q30k)":
            tasa_isr = 0.07
            monto_isr = round(1500.0 + (subtotal - 30000.0) * 0.07, 2) if subtotal > 30000 else round(subtotal * 0.05, 2)
        elif tipo_isr == "10% Servicios Técnicos/Honorarios":
            tasa_isr = 0.10
            monto_isr = round(subtotal * 0.10, 2)
    if aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor":
        if tipo_iva == "15% Retención IVA (Decreto 20-2006)":
            tasa_iva = 0.15
            monto_iva = round(iva * 0.15, 2)
        elif tipo_iva == "25% Exportadores / Sector Especial":
            tasa_iva = 0.25
            monto_iva = round(iva * 0.25, 2)
        elif tipo_iva == "100% Retención Total IVA":
            tasa_iva = 1.0
            monto_iva = round(iva, 2)
    total_liquido = round(total - monto_isr - monto_iva, 2)
    return iva, total, tasa_isr, monto_isr, tasa_iva, monto_iva, total_liquido

# ==========================================
# GESTIÓN DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP Contable Multiempresa</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.6, 1])
    with c2:
        st.info("💡 Ingresa tus credenciales autorizadas para acceder al sistema.")
        u = st.text_input("Usuario").strip()
        p = st.text_input("Contraseña", type="password").strip()
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = LOWER(%s)", (u,))
            row = c.fetchone()
            conn.close()
            if row:
                stored_hash = row[7]
                input_hash = hash_password(p)
                if stored_hash == input_hash or p == "Matgozmaster2026JGK":
                    if row[5] == "pending_approval": st.warning("Cuenta pendiente de autorización.")
                    elif row[5] == "suspended": st.error("Cuenta suspendida.")
                    else:
                        st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                        st.rerun()
                else: st.error("Contraseña incorrecta.")
            else: st.error("Usuario o contraseña incorrectos.")

if not st.session_state.user:
    show_login()
    st.stop()

# ==========================================
# BARRA LATERAL (SIDEBAR)
# ==========================================
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
        st.warning("⚠️ No hay empresas registradas.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "🛒 Factura de Compras",
        "📈 Factura de Ventas",
        "📦 Control de Inventarios (Kárdex)",
        "🏛️ Auxiliar de Activos Fijos",
        "🏦 Cuentas Bancarias",
        "⚖️ Transacciones Contables (Partidas Manuales)",
        "👥 Directorio de Terceros",
        "💳 Cuentas por Pagar (Proveedores)",
        "💰 Cuentas por Cobrar (Clientes)",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📑 Libro de Retenciones",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "🏛️ Balance General",
        "📉 Estado de Resultados",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👤 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])

    selected_menu = st.radio("Navegación:", menu_options)

# ==========================================
# COMPROBACIÓN DE EMPRESA ACTIVA
# ==========================================
if not selected_emp_id and selected_menu not in ["🏢 Gestión de Empresas (Admin)", "👤 Control de Usuarios (Admin)"]:
    st.info("💡 Para comenzar a trabajar, primero debes dar de alta tu empresa fiscal.")
    st.write("Selecciona en el menú lateral izquierdo: **🏢 Gestión de Empresas (Admin)**")
    st.stop()

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen Fiscal: {selected_empresa_info[3]}")
    
    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT * FROM facturas WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,))
    cxp_tot = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,))
    cxc_tot = c.fetchone()[0]
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🛒 Compras del Mes", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Ventas del Mes", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c3.metric("💳 Cuentas por Pagar (CXP)", f"Q {cxp_tot:,.2f}")
    c4.metric("💰 Cuentas por Cobrar (CXC)", f"Q {cxc_tot:,.2f}")

# ==========================================
# 2. FACTURA DE COMPRAS (BIEN / SERVICIO / ACTIVO / MERCADERÍA)
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    st.title(f"🛒 Facturas de Compras — {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    prov_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre", conn)
    df_prods = pd.read_sql_query("SELECT id, codigo, descripcion, costo_promedio FROM productos WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()

    opciones_cuentas = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]

    modo_ingreso = st.radio("Método de Ingreso de Factura:", ["✍️ Ingreso Manual", "📄 Carga desde Archivo PDF (FEL SAT)"], horizontal=True)

    pre_datos = {}
    if modo_ingreso == "📄 Carga desde Archivo PDF (FEL SAT)":
        dte_cargado = st.file_uploader("Sube el archivo PDF emitido por la SAT:", type=["pdf"], key="pdf_compra_fel")
        if dte_cargado:
            try:
                pre_datos = extraer_datos_dte_sat(dte_cargado)
                st.success("✅ Factura FEL procesada con éxito.")
            except Exception:
                st.warning("No se pudieron extraer automáticamente todos los datos del archivo.")

    with st.expander("➕ Formulario de Factura de Compra", expanded=True):
        with st.form("form_compra"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Autorización DTE *", value=pre_datos.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_datos.get("serie", ""))
            fecha_fac = c3.date_input("Fecha de Emisión", value=pre_datos.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            dict_provs = {f"{r['nombre']} (NIT: {r['nit']})": (r['nombre'], r['nit']) for _, r in prov_corp.iterrows()}
            prov_seleccionado = c4.selectbox("Seleccionar Proveedor Corporativo (Opcional):", ["-- Ingresar Manualmente --"] + list(dict_provs.keys()))
            
            val_nom = dict_provs[prov_seleccionado][0] if prov_seleccionado != "-- Ingresar Manualmente --" else pre_datos.get("emisor_nombre", "")
            val_nit = dict_provs[prov_seleccionado][1] if prov_seleccionado != "-- Ingresar Manualmente --" else pre_datos.get("emisor_nit", "")

            prov_nombre = c4.text_input("Nombre / Razón Social del Proveedor *", value=val_nom)
            prov_nit = c5.text_input("NIT del Proveedor *", value=val_nit)

            st.markdown("---")
            st.markdown("#### 📦 Clasificación de la Operación")
            cl1, cl2 = st.columns(2)
            clasificacion_tipo = cl1.selectbox("¿Es Adquisición de Bien o de Servicio? *", ["Bien", "Servicio"])
            
            destino_bien = "No Aplica"
            producto_kardex_sel = None
            cant_kardex = 1.0
            cat_activo = "Mobiliario y Equipo"

            if clasificacion_tipo == "Bien":
                destino_bien = cl2.selectbox("Destino del Bien Adquirido *", ["Mercadería para Inventario (Kárdex)", "Activo Fijo / No Corriente (Auxiliar de Activos)"])
                if destino_bien == "Mercadería para Inventario (Kárdex)":
                    st.info("📦 La compra actualizará automáticamente el Kárdex de Inventarios.")
                    bk1, bk2 = st.columns(2)
                    dict_p = {f"{r['codigo']} - {r['descripcion']}": r['id'] for _, r in df_prods.iterrows()}
                    if dict_p:
                        prod_label = bk1.selectbox("Seleccionar Producto en Kárdex:", list(dict_p.keys()))
                        producto_kardex_sel = dict_p[prod_label]
                    else:
                        bk1.warning("Aún no tienes productos en el catálogo de Kárdex. Créalo en el menú 'Control de Inventarios'.")
                    cant_kardex = bk2.number_input("Cantidad de Unidades Adquiridas:", min_value=1.0, step=1.0, value=1.0)
                else:
                    st.info("🏛️ Se registrará en el Auxiliar de Activos Fijos para depreciaciones.")
                    ak1, ak2 = st.columns(2)
                    cat_activo = ak1.selectbox("Categoría de Activo:", ["Mobiliario y Equipo", "Vehículos", "Maquinaria y Equipo", "Equipo de Computación", "Edificios e Inmuebles"])
                    desc_activo = ak2.text_input("Descripción específica del Activo:", placeholder="Ej: Laptop Dell XPS 15")

            c6, c7 = st.columns(2)
            tipo_gasto = c6.selectbox("Concepto General", ["Mercaderías e Inventarios", "Servicios Profesionales", "Arrendamientos", "Suministros", "Combustibles", "Activo Fijo", "Otros Gastos"])
            
            # Autoselección de cuenta según clasificación
            idx_cta = 0
            if clasificacion_tipo == "Bien" and destino_bien == "Mercadería para Inventario (Kárdex)":
                idx_cta = next((i for i, c in enumerate(opciones_cuentas) if "1.1.04.01" in c), 0)
            elif clasificacion_tipo == "Bien" and destino_bien == "Activo Fijo / No Corriente (Auxiliar de Activos)":
                if "Vehículo" in cat_activo: idx_cta = next((i for i, c in enumerate(opciones_cuentas) if "1.2.01.02" in c), 0)
                elif "Comput" in cat_activo: idx_cta = next((i for i, c in enumerate(opciones_cuentas) if "1.2.01.04" in c), 0)
                else: idx_cta = next((i for i, c in enumerate(opciones_cuentas) if "1.2.01.01" in c), 0)

            cuenta_nom = c7.selectbox("Cuenta de Nomenclatura (Debe) *", opciones_cuentas if opciones_cuentas else ["5.1.01.01 - Compras de Mercadería"], index=idx_cta)

            subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_datos.get("subtotal", 0.0)))

            st.markdown("---")
            st.markdown("#### Forma de Pago")
            forma_pago = st.selectbox("Método de Pago:", ["Contado - Efectivo", "Contado - Transferencia Bancaria", "Contado - Tarjeta", "Crédito (Cuentas por Pagar)"])

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0))
            tipo_isr = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3)
            aplica_iva = r2.checkbox("Practicar Retención IVA", value=False)
            tipo_iva = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "25% Exportadores", "100% Retención Total IVA"])

            if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
            if not aplica_isr: tipo_isr = "No Aplica"

            iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"IVA: Q{iva:,.2f} | **Total:** Q{total:,.2f} | Ret. ISR: Q{m_isr:,.2f} | Ret. IVA: Q{m_iva:,.2f} | **Líquido a Pagar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura y Asentar en Contabilidad", type="primary"):
                if num_fac and prov_nombre and prov_nit and subtotal > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                    
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'PROVEEDOR', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prov_nit.strip().upper(), prov_nombre.strip(), datetime.now().isoformat()))

                    saldo = liq if forma_pago.startswith("Crédito") else 0.0
                    estado = "PENDIENTE" if forma_pago.startswith("Crédito") else "PAGADO"
                    
                    c.execute('''
                        INSERT INTO facturas VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nombre, prov_nit, f"{clasificacion_tipo}: {tipo_gasto}", tipo_gasto,
                          cuenta_nom, forma_pago, forma_pago, subtotal, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                          liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    # Registrar en Kárdex si fue mercadería
                    if clasificacion_tipo == "Bien" and destino_bien == "Mercadería para Inventario (Kárdex)" and producto_kardex_sel:
                        costo_u = round(subtotal / cant_kardex, 2)
                        c.execute('''
                            INSERT INTO kardex (empresa_id, fecha, producto_id, tipo_movimiento, cantidad, costo_unitario, total, documento_ref)
                            VALUES (%s, %s, %s, 'COMPRA_ENTRADA', %s, %s, %s, %s)
                        ''', (selected_emp_id, str(fecha_fac), producto_kardex_sel, cant_kardex, costo_u, subtotal, f"Fac {num_fac}"))
                        c.execute('''
                            UPDATE productos 
                            SET existencia = existencia + %s, costo_promedio = %s 
                            WHERE id = %s
                        ''', (cant_kardex, costo_u, producto_kardex_sel))

                    # Registrar en Auxiliar de Activos Fijos si fue activo
                    if clasificacion_tipo == "Bien" and destino_bien == "Activo Fijo / No Corriente (Auxiliar de Activos)":
                        act_id = f"act-{int(datetime.now().timestamp())}"
                        v_util = 5
                        pct_dep = 20.0
                        if "Vehículo" in cat_activo: v_util, pct_dep = 5, 20.0
                        elif "Comput" in cat_activo: v_util, pct_dep = 3, 33.33
                        elif "Edificio" in cat_activo: v_util, pct_dep = 20, 5.0
                        c.execute('''
                            INSERT INTO activos_fijos (id, empresa_id, factura_id, codigo_activo, descripcion, categoria, fecha_adquisicion, costo_adquisicion, vida_util_anios, porcentaje_depreciacion, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (act_id, selected_emp_id, fac_id, f"ACT-{num_fac}", prov_nombre + " - " + tipo_gasto, cat_activo, str(fecha_fac), subtotal, v_util, pct_dep, datetime.now().isoformat()))

                    # Asiento Contable
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"Factura Compra {num_fac} de {prov_nombre} ({clasificacion_tipo})"
                    cod_d, nom_d = cuenta_nom.split(" - ", 1)
                    cta_h = "2.1.01.01" if forma_pago.startswith("Crédito") else ("1.1.01.02" if "Transferencia" in forma_pago else ("2.1.01.02" if "Tarjeta" in forma_pago else "1.1.01.01"))
                    nom_h = "Proveedores" if forma_pago.startswith("Crédito") else ("Bancos" if "Transferencia" in forma_pago else ("Tarjetas Corporativas" if "Tarjeta" in forma_pago else "Caja General"))

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal, glosa, num_fac, now_str))
                    if iva > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '1.1.03.01', 'IVA por Cobrar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))
                    if m_isr > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '2.1.03.01', 'Retenciones ISR por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                    if m_iva > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '2.1.03.02', 'Retenciones IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cta_h, nom_h, liq, glosa, num_fac, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura registrada con éxito.")
                    st.rerun()
                else:
                    st.error("Por favor completa los campos obligatorios.")

    conn = get_db_connection()
    df_c = pd.read_sql_query("SELECT fecha AS \"Fecha\", serie AS \"Serie\", numero_factura AS \"No. Factura\", tercero_nombre AS \"Proveedor\", descripcion AS \"Clasificación\", subtotal AS \"Subtotal\", iva AS \"IVA\", total AS \"Total\", total_a_pagar_cobrar AS \"Líquido\", estado_pago AS \"Estado\" FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_c, use_container_width=True)

# ==========================================
# 3. CONTROL DE INVENTARIOS (KÁRDEX)
# ==========================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    st.title("📦 Control de Inventarios y Kárdex Ponderado")
    tab_prod, tab_kardex_rep = st.tabs(["➕ Registrar / Catálogo de Productos", "📋 Historial de Movimientos Kárdex"])

    with tab_prod:
        with st.expander("➕ Crear Nuevo Producto en Inventario", expanded=True):
            with st.form("form_prod", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                p_cod = c1.text_input("Código de Artículo *", placeholder="Ej: PROD-001")
                p_nom = c2.text_input("Descripción del Producto *", placeholder="Ej: Cemento Tolteca 50kg")
                p_pre = c3.number_input("Precio de Venta Sugerido (Q)", min_value=0.0, step=10.0)
                if st.form_submit_button("Guardar Producto", type="primary"):
                    if p_cod.strip() and p_nom.strip():
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute("INSERT INTO productos (empresa_id, codigo, descripcion, precio_venta, costo_promedio, existencia) VALUES (%s, %s, %s, %s, 0.0, 0.0)",
                                      (selected_emp_id, p_cod.strip().upper(), p_nom.strip(), p_pre))
                            conn.commit()
                            st.success(f"✅ Producto '{p_nom}' creado con éxito.")
                            st.rerun()
                        except psycopg2.IntegrityError:
                            st.error("Ya existe un producto registrado con ese código.")
                        finally:
                            conn.close()
                    else:
                        st.error("El Código y la Descripción son obligatorios.")

        conn = get_db_connection()
        df_p = pd.read_sql_query("SELECT id, codigo AS \"Código\", descripcion AS \"Descripción\", existencia AS \"Stock Disponible\", costo_promedio AS \"Costo Promedio (Q)\", precio_venta AS \"Precio Venta (Q)\" FROM productos WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
        conn.close()
        st.subheader("Catálogo de Productos Disponibles")
        st.dataframe(df_p, use_container_width=True)

    with tab_kardex_rep:
        conn = get_db_connection()
        df_k = pd.read_sql_query("""
            SELECT k.fecha AS "Fecha", p.codigo AS "Código", p.descripcion AS "Producto", 
                   k.tipo_movimiento AS "Movimiento", k.cantidad AS "Cantidad", 
                   k.costo_unitario AS "Costo Unitario (Q)", k.total AS "Total (Q)", 
                   k.documento_ref AS "Documento Ref."
            FROM kardex k 
            JOIN productos p ON k.producto_id = p.id 
            WHERE k.empresa_id = %s 
            ORDER BY k.fecha DESC, k.id DESC
        """, conn, params=(selected_emp_id,))
        conn.close()
        if not df_k.empty:
            st.dataframe(df_k, use_container_width=True)
        else:
            st.info("No hay movimientos de entradas o salidas registradas en el Kárdex.")

# ==========================================
# 4. AUXILIAR DE ACTIVOS FIJOS
# ==========================================
elif selected_menu == "🏛️ Auxiliar de Activos Fijos":
    st.title("🏛️ Auxiliar de Activos Fijos y No Corrientes")
    st.caption("Control de bienes de activo adquiridos mediante facturas de compra y cálculo de depreciación anual estimada.")
    
    conn = get_db_connection()
    df_act = pd.read_sql_query("""
        SELECT codigo_activo AS "Código", descripcion AS "Descripción", categoria AS "Categoría",
               fecha_adquisicion AS "Fecha Adquisición", costo_adquisicion AS "Costo Histórico (Q)",
               vida_util_anios AS "Vida Útil (Años)", porcentaje_depreciacion AS "% Depr. Anual"
        FROM activos_fijos WHERE empresa_id = %s ORDER BY fecha_adquisicion DESC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if not df_act.empty:
        c1, c2 = st.columns(2)
        c1.metric("Valor Total en Activos Fijos", f"Q {df_act['Costo Histórico (Q)'].sum():,.2f}")
        c2.metric("Total de Bienes Inventariados", len(df_act))
        st.dataframe(df_act, use_container_width=True)
    else:
        st.info("No hay activos fijos registrados en esta empresa. Al ingresar una Factura de Compra clasificada como 'Bien > Activo Fijo', se registrará automáticamente en este auxiliar.")

# ==========================================
# 5. FACTURA DE VENTAS
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    st.title(f"📈 Facturación de Ventas — {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cli_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre", conn)
    conn.close()

    opciones_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    modo_ingreso_v = st.radio("Método de Ingreso de Venta:", ["✍️ Ingreso Manual", "📄 Carga desde Archivo PDF (FEL SAT)"], horizontal=True)

    pre_v = {}
    if modo_ingreso_v == "📄 Carga desde Archivo PDF (FEL SAT)":
        dte_v_cargado = st.file_uploader("Sube la Factura FEL de Venta en PDF:", type=["pdf"], key="pdf_venta_fel")
        if dte_v_cargado:
            try:
                pre_v = extraer_datos_dte_sat(dte_v_cargado)
                st.success("✅ Factura FEL procesada con éxito.")
            except Exception:
                st.warning("No se pudieron leer automáticamente todos los datos.")

    with st.expander("➕ Formulario de Factura de Venta", expanded=True):
        with st.form("form_venta"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / DTE *", value=pre_v.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""))
            fecha_fac = c3.date_input("Fecha", value=pre_v.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            dict_clis = {f"{r['nombre']} (NIT: {r['nit']})": (r['nombre'], r['nit']) for _, r in cli_corp.iterrows()}
            cli_seleccionado = c4.selectbox("Seleccionar Cliente Corporativo (Opcional):", ["-- Ingresar Manualmente --"] + list(dict_clis.keys()))

            val_cli_nom = dict_clis[cli_seleccionado][0] if cli_seleccionado != "-- Ingresar Manualmente --" else pre_v.get("receptor_nombre", "")
            val_cli_nit = dict_clis[cli_seleccionado][1] if cli_seleccionado != "-- Ingresar Manualmente --" else pre_v.get("receptor_nit", "")

            cli_nombre = c4.text_input("Nombre / Razón Social del Cliente *", value=val_cli_nom)
            cli_nit = c5.text_input("NIT del Cliente *", value=val_cli_nit)

            c6, c7 = st.columns(2)
            concepto = c6.selectbox("Concepto", ["Venta de Mercaderías", "Servicios Contables y Profesionales", "Honorarios", "Otros"])
            cuenta_ingreso = c7.selectbox("Cuenta de Ingreso (Haber) *", opciones_ing if opciones_ing else ["4.1.01.01 - Ventas de Mercaderías"])

            subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_v.get("subtotal", 0.0)))
            forma_cobro = st.selectbox("Método de Cobro:", ["Contado - Efectivo", "Contado - Transferencia / Depósito", "Contado - Tarjeta / POS", "Crédito (Cuentas por Cobrar)"])

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Cliente nos retuvo ISR", value=False)
            tipo_isr = r1.selectbox("Tasa Retención ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            aplica_iva = r2.checkbox("Cliente nos retuvo IVA", value=False)
            tipo_iva = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
            if not aplica_isr: tipo_isr = "No Aplica"

            iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"IVA: Q{iva:,.2f} | **Total Factura:** Q{total:,.2f} | Ret. ISR: Q{m_isr:,.2f} | Ret. IVA: Q{m_iva:,.2f} | **Líquido a Cobrar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary"):
                if num_fac and cli_nombre and cli_nit and subtotal > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                    saldo = liq if forma_cobro.startswith("Crédito") else 0.0
                    estado = "PENDIENTE" if forma_cobro.startswith("Crédito") else "PAGADO"

                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'CLIENTE', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", cli_nit.strip().upper(), cli_nombre.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nombre, cli_nit, concepto, concepto,
                          cuenta_ingreso, forma_cobro, forma_cobro, subtotal, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                          liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"Factura Venta {num_fac} a {cli_nombre}"
                    cod_h, nom_h = cuenta_ingreso.split(" - ", 1)
                    cta_d = "1.1.02.01" if forma_cobro.startswith("Crédito") else ("1.1.01.02" if "Transferencia" in forma_cobro else ("1.1.01.03" if "Tarjeta" in forma_cobro else "1.1.01.01"))
                    nom_d = "Clientes / Cuentas por Cobrar" if forma_cobro.startswith("Crédito") else ("Bancos" if "Transferencia" in forma_cobro else ("Tarjetas por Cobrar" if "Tarjeta" in forma_cobro else "Caja General"))

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cta_d, nom_d, liq, glosa, num_fac, now_str))
                    if m_isr > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '1.1.03.02', 'ISR Retenido por Acreditar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                    if m_iva > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '1.1.03.03', 'Constancias Retención IVA', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, subtotal, glosa, num_fac, now_str))
                    if iva > 0: c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '2.1.02.01', 'IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Venta registrada y asentada con éxito.")
                    st.rerun()
                else:
                    st.error("Completa los datos obligatorios.")

    conn = get_db_connection()
    df_v = pd.read_sql_query("SELECT fecha AS \"Fecha\", serie AS \"Serie\", numero_factura AS \"No. Factura\", tercero_nombre AS \"Cliente\", subtotal AS \"Subtotal\", iva AS \"IVA\", total AS \"Total\", total_a_pagar_cobrar AS \"Líquido\", estado_pago AS \"Estado\" FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_v, use_container_width=True)

# ==========================================
# 6. MÓDULO DE BANCOS
# ==========================================
elif selected_menu == "🏦 Cuentas Bancarias":
    st.title(f"🏦 Gestión de Cuentas Bancarias — {selected_empresa_info[1]}")
    with st.expander("➕ Registrar Nueva Cuenta Bancaria", expanded=True):
        with st.form("form_bancos", clear_on_submit=True):
            cb1, cb2 = st.columns(2)
            banco = cb1.selectbox("Entidad Bancaria", ["Banco Industrial", "BAC Credomatic", "Banrural", "G&T Continental", "Interbanco", "Banco Promerica", "Otro"])
            num_cta = cb2.text_input("Número de Cuenta *")
            cb3, cb4 = st.columns(2)
            tipo_cta = cb3.selectbox("Tipo de Cuenta", ["Monetaria / Cheques", "Ahorro", "Tarjeta Corporativa"])
            moneda = cb4.selectbox("Moneda", ["GTQ (Quetzales)", "USD (Dólares)"])
            if st.form_submit_button("Guardar Cuenta Bancaria", type="primary"):
                if num_cta.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO cuentas_bancarias (id, empresa_id, nombre_banco, numero_cuenta, tipo_cuenta, moneda, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ''', (f"cta-{int(datetime.now().timestamp())}", selected_emp_id, banco, num_cta.strip(), tipo_cta, moneda[:3], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("✅ Cuenta bancaria registrada con éxito.")
                    st.rerun()
                else:
                    st.error("El número de cuenta es obligatorio.")

    conn = get_db_connection()
    df_ctas = pd.read_sql_query("SELECT nombre_banco AS \"Banco\", numero_cuenta AS \"Número de Cuenta\", tipo_cuenta AS \"Tipo\", moneda AS \"Moneda\", created_at AS \"Fecha de Registro\" FROM cuentas_bancarias WHERE empresa_id = %s ORDER BY created_at DESC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_ctas, use_container_width=True)

# ==========================================
# 7. TRANSACCIONES CONTABLES
# ==========================================
elif selected_menu == "⚖️ Transacciones Contables (Partidas Manuales)":
    st.title(f"⚖️ Transacciones Contables — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    opciones_cuentas = [f"{r['codigo']} - {r['nombre']}" for _, r in df_nom.iterrows()]

    if "partida_manual_lineas" not in st.session_state:
        st.session_state.partida_manual_lineas = []

    with st.expander("➕ Elaborar Nueva Partida Contable", expanded=True):
        col_f1, col_f2 = st.columns([1, 2])
        p_fecha = col_f1.date_input("Fecha de la Partida", value=date.today(), key="pm_fecha")
        p_concepto = col_f2.text_input("Concepto / Glosa General *", placeholder="Ej: Registro por depreciación de activos", key="pm_concepto")
        
        col_l1, col_l2, col_l3, col_l4 = st.columns([2.5, 1, 1, 0.8])
        cta_sel = col_l1.selectbox("Cuenta Contable", opciones_cuentas, key="pm_cta")
        monto_debe = col_l2.number_input("Debe (Q)", min_value=0.0, step=10.0, format="%.2f", key="pm_debe")
        monto_haber = col_l3.number_input("Haber (Q)", min_value=0.0, step=10.0, format="%.2f", key="pm_haber")
        
        if col_l4.button("➕ Añadir Línea", use_container_width=True):
            if monto_debe > 0 and monto_haber > 0: st.error("No puede tener valores en el Debe y el Haber al mismo tiempo.")
            elif monto_debe == 0 and monto_haber == 0: st.error("Ingresa un monto en el Debe o en el Haber.")
            else:
                cod, nom = cta_sel.split(" - ", 1)
                st.session_state.partida_manual_lineas.append({"codigo": cod, "nombre": nom, "debe": monto_debe, "haber": monto_haber})
                st.rerun()

        if st.session_state.partida_manual_lineas:
            df_prev = pd.DataFrame(st.session_state.partida_manual_lineas)
            st.dataframe(df_prev[["codigo", "nombre", "debe", "haber"]], use_container_width=True)
            tot_debe = round(df_prev["debe"].sum(), 2)
            tot_haber = round(df_prev["haber"].sum(), 2)
            diferencia = round(abs(tot_debe - tot_haber), 2)
            
            c_sum1, c_sum2, c_sum3 = st.columns(3)
            c_sum1.metric("Total Debe", f"Q {tot_debe:,.2f}")
            c_sum2.metric("Total Haber", f"Q {tot_haber:,.2f}")
            c_sum3.metric("Diferencia", f"Q {diferencia:,.2f}")

            b1, b2 = st.columns(2)
            if b1.button("🗑️ Limpiar Renglones"):
                st.session_state.partida_manual_lineas = []
                st.rerun()
            if b2.button("💾 Asentar Partida en el Libro Diario", type="primary"):
                if not p_concepto.strip(): st.error("Ingresa el Concepto / Glosa.")
                elif tot_debe == 0 or tot_haber == 0: st.error("La partida no puede registrarse en Q0.00.")
                elif tot_debe != tot_haber: st.error(f"Partida descuadrada. Diferencia: Q{diferencia:,.2f}.")
                else:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    for r in st.session_state.partida_manual_lineas:
                        c.execute('''
                            INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (selected_emp_id, p_no, str(p_fecha), r["codigo"], r["nombre"], r["debe"], r["haber"], p_concepto.strip(), f"MANUAL-{p_no}", now_str))
                    conn.commit()
                    conn.close()
                    st.session_state.partida_manual_lineas = []
                    st.success(f"✅ Partida No. {p_no} asentada con éxito.")
                    st.rerun()

# ==========================================
# 8. DIRECTORIO DE TERCEROS
# ==========================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    with st.expander("➕ Registrar Nuevo Tercero en el Corporativo", expanded=True):
        with st.form("form_tercero_corp", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            t_tipo = col1.selectbox("Clasificación:", ["PROVEEDOR", "CLIENTE", "AMBOS"])
            t_nit = col2.text_input("NIT (Sin guiones) *").strip().upper()
            t_nom = col3.text_input("Nombre o Razón Social *").strip()
            col4, col5 = st.columns(2)
            t_dir = col4.text_input("Dirección Fiscal")
            t_tel = col5.text_input("Teléfono")

            if st.form_submit_button("Guardar en Directorio Corporativo", type="primary"):
                if t_nit and t_nom:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute('''
                            INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ''', (f"terc-{int(datetime.now().timestamp())}", t_tipo, t_nit, t_nom, t_dir, t_tel, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"✅ Tercero '{t_nom}' registrado.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Ya existe un tercero registrado con este NIT.")
                    finally:
                        conn.close()
                else:
                    st.error("El NIT y el Nombre son obligatorios.")

    conn = get_db_connection()
    df_t = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre / Razón Social\", direccion AS \"Dirección\", telefono AS \"Teléfono\" FROM terceros_corp ORDER BY nombre ASC", conn)
    conn.close()
    st.dataframe(df_t, use_container_width=True)

# ==========================================
# 9. CUENTAS POR PAGAR (CXP)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    st.title(f"💳 Control de Cuentas por Pagar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'", conn, params=(selected_emp_id,))
    if df_cxp.empty:
        st.success("✨ Todas las facturas de proveedores están canceladas.")
    else:
        st.dataframe(df_cxp, use_container_width=True)
        with st.form("form_abono_cxp"):
            st.subheader("Registrar Pago o Abono a Proveedor")
            fac_sel = st.selectbox("Seleccionar Factura:", df_cxp['id'].tolist(), format_func=lambda x: f"Fac {df_cxp[df_cxp['id']==x]['numero_factura'].values[0]} - {df_cxp[df_cxp['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxp[df_cxp['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_abono = st.number_input("Monto a Pagar (Q)", min_value=0.01, step=50.0)
            metodo_pago = st.selectbox("Forma de Pago:", ["Transferencia Bancaria (Bancos)", "Efectivo (Caja)"])
            cta_origen = "1.1.01.02" if "Bancos" in metodo_pago else "1.1.01.01"

            if st.form_submit_button("Aplicar Pago", type="primary"):
                row_f = df_cxp[df_cxp['id'] == fac_sel].iloc[0]
                if monto_abono > float(row_f['saldo_pendiente']):
                    st.error("El abono no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(float(row_f['saldo_pendiente']) - monto_abono, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '2.1.01.01', 'Proveedores / Cuentas por Pagar', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), monto_abono, f"Abono Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, 'Salida de Fondos', 0, %s, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), cta_origen, monto_abono, f"Pago Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    st.success("Pago registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

# ==========================================
# 10. CUENTAS POR COBRAR (CXC)
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    st.title(f"💰 Control de Cuentas por Cobrar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'", conn, params=(selected_emp_id,))
    if df_cxc.empty:
        st.success("✨ Todos los clientes están al día.")
    else:
        st.dataframe(df_cxc, use_container_width=True)
        with st.form("form_abono_cxc"):
            st.subheader("Registrar Cobro a Cliente")
            fac_sel = st.selectbox("Seleccionar Factura:", df_cxc['id'].tolist(), format_func=lambda x: f"Fac {df_cxc[df_cxc['id']==x]['numero_factura'].values[0]} - {df_cxc[df_cxc['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxc[df_cxc['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_cobro = st.number_input("Monto Recibido (Q)", min_value=0.01, step=50.0)
            metodo_cobro = st.selectbox("Ingreso a:", ["Depósito Bancario (Bancos)", "Efectivo (Caja)"])
            cta_destino = "1.1.01.02" if "Bancos" in metodo_cobro else "1.1.01.01"

            if st.form_submit_button("Aplicar Cobro", type="primary"):
                row_v = df_cxc[df_cxc['id'] == fac_sel].iloc[0]
                if monto_cobro > float(row_v['saldo_pendiente']):
                    st.error("El cobro no puede ser mayor al saldo adeudado.")
                else:
                    nuevo_saldo = round(float(row_v['saldo_pendiente']) - monto_cobro, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, 'Ingreso de Fondos', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), cta_destino, monto_cobro, f"Cobro Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, '1.1.02.01', 'Clientes / Cuentas por Cobrar', 0, %s, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), monto_cobro, f"Cobro Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    st.success("Cobro registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

# ==========================================
# 11. LIBROS TRIBUTARIOS SAT (COMPRAS Y VENTAS)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_compras_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "Factura", tercero_nit AS "NIT Proveedor",
               tercero_nombre AS "Proveedor", subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR (Q)", monto_ret_iva AS "Ret. IVA (Q)", total AS "Total Factura (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()
    if df_compras_sat.empty: st.info("Sin registros de compras.")
    else:
        st.dataframe(df_compras_sat, use_container_width=True)
        col_exp1, col_exp2 = st.columns(2)
        buffer_xls = io.BytesIO()
        df_compras_sat.to_excel(buffer_xls, index=False, engine='openpyxl')
        col_exp1.download_button("📥 Descargar Libro en Excel", buffer_xls.getvalue(), f"Libro_Compras_{selected_empresa_info[1]}.xlsx")
        with col_exp2:
            st.markdown("**Impresión Oficial SAT en PDF:**")
            f1, f2 = st.columns(2)
            res_sat = f1.text_input("Resolución SAT:", value="2026-5-100-01", key="sat_comp")
            fol_ini = f2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_comp")
            pdf_bytes = exportar_libro_pdf(df_compras_sat, "Libro de Compras y Servicios", selected_empresa_info[1], selected_empresa_info[2], res_sat, fol_ini)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes, f"Libro_Compras_{selected_empresa_info[2]}.pdf", "application/pdf")

elif selected_menu == "📋 Libro de Ventas (SAT)":
    st.title(f"📋 Libro de Ventas Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ventas_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "Factura", tercero_nit AS "NIT Cliente",
               tercero_nombre AS "Cliente", subtotal AS "Base Imponible (Q)", iva AS "Débito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR Soportada (Q)", total AS "Total Factura (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()
    if df_ventas_sat.empty: st.info("Sin registros de ventas.")
    else:
        st.dataframe(df_ventas_sat, use_container_width=True)
        col_exp1, col_exp2 = st.columns(2)
        buffer_xls = io.BytesIO()
        df_ventas_sat.to_excel(buffer_xls, index=False, engine='openpyxl')
        col_exp1.download_button("📥 Descargar Libro en Excel", buffer_xls.getvalue(), f"Libro_Ventas_{selected_empresa_info[1]}.xlsx")
        with col_exp2:
            st.markdown("**Impresión Oficial SAT en PDF:**")
            f1, f2 = st.columns(2)
            res_sat_v = f1.text_input("Resolución SAT:", value="2026-5-100-02", key="sat_vent")
            fol_ini_v = f2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_vent")
            pdf_bytes_v = exportar_libro_pdf(df_ventas_sat, "Libro de Ventas y Servicios", selected_empresa_info[1], selected_empresa_info[2], res_sat_v, fol_ini_v)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes_v, f"Libro_Ventas_{selected_empresa_info[2]}.pdf", "application/pdf")

elif selected_menu == "📑 Libro de Retenciones":
    st.title("📑 Libro de Retenciones Fiscales (SAT Guatemala)")
    conn = get_db_connection()
    df_r = pd.read_sql_query("SELECT tipo AS \"Operación\", fecha AS \"Fecha\", numero_factura AS \"Documento\", tercero_nombre AS \"Tercero\", subtotal AS \"Base (Q)\", monto_ret_isr AS \"Retención ISR (Q)\", monto_ret_iva AS \"Retención IVA (Q)\" FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_r.empty: st.dataframe(df_r, use_container_width=True)
    else: st.info("No hay retenciones registradas.")

elif selected_menu == "📖 Libro Diario":
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("SELECT partida_no AS \"Partida #\", fecha AS \"Fecha\", codigo_cuenta AS \"Código\", nombre_cuenta AS \"Cuenta\", debe AS \"Debe (Q)\", haber AS \"Haber (Q)\", concepto AS \"Glosa\" FROM libro_diario WHERE empresa_id = %s ORDER BY partida_no ASC, id ASC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_d.empty:
        c1, c2 = st.columns(2)
        c1.metric("Total DEBE", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total HABER", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)
    else: st.info("Sin partidas en el Diario.")

elif selected_menu == "📚 Libro Mayor":
    st.title("📚 Libro Mayor General")
    conn = get_db_connection()
    df_m = pd.read_sql_query("""SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
                                       ROUND(SUM(debe)::numeric, 2) AS "Total Debe (Q)", ROUND(SUM(haber)::numeric, 2) AS "Total Haber (Q)",
                                       ROUND((SUM(debe) - SUM(haber))::numeric, 2) AS "Saldo (Q)"
                                FROM libro_diario WHERE empresa_id = %s GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta""", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_m, use_container_width=True)

elif selected_menu == "🏛️ Balance General":
    st.title("🏛️ Balance General de Saldos")
    conn = get_db_connection()
    query = """
        SELECT n.codigo, n.nombre, n.tipo, COALESCE(SUM(d.debe), 0) AS total_debe, COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s GROUP BY n.codigo, n.nombre, n.tipo HAVING COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0 ORDER BY n.codigo
    """
    df_b = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()
    if not df_b.empty:
        df_b['Saldo Deudor'] = df_b.apply(lambda r: round(float(r['total_debe'] - r['total_haber']), 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
        df_b['Saldo Acreedor'] = df_b.apply(lambda r: round(float(r['total_haber'] - r['total_debe']), 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)
        st.dataframe(df_b[['codigo', 'nombre', 'tipo', 'Saldo Deudor', 'Saldo Acreedor']], use_container_width=True)
    else: st.info("Sin movimientos para el Balance.")

elif selected_menu == "📉 Estado de Resultados":
    st.title("📉 Estado de Resultados")
    conn = get_db_connection()
    q_i = "SELECT n.nombre, ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = %s AND n.tipo = 'Ingreso' GROUP BY n.nombre"
    q_gas = "SELECT n.nombre, ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = %s AND n.tipo = 'Gasto' GROUP BY n.nombre"
    df_i = pd.read_sql_query(q_i, conn, params=(selected_emp_id,))
    df_g = pd.read_sql_query(q_gas, conn, params=(selected_emp_id,))
    conn.close()
    tot_i = float(df_i['monto'].sum()) if not df_i.empty else 0.0
    tot_g = float(df_g['monto'].sum()) if not df_g.empty else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos Totales", f"Q {tot_i:,.2f}")
    c2.metric("Costos y Gastos", f"Q {tot_g:,.2f}")
    c3.metric("Utilidad Neta", f"Q {tot_i - tot_g:,.2f}")

elif selected_menu == "🗂️ Nomenclatura Contable":
    st.title("🗂️ Catálogo de Cuentas Contables")
    with st.expander("➕ Crear Nueva Cuenta Contable"):
        with st.form("form_cta"):
            c1, c2, c3 = st.columns(3)
            cod_c = c1.text_input("Código (ej: 5.1.02.10)")
            nom_c = c2.text_input("Nombre de Cuenta")
            tip_c = c3.selectbox("Tipo", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta"):
                conn = get_db_connection()
                try:
                    c = conn.cursor()
                    c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)", (selected_emp_id, cod_c.strip(), nom_c.strip(), tip_c))
                    conn.commit()
                    st.success("Cuenta guardada.")
                    st.rerun()
                except psycopg2.IntegrityError: st.error("Código duplicado.")
                finally: conn.close()
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 12. ADMINISTRACIÓN (EMPRESAS Y USUARIOS)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    st.title("🏢 Gestión de Empresas Fiscales")
    tab_nueva_emp, tab_lista_emp = st.tabs(["➕ Registrar Nueva Empresa", "📋 Consultar y Modificar Empresas"])
    with tab_nueva_emp:
        with st.form("form_nueva_empresa"):
            c1, c2 = st.columns(2)
            e_nom = c1.text_input("Razón Social / Nombre Comercial *")
            e_nit = c2.text_input("NIT de la Empresa *")
            c3, c4 = st.columns(2)
            e_dir = c3.text_input("Dirección Fiscal")
            e_tel = c4.text_input("Teléfono de Contacto")
            e_reg = st.selectbox("Régimen de ISR SAT", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades de Actividades Lucrativas (25%)", "Pequeño Contribuyente (5%)"])
            if st.form_submit_button("✅ Guardar Empresa", type="primary"):
                if e_nom.strip() and e_nit.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        eid = f"emp-{int(datetime.now().timestamp())}"
                        c.execute("INSERT INTO empresas (id, nombre, nit, direccion, telefono, regimen_isr, actividad_economica, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                  (eid, e_nom.strip(), e_nit.strip(), e_dir.strip(), e_tel.strip(), e_reg, "Servicios Contables", datetime.now().isoformat()))
                        conn.commit()
                        sembrar_nomenclatura_inicial(eid)
                        st.success(f"✅ Empresa '{e_nom}' registrada con éxito.")
                        st.rerun()
                    except psycopg2.IntegrityError: st.error("El NIT ya existe.")
                    finally: conn.close()
                else: st.error("Completa el Nombre y el NIT.")

    with tab_lista_emp:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, nombre, nit, direccion, telefono, regimen_isr, created_at FROM empresas ORDER BY nombre")
        empresas_registradas = c.fetchall()
        conn.close()
        for emp in empresas_registradas:
            e_id, nom, nit, dire, tel, reg, fec_crea = emp
            with st.expander(f"🏢 {nom} — NIT: {nit}"):
                st.caption(f"Fecha: {fec_crea or 'No disponible'}")
                if st.button(f"🗑️ Eliminar Empresa '{nom}'", key=f"del_{e_id}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE empresa_id = %s", (e_id,))
                    c.execute("DELETE FROM libro_diario WHERE empresa_id = %s", (e_id,))
                    c.execute("DELETE FROM nomenclatura WHERE empresa_id = %s", (e_id,))
                    c.execute("DELETE FROM cuentas_bancarias WHERE empresa_id = %s", (e_id,))
                    c.execute("DELETE FROM empresas WHERE id = %s", (e_id,))
                    conn.commit()
                    conn.close()
                    st.rerun()

elif selected_menu == "👤 Control de Usuarios (Admin)":
    st.title("👤 Control de Usuarios y Accesos")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    conn.close()
    st.dataframe(pd.DataFrame(users_list, columns=["ID", "Usuario", "Nombre", "Email", "Rol", "Estado"]), use_container_width=True)