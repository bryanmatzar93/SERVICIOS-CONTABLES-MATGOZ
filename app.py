import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import io
import re
from datetime import datetime, date

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ==========================================
# CONFIGURACIÓN GENERAL STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Servicios Contables Matgoz - ERP Corporativo",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main-title { color: #1E3A8A; font-weight: 700; margin-bottom: 0px; }
    .stMetric { background-color: #F8FAFC; padding: 12px; border-radius: 10px; border: 1px solid #E2E8F0; }
    div[data-testid="stExpander"] { border: 1px solid #E2E8F0; border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# CONEXIÓN A SUPABASE (POSTGRESQL)
# ==========================================
def get_db_connection():
    db_url = st.secrets.get("DATABASE_URL")
    if not db_url:
        st.error("No se encontró la variable DATABASE_URL en los Secrets de Streamlit.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = %s", (str(empresa_id),))
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
            ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
            ("2.1.01.02", "Tarjetas de Crédito Corporativas por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("2.1.03.99", "Provisión ISR por Pagar", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.1.01.03", "Servicios de Transporte y Fletes", "Ingreso"),
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Prestaciones", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres", "Gasto"),
            ("5.1.02.04", "Combustibles", "Gasto"),
            ("5.1.02.99", "Gastos Generales", "Gasto")
        ]
        for cod, nom, t in base:
            c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                      (str(empresa_id), cod, nom, t))
        conn.commit()
    conn.close()

def calcular_impuesto_renta(regimen_empresa, total_ingresos, utilidad_antes_impuestos):
    impuesto = 0.0
    tasa_desc = "0%"
    if "Pequeño" in str(regimen_empresa):
        impuesto = round(total_ingresos * 0.05, 2)
        tasa_desc = "5% s/ Ingresos Brutos (Pequeño Contribuyente)"
    elif "Opcional" in str(regimen_empresa) or "Simplificado" in str(regimen_empresa):
        if total_ingresos <= 30000.0:
            impuesto = round(total_ingresos * 0.05, 2)
            tasa_desc = "5% Régimen Opcional Simplificado"
        else:
            impuesto = round(1500.0 + (total_ingresos - 30000.0) * 0.07, 2)
            tasa_desc = "5% hasta Q30k y 7% s/ Excedente"
    elif "Utilidades" in str(regimen_empresa) or "General" in str(regimen_empresa):
        if utilidad_antes_impuestos > 0:
            impuesto = round(utilidad_antes_impuestos * 0.25, 2)
            tasa_desc = "25% s/ Utilidad Neta (Actividades Lucrativas)"
        else:
            impuesto = 0.0
            tasa_desc = "25% (Pérdida - Q0.00)"
    ganancia_neta = round(utilidad_antes_impuestos - impuesto, 2)
    return impuesto, ganancia_neta, tasa_desc

# ==========================================
# EXTRACTOR DE DATOS FEL SAT GUATEMALA
# ==========================================
def extraer_datos_dte_sat(archivo_pdf):
    reader = PdfReader(archivo_pdf)
    texto = "\n".join([page.extract_text() or "" for page in reader.pages])
    datos = {}

    # 1. Número de Autorización / DTE
    m_aut = re.search(r"N[ÚU]MERO\s+DE\s+AUTORIZACI[ÓO]N:\s*\n*([A-F0-9\-]{30,40})", texto, re.IGNORECASE)
    if m_aut:
        datos["numero_dte"] = m_aut.group(1).strip()
    else:
        m_dte = re.search(r"N[úu]mero\s+de\s+DTE:\s*([0-9]+)", texto)
        datos["numero_dte"] = m_dte.group(1).strip() if m_dte else ""

    # 2. Serie
    m_ser = re.search(r"Serie:\s*([A-F0-9]+)", texto, re.IGNORECASE)
    datos["serie"] = m_ser.group(1).strip() if m_ser else ""

    # 3. Fecha de Emisión
    m_fec = re.search(r"Fecha(?:\s+y\s+hora)?\s+de\s+emisi[óo]n:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})", texto, re.IGNORECASE)
    if m_fec:
        raw_f = m_fec.group(1).strip()
        try:
            if "-" in raw_f:
                datos["fecha"] = datetime.strptime(raw_f, "%Y-%m-%d").date()
            else:
                datos["fecha"] = datetime.strptime(raw_f, "%d/%m/%Y").date()
        except Exception:
            datos["fecha"] = date.today()
    else:
        datos["fecha"] = date.today()

    # 4. NIT Emisor (Proveedor en Compras)
    m_nit = re.search(r"Nit\s+Emisor:\s*([0-9A-Z]+)", texto, re.IGNORECASE)
    datos["emisor_nit"] = m_nit.group(1).strip() if m_nit else ""

    # 5. Nombre Proveedor (Corta antes de firmas, certificadores o leyendas SAT)
    m_nom = re.search(r"Nombre\s+Emisor:\s*([^\n\r]+)", texto, re.IGNORECASE)
    if not m_nom:
        m_nom = re.search(r"(?:EMISOR|DATOS DEL EMISOR)[\s:]*\n*([^\n\r]+)", texto, re.IGNORECASE)
    
    if m_nom:
        nom_limpio = m_nom.group(1).strip()
        # Filtro estricto: cortar si se cuela la firma o certificador
        for corte in ["FIRMA", "CERTIFICADOR", "DOCUMENTO TRIBUTARIO", "AUTORIZACIÓN", "NIT:"]:
            if corte in nom_limpio.upper():
                nom_limpio = nom_limpio.upper().split(corte)[0].strip()
        datos["emisor_nombre"] = nom_limpio
    else:
        datos["emisor_nombre"] = ""

    # 6. Total y Subtotal
    m_tot = re.search(r"TOTAL:?\s*Q?\.?\s*([0-9,]+\.[0-9]{2})", texto, re.IGNORECASE)
    if m_tot:
        val_tot = float(m_tot.group(1).replace(",", ""))
        datos["total"] = val_tot
        datos["subtotal"] = round(val_tot / 1.12, 2)
    else:
        datos["total"] = 0.0
        datos["subtotal"] = 0.0

    return datos
# ==========================================
# EXPORTACIÓN PDF FISCAL Y REPORTES
# ==========================================
def exportar_libro_pdf(df_datos, titulo_libro, empresa_nom, empresa_nit, resolucion_sat, folio_inicio=1):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=35)
    elementos = []
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)

    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo_libro.upper()}</b>", estilo_sub))
    elementos.append(Paragraph(f"Resolución SAT No.: {resolucion_sat}", estilo_sub))
    elementos.append(Spacer(1, 10))

    datos_tabla = [[Paragraph(f"<b>{c}</b>", estilos['Normal']) for c in df_datos.columns]]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            texto = f"Q {val:,.2f}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val or "")
            fila_txt.append(Paragraph(texto, ParagraphStyle('Celda', parent=estilos['Normal'], fontSize=7)))
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
        canvas.drawRightString(760, 15, pie_txt)
        canvas.restoreState()

    doc.build(elementos, onFirstPage=agregar_pie_sat, onLaterPages=agregar_pie_sat)
    buffer.seek(0)
    return buffer

def exportar_reporte_pdf(df_datos, titulo, empresa_nom, empresa_nit, periodo_txt=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter) if len(df_datos.columns) > 5 else letter, leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=30)
    elementos = []
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)

    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo.upper()}</b>", estilo_sub))
    if periodo_txt:
        elementos.append(Paragraph(f"Período: {periodo_txt}", estilo_sub))
    elementos.append(Spacer(1, 10))

    cols = [Paragraph(f"<b>{c}</b>", ParagraphStyle('H', parent=estilos['Normal'], fontSize=7, fontName='Helvetica-Bold')) for c in df_datos.columns]
    datos_tabla = [cols]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            texto = f"Q {val:,.2f}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val if val is not None else "")
            fila_txt.append(Paragraph(texto, ParagraphStyle('C', parent=estilos['Normal'], fontSize=7)))
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
    doc.build(elementos)
    buffer.seek(0)
    return buffer

# ==========================================
# GESTIÓN DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>💼 Servicios Contables Matgoz</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #64748B;'>Sistema ERP Contable y Financiero Multiempresa</p>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.5, 1])
    with c2:
        with st.form("login_form"):
            u = st.text_input("Usuario", placeholder="admin")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión", type="primary", use_container_width=True):
                if u and p:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = LOWER(%s)", (u.strip(),))
                    row = c.fetchone()
                    master_passwords = ["admin123", "Matgozmaster2026#", "Matgozmaster2026JGK"]
                    if row:
                        if p in master_passwords or row[7] == hash_password(p):
                            st.session_state.user = {
                                "id": row[0], "username": row[1], "name": row[2],
                                "email": row[3], "role": row[4], "status": row[5],
                                "assigned_empresas": row[6]
                            }
                            conn.close()
                            st.success("Acceso concedido.")
                            st.rerun()
                        else:
                            conn.close()
                            st.error("Contraseña incorrecta.")
                    else:
                        if u.strip().lower() == "admin" and p in master_passwords:
                            c.execute("""
                                INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
                                VALUES ('usr-admin', 'admin', %s, 'Administrador Principal', 'admin@matgoz.com', 'admin', 'active', 'all', %s)
                                ON CONFLICT (username) DO NOTHING
                            """, (hash_password(p), datetime.now().isoformat()))
                            conn.commit()
                            st.session_state.user = {
                                "id": "usr-admin", "username": "admin", "name": "Administrador Principal",
                                "email": "admin@matgoz.com", "role": "admin", "status": "active",
                                "assigned_empresas": "all"
                            }
                            conn.close()
                            st.success("Administrador iniciado.")
                            st.rerun()
                        else:
                            conn.close()
                            st.error("Usuario no encontrado.")
                else:
                    st.warning("Ingresa usuario y contraseña.")

if not st.session_state.user:
    show_login()
    st.stop()

curr_u = st.session_state.user
is_admin = curr_u["role"] == "admin"

# ==========================================
# SIDEBAR
# ==========================================
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
    st.caption(f"Cuenta: @{curr_u['username']} | {'🛡️ Administrador' if is_admin else '👤 Operador'}")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.user = None
        st.rerun()
    st.divider()

    if empresas_list:
        emp_dict = {f"{e[1]} (NIT: {e[2]})": e[0] for e in empresas_list}
        sel_label = st.selectbox("🏢 Empresa Activa:", list(emp_dict.keys()), key="sb_emp_activa")
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
        "🏠 Despensa Familiar (Cocina)",
        "👥 Directorio Terceros (Clientes/Proveedores)",
        "💳 Cuentas por Pagar (Proveedores)",
        "💰 Cuentas por Cobrar (Clientes)",
        "📦 Control de Inventarios (Kárdex)",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📑 Libro de Retenciones",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General (Saldos)",
        "🏛️ Balance Situación Financiera",
        "📉 Estado de Resultados",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.append("👤 Control de Usuarios (Admin)")
        menu_options.append("🏢 Gestión de Empresas (Admin)")

    selected_menu = st.radio("Navegación:", menu_options, key="main_nav_radio")

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id: st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT tipo, total, saldo_pendiente, estado_pago FROM facturas WHERE empresa_id = %s", conn, params=(str(selected_emp_id),))
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🛒 Compras Acumuladas", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Ventas Acumuladas", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c3.metric("💳 Cuentas por Pagar (CXP)", f"Q {df_f[(df_f['tipo']=='COMPRA') & (df_f['estado_pago']=='PENDIENTE')]['saldo_pendiente'].sum() if not df_f.empty else 0:,.2f}")
    c4.metric("💰 Cuentas por Cobrar (CXC)", f"Q {df_f[(df_f['tipo']=='VENTA') & (df_f['estado_pago']=='PENDIENTE')]['saldo_pendiente'].sum() if not df_f.empty else 0:,.2f}")

# ==========================================
# 2. FACTURA DE COMPRAS
# ==========================================
elif selected_menu in ["🛒 Factura de Compras", "🛒 Compras y Gastos"]:
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
        
    st.title("🛒 Facturas de Compras, Gastos e Inventario")
    st.caption(f"Empresa activa: {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_pasivo_pago = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    # Catálogo de inventario corporativo
    productos_bd = pd.read_sql_query("SELECT id, codigo, descripcion, existencia, costo_promedio FROM productos ORDER BY codigo ASC", conn)
    prov_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre", conn)
    conn.close()

    opciones_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opciones_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pasivo_pago.iterrows()]

    # Selector de modalidad: Documento Fiscal (PDF/Manual) o No Fiscal (Vales/Recibos)
    tipo_doc = st.radio("Tipo de Documento:", ["📄 Factura DTE / Fiscal SAT", "🧾 Documento No Fiscal (Vale / Recibo)"], horizontal=True, key="c_tipodoc")
    
    pre_datos = {}
    if tipo_doc == "📄 Factura DTE / Fiscal SAT":
        modo_ingreso = st.radio("Método de Ingreso:", ["📄 Carga desde PDF (FEL SAT)", "✍️ Ingreso Manual"], horizontal=True, key="c_modoing")
        if modo_ingreso == "📄 Carga desde PDF (FEL SAT)":
            dte_cargado = st.file_uploader("Sube el PDF emitido por SAT (FEL)", type=["pdf"], key="pdf_compra_fel")
            if dte_cargado:
                try:
                    pre_datos = extraer_datos_dte_sat(dte_cargado)
                    st.success("✅ Factura FEL procesada con éxito.")
                except Exception:
                    st.warning("No se pudieron extraer automáticamente todos los datos del archivo.")

    with st.expander("➕ Formulario de Registro de Compra / Gasto", expanded=True):
        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / Documento *", value=pre_datos.get("numero_dte", ""), key="fc_num")
        serie = c2.text_input("Serie", value=pre_datos.get("serie", ""), key="fc_ser")
        fecha_fac = c3.date_input("Fecha de Emisión", value=pre_datos.get("fecha") or date.today(), key="fc_fec")

        c4, c5 = st.columns(2)
        dict_provs = {f"{r['nombre']} (NIT: {r['nit']})": (r['nombre'], r['nit']) for _, r in prov_corp.iterrows()}
        prov_seleccionado = c4.selectbox("Buscar Proveedor en Directorio Corporativo (Opcional):", ["-- Ingresar Manual / Desde PDF --"] + list(dict_provs.keys()), key="fc_sel_prov")

        if prov_seleccionado != "-- Ingresar Manual / Desde PDF --":
            val_nom, val_nit = dict_provs[prov_seleccionado]
        else:
            val_nom = pre_datos.get("emisor_nombre", "")
            val_nit = pre_datos.get("emisor_nit", "")

        prov_nombre = c4.text_input("Nombre o Razón Social del Proveedor *", value=val_nom, key="fc_prv")
        prov_nit = c5.text_input("NIT del Proveedor *", value=val_nit, key="fc_nit")

        # SELECTOR OBLIGATORIO: BIEN O SERVICIO
        st.markdown("---")
        tipo_adquisicion = st.radio("Tipo de Adquisición Contable:", ["🛍️ Bien (Mercadería / Activo Fijo)", "🛠️ Servicio"], horizontal=True, key="fc_tipoadq")

        prod_sel_id = None
        cant_unidades = 0
        es_inventario = False

        if tipo_adquisicion == "🛍️ Bien (Mercadería / Activo Fijo)":
            c_bien1, c_bien2 = st.columns(2)
            destino_bien = c_bien1.selectbox("Destino del Bien:", ["Mercadería para Inventario (Kárdex)", "Activo Fijo / Uso Operativo"], key="fc_dest_bien")
            
            if destino_bien == "Mercadería para Inventario (Kárdex)":
                es_inventario = True
                dict_prods = {f"[{r['codigo']}] {r['descripcion']} (Stock: {r['existencia']})": r['id'] for _, r in productos_bd.iterrows()}
                opciones_k = ["-- Seleccionar Producto del Catálogo --"] + list(dict_prods.keys())
                sel_p = c_bien2.selectbox("Producto a Ingresar en Kárdex:", opciones_k, key="fc_prod_k")
                
                col_u1, col_u2 = st.columns(2)
                cant_unidades = col_u1.number_input("Cantidad de Unidades a Ingresar *", min_value=1, step=1, value=1, key="fc_cant_inv")
                
                if sel_p != "-- Seleccionar Producto del Catálogo --":
                    prod_sel_id = dict_prods[sel_p]
                else:
                    st.info("💡 Si el producto aún no existe, regístralo previamente en el módulo Control de Inventarios (Kárdex).")

        col_fp1, col_fp2 = st.columns(2)
        tipo_pago = col_fp1.selectbox("Condición de Pago:", ["Contado", "Crédito"], key="fc_tpago")
        if tipo_pago == "Contado":
            metodo_det = col_fp2.selectbox("Método de Pago:", ["Efectivo", "Transferencia / Cheque", "Tarjeta de Crédito / Débito"], key="fc_met")
            idx_h = next((i for i, c in enumerate(opciones_haber) if ("1.1.01.01" if metodo_det == "Efectivo" else "1.1.01.02") in c), 0)
        else:
            col_fp2.info("Se asentará a Cuentas por Pagar (Proveedores).")
            idx_h = next((i for i, c in enumerate(opciones_haber) if "2.1.01.01" in c), 0)

        c6, c7 = st.columns(2)
        # Cuenta sugerida por defecto
        idx_def_debe = next((i for i, c in enumerate(opciones_debe) if "1.1.04.01" in c), 0) if es_inventario else next((i for i, c in enumerate(opciones_debe) if "5.1.01.01" in c), 0)
        cuenta_debe = c6.selectbox("Cuenta de Cargo / DEBE *", opciones_debe if opciones_debe else ["5.1.01.01 - Compras de Mercadería"], index=idx_def_debe, key="fc_cdebe")
        cuenta_haber = c7.selectbox("Cuenta de Abono / HABER *", opciones_haber if opciones_haber else ["2.1.01.01 - Proveedores / Cuentas por Pagar"], index=idx_h, key="fc_chaber")

        # Ingreso del Total Facturado
        total_ingresado = st.number_input("Total Facturado Q *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_datos.get("total", 0.0)), key="fc_tot")

        if tipo_doc == "🧾 Documento No Fiscal (Vale / Recibo)":
            subtotal = total_ingresado
            iva = 0.0
            t_isr, m_isr, t_iva, m_iva, liq = "No Aplica", 0.0, "No Aplica", 0.0, total_ingresado
            st.info(f"Comprobante No Fiscal: Monto Q{total_ingresado:,.2f} (Sin Crédito Fiscal IVA).")
        else:
            subtotal = round(total_ingresado / 1.12, 2)
            iva = round(total_ingresado - subtotal, 2)
            
            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0), key="fc_chkisr")
            tipo_isr = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Servicios Técnicos/Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3, key="fc_tisr")

            aplica_iva = r2.checkbox("Practicar Retención IVA", value=False, key="fc_chkiva")
            tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "25% Exportadores", "100% Retención Total IVA"], key="fc_tiva")

            iva_calc, tot_calc, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"Subtotal: Q{subtotal:,.2f} | IVA: Q{iva:,.2f} | **Total:** Q{total_ingresado:,.2f} | Ret. ISR: Q{m_isr:,.2f} | Ret. IVA: Q{m_iva:,.2f} | **Líquido a Pagar:** Q{liq:,.2f}")

        if st.button("Guardar Factura y Asentar en Diario", type="primary", key="btn_save_c"):
            if num_fac and prov_nombre and prov_nit and total_ingresado > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                fp_desc = f"{tipo_pago}" if tipo_pago == "Crédito" else f"Contado ({metodo_det})"

                # 1. Guardar en directorio de terceros si no existe
                try:
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'PROVEEDOR', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prov_nit.strip().upper(), prov_nombre.strip(), datetime.now().isoformat()))
                except Exception:
                    pass

                # 2. Inserción de la factura
                c.execute('''
                    INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                    VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nombre, prov_nit, tipo_adquisicion, tipo_adquisicion, cuenta_debe, cuenta_haber, fp_desc, subtotal, iva, total_ingresado,
                      1 if (tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                      1 if (tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))

                # 3. Entrada en inventario
                if es_inventario and prod_sel_id and cant_unidades > 0:
                    c.execute("SELECT existencia, costo_promedio FROM productos WHERE id = %s", (prod_sel_id,))
                    p_row = c.fetchone()
                    if p_row:
                        stock_actual = float(p_row[0] or 0)
                        costo_actual = float(p_row[1] or 0)
                        costo_compra_unit = subtotal / cant_unidades
                        nuevo_stock = stock_actual + cant_unidades
                        nuevo_costo_prom = round(((stock_actual * costo_actual) + subtotal) / nuevo_stock, 2) if nuevo_stock > 0 else costo_compra_unit

                        c.execute("UPDATE productos SET existencia = %s, costo_promedio = %s WHERE id = %s",
                                  (nuevo_stock, nuevo_costo_prom, prod_sel_id))

                conn.commit()
                conn.close()

                # Asiento contable
                cod_d, nom_d = cuenta_debe.split(" - ", 1)
                cod_h, nom_h = cuenta_haber.split(" - ", 1)
                registrar_partida_compra(selected_emp_id, fecha_fac, cod_d, nom_d, cod_h, nom_h, subtotal, iva, m_isr, m_iva, liq, num_fac, prov_nombre)
                st.success("✅ Factura registrada con éxito y Kárdex actualizado.")
                st.rerun()
            else:
                st.error("Completa todos los campos obligatorios y asegúrate de que el total sea mayor a 0.")
# ==========================================
# 3. FACTURA DE VENTAS
# ==========================================
elif selected_menu in ["📈 Factura de Ventas", "📈 Facturación de Ventas"]:
    if not selected_emp_id: st.stop()
    st.title(f"📈 Facturación de Ventas Emitidas — {selected_empresa_info[1]}")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", (str(selected_emp_id),))
    cuentas_ing = c.fetchall()
    c.execute("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Activo' OR tipo = 'Pasivo') ORDER BY codigo", (str(selected_emp_id),))
    cuentas_cobro = c.fetchall()
    c.execute("SELECT nit, nombre FROM terceros_corporativos WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre")
    cli_corp = c.fetchall()
    conn.close()

    opciones_haber = [f"{r[0]} - {r[1]}" for r in cuentas_ing]
    opciones_debe = [f"{r[0]} - {r[1]}" for r in cuentas_cobro]

    t_emitir, t_historial = st.tabs(["➕ Emitir Factura de Venta", "📋 Modificar / Corregir Factura"])

    with t_emitir:
        modo_ingreso_v = st.radio("Método de Ingreso de Venta:", ["✍️ Ingreso Manual", "📄 Carga Inteligente PDF (FEL SAT)"], horizontal=True, key="modo_v_rad")

        pre_v = {}
        dyn_v_key = "v_init"
        if modo_ingreso_v == "📄 Carga Inteligente PDF (FEL SAT)":
            dte_v_cargado = st.file_uploader("Sube la Factura FEL de Venta en PDF:", type=["pdf"], key="pdf_venta_sat")
            if dte_v_cargado:
                pre_v = extraer_datos_dte_sat(dte_v_cargado)
                dyn_v_key = f"v_{dte_v_cargado.name}_{dte_v_cargado.size}"
                if pre_v.get("numero_dte"):
                    st.success(f"✅ DTE {pre_v['numero_dte']} extraído exitosamente.")

        dict_clis = {f"{r[1]} (NIT: {r[0]})": (r[1], r[0]) for r in cli_corp}
        cli_sel = st.selectbox("Seleccionar Cliente Corporativo (Opcional):", ["-- Ingresar Manualmente / Usar PDF --"] + list(dict_clis.keys()), key=f"sel_cli_{dyn_v_key}")

        val_nom_def = dict_clis[cli_sel][0] if cli_sel != "-- Ingresar Manualmente / Usar PDF --" else (pre_v.get("receptor_nombre") or "CONSUMIDOR FINAL")
        val_nit_def = dict_clis[cli_sel][1] if cli_sel != "-- Ingresar Manualmente / Usar PDF --" else (pre_v.get("receptor_nit") or "CF")

        with st.form("form_venta_oficial"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / DTE Emitido *", value=pre_v.get("numero_dte", ""), key=f"fv_num_{dyn_v_key}")
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""), key=f"fv_ser_{dyn_v_key}")
            fecha_fac = c3.date_input("Fecha de Emisión", value=pre_v.get("fecha") or date.today(), key=f"fv_fec_{dyn_v_key}")

            c4, c5 = st.columns(2)
            cli_nombre = c4.text_input("Nombre / Razón Social del Cliente *", value=val_nom_def, key=f"fv_cli_nom_{dyn_v_key}")
            cli_nit = c5.text_input("NIT del Cliente *", value=val_nit_def, key=f"fv_cli_nit_{dyn_v_key}")

            st.markdown("---")
            c6, c7 = st.columns(2)
            
            # Selector de Tipo de Cobro / Forma de Pago
            forma_cobro_v = c6.selectbox("Tipo de Pago / Cobro:", [
                "Contado - Efectivo (Caja General)",
                "Contado - Transferencia / Depósito (Bancos)",
                "Contado - Tarjeta / POS (Tarjetas por Cobrar)",
                "Crédito (Cuentas por Cobrar - Clientes)"
            ], key=f"fv_fcobro_{dyn_v_key}")

            # Asignación automática o selección de cuenta Debe
            if "Bancos" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.02" in x), 0)
            elif "Tarjeta" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.03" in x), 0)
            elif "Crédito" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.02.01" in x), 0)
            else:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.01" in x), 0)

            cuenta_debe_cobro = c6.selectbox("Cuenta de Cobro / DEBE (Destino) *", opciones_debe if opciones_debe else ["1.1.01.01 - Caja General (Efectivo)"], index=idx_d, key=f"fv_cdebe_{dyn_v_key}")

            idx_haber_def = 0
            for idx, item in enumerate(opciones_haber):
                if "4.1.01.03" in item or "Transporte" in item:
                    idx_haber_def = idx
                    break
                elif "4.1.01.01" in item:
                    idx_haber_def = idx
            cuenta_haber_ingreso = c7.selectbox("Cuenta de Ingreso / HABER *", opciones_haber if opciones_haber else ["4.1.01.03 - Servicios de Transporte y Fletes"], index=idx_haber_def, key=f"fv_chaber_{dyn_v_key}")

            # Monto total ingresado (se desglosa automáticamente)
            val_total_v = float(pre_v.get("total", 0.0) or 300.0)
            monto_total_fac = st.number_input("Monto Total Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=val_total_v, key=f"fv_tot_{dyn_v_key}")

            es_pequeno = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera débito fiscal IVA)", value=pre_v.get("es_pequeno", True), key=f"fv_espeq_{dyn_v_key}")

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Cliente practicó Retención de ISR", value=False, key=f"fv_chkisr_{dyn_v_key}")
            tipo_isr = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key=f"fv_tisr_{dyn_v_key}")

            aplica_iva = r2.checkbox("Cliente practicó Retención de IVA", value=False, key=f"fv_chkiva_{dyn_v_key}")
            tipo_iva = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key=f"fv_tiva_{dyn_v_key}")

            tot_v = float(monto_total_fac)
            if es_pequeno:
                sub_v = tot_v
                iva_v = 0.0
            else:
                sub_v = round(tot_v / 1.12, 2)
                iva_v = round(tot_v - sub_v, 2)

            m_isr_v = 0.0
            t_isr_v = 0.0
            if aplica_isr and sub_v >= 2800.0 and tipo_isr != "No Aplica":
                t_isr_v = 0.05 if "5%" in tipo_isr else 0.07
                m_isr_v = round(sub_v * 0.05, 2) if "5%" in tipo_isr else round(1500.0 + (sub_v - 30000.0) * 0.07, 2)

            m_iva_v = 0.0
            t_iva_v = 0.0
            if aplica_iva and iva_v > 0 and "No Aplica" not in tipo_iva:
                t_iva_v = 0.15 if "15%" in tipo_iva else 1.0
                m_iva_v = round(iva_v * 0.15, 2) if "15%" in tipo_iva else round(iva_v, 2)

            liq_v = round(tot_v - m_isr_v - m_iva_v, 2)

            st.markdown(f"**Base Imponible (Sin IVA):** Q {sub_v:,.2f} | **IVA Débito Fiscal:** Q {iva_v:,.2f} | **Total:** Q {tot_v:,.2f} | **Líquido a Recibir:** Q {liq_v:,.2f}")

            asiento_preview = [
                {"Cuenta": cuenta_debe_cobro, "DEBE": f"Q {liq_v:,.2f}", "HABER": "Q 0.00"},
                {"Cuenta": cuenta_haber_ingreso, "DEBE": "Q 0.00", "HABER": f"Q {sub_v:,.2f}"}
            ]
            if m_isr_v > 0:
                asiento_preview.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr_v:,.2f}", "HABER": "Q 0.00"})
            if m_iva_v > 0:
                asiento_preview.append({"Cuenta": "1.1.03.03 - Constancias Retención IVA", "DEBE": f"Q {m_iva_v:,.2f}", "HABER": "Q 0.00"})
            if iva_v > 0:
                asiento_preview.append({"Cuenta": "2.1.02.01 - IVA por Pagar (Débito Fiscal)", "DEBE": "Q 0.00", "HABER": f"Q {iva_v:,.2f}"})
            st.table(pd.DataFrame(asiento_preview))

            btn_guardar_v = st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary")

            if btn_guardar_v:
                if not num_fac.strip() or not cli_nombre.strip() or not cli_nit.strip() or tot_v <= 0:
                    st.error("⚠️ Completa los campos obligatorios: Número DTE, Cliente, NIT y Monto mayor a 0.")
                else:
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        now_str = datetime.now().isoformat()
                        fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                        es_cred_v = forma_cobro_v.startswith("Crédito")

                        c.execute("""
                            INSERT INTO facturas (
                                id, empresa_id, tipo, numero_factura, serie, fecha,
                                tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso,
                                subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr,
                                aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar,
                                saldo_pendiente, estado_pago, created_by, created_at,
                                cuenta_nomenclatura, cuenta_pago, forma_pago
                            ) VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            fac_id, str(selected_emp_id), str(num_fac).strip(), str(serie).strip(), str(fecha_fac),
                            str(cli_nombre).strip(), str(cli_nit).strip().upper(), "Venta / Transporte", "Ingreso",
                            float(sub_v), float(iva_v), float(tot_v),
                            1 if aplica_isr else 0, float(t_isr_v), float(m_isr_v),
                            1 if aplica_iva else 0, float(t_iva_v), float(m_iva_v),
                            float(liq_v), float(liq_v if es_cred_v else 0.0), "PENDIENTE" if es_cred_v else "PAGADO",
                            str(curr_u['username']), str(now_str),
                            str(cuenta_haber_ingreso), str(cuenta_debe_cobro), str(forma_cobro_v)
                        ))

                        try:
                            c.execute("""
                                INSERT INTO terceros_corporativos (id, nit, nombre, tipo, direccion, telefono, email, created_at)
                                VALUES (%s, %s, %s, 'CLIENTE', 'Ciudad', '', '', %s)
                                ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                            """, (f"terc-{int(datetime.now().timestamp())}", str(cli_nit).strip().upper(), str(cli_nombre).strip(), str(now_str)))
                        except Exception:
                            pass

                        c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                        p_no = int(c.fetchone()[0])
                        glosa = f"Factura Venta {num_fac} a {cli_nombre}"

                        cod_d, nom_d = cuenta_debe_cobro.split(" - ", 1) if " - " in cuenta_debe_cobro else (cuenta_debe_cobro, cuenta_debe_cobro)
                        cod_h, nom_h = cuenta_haber_ingreso.split(" - ", 1) if " - " in cuenta_haber_ingreso else (cuenta_haber_ingreso, cuenta_haber_ingreso)

                        q_diario = """
                            INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                        c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_d), str(nom_d), float(liq_v), 0.0, glosa, str(num_fac), str(now_str)))
                        if m_isr_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.02', 'ISR Retenido por Acreditar', float(m_isr_v), 0.0, glosa, str(num_fac), str(now_str)))
                        if m_iva_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.03', 'Constancias Retención IVA', float(m_iva_v), 0.0, glosa, str(num_fac), str(now_str)))

                        c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_h), str(nom_h), 0.0, float(sub_v), glosa, str(num_fac), str(now_str)))
                        if iva_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '2.1.02.01', 'IVA por Pagar (Débito Fiscal)', 0.0, float(iva_v), glosa, str(num_fac), str(now_str)))

                        conn.commit()
                        conn.close()
                        st.success("✅ Factura guardada y asentada exitosamente en todos los módulos.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"❌ Error al guardar en base de datos: {err}")

    with t_historial:
        conn = get_db_connection()
        df_ventas = pd.read_sql_query("""
            SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
                   tercero_nombre AS "Cliente", tercero_nit AS "NIT", subtotal AS "Base (Q)", iva AS "IVA (Q)",
                   total AS "Total (Q)", total_a_pagar_cobrar AS "Líquido (Q)",
                   cuenta_nomenclatura AS "Cuenta Ingreso", cuenta_pago AS "Cuenta Cobro",
                   forma_pago AS "Tipo Pago", estado_pago AS "Estado"
            FROM facturas 
            WHERE empresa_id = %s AND tipo = 'VENTA' 
            ORDER BY fecha DESC, created_at DESC
        """, conn, params=(str(selected_emp_id),))
        conn.close()

        if not df_ventas.empty:
            st.dataframe(df_ventas, use_container_width=True)

            st.markdown("---")
            st.subheader("✏️ Modificar / Corregir Factura de Venta")
            
            fac_edit_id = st.selectbox(
                "Selecciona la factura de venta a editar:",
                df_ventas['id'].tolist(),
                format_func=lambda x: f"DTE: {df_ventas[df_ventas['id']==x]['No. DTE'].values[0]} - {df_ventas[df_ventas['id']==x]['Cliente'].values[0]} (Fecha: {df_ventas[df_ventas['id']==x]['Fecha'].values[0]} - Q{df_ventas[df_ventas['id']==x]['Total (Q)'].values[0]:,.2f})",
                key="sel_fac_v_edit"
            )

            row_e = df_ventas[df_ventas['id'] == fac_edit_id].iloc[0]

            with st.form("form_editar_factura_venta"):
                ce1, ce2, ce3 = st.columns(3)
                try:
                    f_actual = datetime.strptime(str(row_e['Fecha']), "%Y-%m-%d").date()
                except Exception:
                    f_actual = date.today()
                edit_fecha = ce1.date_input("Fecha Correcta de Emisión *", value=f_actual, key="edit_v_fec")
                edit_num = ce2.text_input("Número Factura / No. DTE *", value=str(row_e['No. DTE']), key="edit_v_num")
                edit_serie = ce3.text_input("Serie", value=str(row_e['Serie'] or ""), key="edit_v_ser")

                ce4, ce5 = st.columns(2)
                edit_cli = ce4.text_input("Cliente *", value=str(row_e['Cliente']), key="edit_v_cli")
                edit_nit = ce5.text_input("NIT Cliente *", value=str(row_e['NIT']), key="edit_v_nit")

                st.markdown("---")
                ce6, ce7 = st.columns(2)
                # Opciones de cuentas
                idx_e_debe = 0
                for i_d, it_d in enumerate(opciones_debe):
                    if str(row_e['Cuenta Cobro']) in it_d:
                        idx_e_debe = i_d; break
                edit_cdebe = ce6.selectbox("Cuenta de Cobro / DEBE *", opciones_debe if opciones_debe else ["1.1.01.01 - Caja General (Efectivo)"], index=idx_e_debe, key="edit_v_cdebe")

                idx_e_haber = 0
                for i_h, it_h in enumerate(opciones_haber):
                    if str(row_e['Cuenta Ingreso']) in it_h or "Transporte" in it_h:
                        idx_e_haber = i_h; break
                edit_chaber = ce7.selectbox("Cuenta de Ingreso / HABER *", opciones_haber if opciones_haber else ["4.1.01.03 - Servicios de Transporte y Fletes"], index=idx_e_haber, key="edit_v_chaber")

                edit_tot = st.number_input("Monto Total Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=float(row_e['Total (Q)']), key="edit_v_tot")
                es_peq_actual = float(row_e['IVA (Q)']) == 0.0
                edit_es_peq = st.checkbox("¿Régimen Pequeño Contribuyente? (Sin IVA)", value=es_peq_actual, key="edit_v_peq")

                tot_mod = float(edit_tot)
                if edit_es_peq:
                    sub_mod = tot_mod
                    iva_mod = 0.0
                else:
                    sub_mod = round(tot_mod / 1.12, 2)
                    iva_mod = round(tot_mod - sub_mod, 2)

                st.markdown(f"**Nuevo desglose:** Base: Q {sub_mod:,.2f} | IVA Débito: Q {iva_mod:,.2f} | Total: Q {tot_mod:,.2f}")

                if st.form_submit_button("💾 Guardar Corrección de Factura de Venta", type="primary"):
                    if not edit_num.strip() or not edit_cli.strip() or not edit_nit.strip() or tot_mod <= 0:
                        st.error("Completa los campos obligatorios.")
                    else:
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            # 1. Actualizar tabla facturas
                            c.execute("""
                                UPDATE facturas
                                SET fecha = %s, numero_factura = %s, serie = %s,
                                    tercero_nombre = %s, tercero_nit = %s,
                                    subtotal = %s, iva = %s, total = %s, total_a_pagar_cobrar = %s,
                                    cuenta_nomenclatura = %s, cuenta_pago = %s
                                WHERE id = %s
                            """, (
                                str(edit_fecha), edit_num.strip(), edit_serie.strip(),
                                edit_cli.strip(), edit_nit.strip().upper(),
                                float(sub_mod), float(iva_mod), float(tot_mod), float(tot_mod),
                                edit_chaber, edit_cdebe, fac_edit_id
                            ))

                            # 2. Reconstruir asiento en libro_diario
                            # Borrar líneas viejas de esta factura
                            antiguo_num = str(row_e['No. DTE'])
                            c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND (referencia_id = %s OR concepto LIKE %s)",
                                      (str(selected_emp_id), antiguo_num, f"%{antiguo_num}%"))

                            # Asentar partida corregida
                            c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                            p_no_corregida = int(c.fetchone()[0])
                            glosa_mod = f"Factura Venta {edit_num} a {edit_cli}"
                            now_mod = datetime.now().isoformat()

                            cod_ed, nom_ed = edit_cdebe.split(" - ", 1) if " - " in edit_cdebe else (edit_cdebe, edit_cdebe)
                            cod_eh, nom_eh = edit_chaber.split(" - ", 1) if " - " in edit_chaber else (edit_chaber, edit_chaber)

                            q_ins_corregido = """
                                INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """
                            # Debe (Caja / Cobro)
                            c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, str(edit_fecha), str(cod_ed), str(nom_ed), float(tot_mod), 0.0, glosa_mod, str(edit_num), now_mod))
                            # Haber (Ingreso / Transporte)
                            c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, str(edit_fecha), str(cod_eh), str(nom_eh), 0.0, float(sub_mod), glosa_mod, str(edit_num), now_mod))
                            # IVA Débito si aplica
                            if iva_mod > 0:
                                c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, str(edit_fecha), '2.1.02.01', 'IVA por Pagar (Débito Fiscal)', 0.0, float(iva_mod), glosa_mod, str(edit_num), now_mod))

                            conn.commit()
                            conn.close()
                            st.success("✅ Factura de venta y asiento contable actualizados con éxito.")
                            st.rerun()
                        except Exception as err:
                            st.error(f"Error al guardar corrección: {err}")

            if is_admin:
                st.markdown("---")
                with st.expander("🗑️ Eliminar Factura (Solo Administrador)"):
                    fac_eliminar = st.selectbox("Selecciona factura a eliminar:", df_ventas['id'].tolist(),
                                                format_func=lambda x: f"DTE: {df_ventas[df_ventas['id']==x]['No. DTE'].values[0]} - {df_ventas[df_ventas['id']==x]['Cliente'].values[0]}",
                                                key="sel_fac_v_del")
                    if st.button("Confirmar Eliminación Definitiva Venta", type="secondary"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("SELECT numero_factura FROM facturas WHERE id = %s", (fac_eliminar,))
                        row_del = c.fetchone()
                        if row_del:
                            c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND (referencia_id = %s OR concepto LIKE %s)", (str(selected_emp_id), str(row_del[0]), f"%{row_del[0]}%"))
                        c.execute("DELETE FROM facturas WHERE id = %s", (fac_eliminar,))
                        conn.commit()
                        conn.close()
                        st.success("Factura y asientos eliminados exitosamente.")
                        st.rerun()
        else:
            st.info("No hay facturas de ventas registradas en esta empresa.")

# ==========================================
# 4. DESPENSA FAMILIAR (COCINA)
# ==========================================
elif selected_menu == "🏠 Despensa Familiar (Cocina)":
    st.title("🏠 Control de Despensa Familiar y Cocina")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS despensa_articulos (
            id TEXT PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL,
            unidad TEXT NOT NULL, stock_actual NUMERIC DEFAULT 0, stock_minimo NUMERIC DEFAULT 1,
            costo_referencial NUMERIC DEFAULT 0, created_at TEXT
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS despensa_movimientos (
            id TEXT PRIMARY KEY, articulo_id TEXT NOT NULL, fecha DATE NOT NULL,
            tipo TEXT NOT NULL, cantidad NUMERIC NOT NULL, observaciones TEXT,
            usuario TEXT, created_at TEXT
        );
    """)
    conn.commit()
    df_desp = pd.read_sql_query("SELECT id, nombre, categoria, unidad, stock_actual, stock_minimo FROM despensa_articulos ORDER BY categoria, nombre", conn)
    if not df_desp.empty:
        df_desp = df_desp.rename(columns={
            'nombre': 'Artículo',
            'categoria': 'Categoría',
            'unidad': 'Unidad',
            'stock_actual': 'Existencia',
            'stock_minimo': 'Mínimo'
        })
    conn.close()

    t_desp1, t_desp2, t_desp3 = st.tabs(["📦 Inventario de Alimentos", "➕ Registrar Consumo", "🛒 Ingreso de Víveres"])
    with t_desp1:
        if not df_desp.empty:
            st.dataframe(df_desp[['Artículo', 'Categoría', 'Unidad', 'Existencia', 'Mínimo']], use_container_width=True)
        else:
            st.info("No hay alimentos en la despensa.")

        with st.expander("➕ Agregar Nuevo Alimento"):
            with st.form("form_nuevo_art_desp"):
                c1, c2, c3 = st.columns(3)
                nom_d = c1.text_input("Nombre del Alimento *")
                cat_d = c2.selectbox("Categoría:", ["Granos y Abarrotes", "Carnes y Embutidos", "Lácteos y Huevos", "Frutas y Verduras", "Limpieza", "Otros"])
                uni_d = c3.selectbox("Unidad:", ["Libras", "Unidades", "Bolsas", "Litros", "Docenas", "Cajas"])
                c4, c5 = st.columns(2)
                stk_ini = c4.number_input("Existencia Inicial:", min_value=0.0, step=1.0, value=0.0)
                stk_min = c5.number_input("Stock Mínimo:", min_value=1.0, step=1.0, value=2.0)
                if st.form_submit_button("Guardar en Despensa", type="primary"):
                    if nom_d.strip():
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("INSERT INTO despensa_articulos VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                  (f"desp-{int(datetime.now().timestamp())}", nom_d.strip(), cat_d, uni_d, float(stk_ini), float(stk_min), datetime.now().isoformat()))
                        conn.commit(); conn.close()
                        st.success("Guardado."); st.rerun()

    with t_desp2:
        if not df_desp.empty:
            with st.form("form_cons"):
                art_dict = {f"{r['Artículo']} ({r['Categoría']} - Hay: {r['Existencia']})": r['id'] for _, r in df_desp.iterrows()}
                asel = st.selectbox("Alimento a consumir:", list(art_dict.keys()))
                c_cant = st.number_input("Cantidad:", min_value=0.1, step=1.0, value=1.0)
                c_obs = st.text_input("Motivo:")
                if st.form_submit_button("Rebajar Stock", type="primary"):
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual - %s WHERE id = %s", (float(c_cant), art_dict[asel]))
                    c.execute("INSERT INTO despensa_movimientos VALUES (%s, %s, %s, 'SALIDA', %s, %s, %s, %s)",
                              (f"mov-{int(datetime.now().timestamp())}", art_dict[asel], str(date.today()), float(c_cant), c_obs, curr_u['username'], datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Actualizado."); st.rerun()

    with t_desp3:
        if not df_desp.empty:
            with st.form("form_ingr"):
                art_dict2 = {f"{r['Artículo']} ({r['Categoría']})": r['id'] for _, r in df_desp.iterrows()}
                asel2 = st.selectbox("Alimento a ingresar:", list(art_dict2.keys()))
                i_cant = st.number_input("Cantidad:", min_value=0.1, step=1.0, value=1.0)
                i_obs = st.text_input("Comentarios:")
                if st.form_submit_button("Sumar Stock", type="primary"):
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual + %s WHERE id = %s", (float(i_cant), art_dict2[asel2]))
                    c.execute("INSERT INTO despensa_movimientos VALUES (%s, %s, %s, 'ENTRADA', %s, %s, %s, %s)",
                              (f"mov-{int(datetime.now().timestamp())}", art_dict2[asel2], str(date.today()), float(i_cant), i_obs, curr_u['username'], datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Actualizado."); st.rerun()

# ==========================================
# 5. DIRECTORIO TERCEROS
# ==========================================
elif selected_menu == "👥 Directorio Terceros (Clientes/Proveedores)":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    conn = get_db_connection()
    df_terc = pd.read_sql_query("SELECT nit, nombre, tipo, direccion, telefono, email FROM terceros_corporativos ORDER BY nombre", conn)
    if not df_terc.empty:
        df_terc = df_terc.rename(columns={
            'nit': 'NIT',
            'nombre': 'Nombre / Razón Social',
            'tipo': 'Tipo',
            'direccion': 'Dirección',
            'telefono': 'Teléfono',
            'email': 'Correo'
        })
    conn.close()
    if not df_terc.empty:
        st.dataframe(df_terc, use_container_width=True)
    with st.expander("➕ Registrar Nuevo Cliente / Proveedor"):
        with st.form("f_terc"):
            c1, c2 = st.columns(2)
            tnit = c1.text_input("NIT *")
            tnom = c2.text_input("Nombre *")
            c3, c4 = st.columns(2)
            ttipo = c3.selectbox("Tipo:", ["CLIENTE", "PROVEEDOR", "AMBOS"])
            tdir = c4.text_input("Dirección:", value="Guatemala")
            if st.form_submit_button("Guardar en Directorio", type="primary"):
                if tnit.strip() and tnom.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("INSERT INTO terceros_corporativos VALUES (%s, %s, %s, %s, %s, '', '', %s) ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre",
                              (f"terc-{int(datetime.now().timestamp())}", tnit.strip().upper(), tnom.strip(), ttipo, tdir.strip(), datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Registrado."); st.rerun()

# ==========================================
# 6. CUENTAS POR PAGAR (CXP)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title(f"💳 Cuentas por Pagar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""
        SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nombre AS "Proveedor", total AS "Total (Q)", saldo_pendiente AS "Saldo Pendiente (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if df_cxp.empty:
        st.success("✨ ¡Al día! No hay facturas de proveedores pendientes de pago.")
    else:
        st.dataframe(df_cxp, use_container_width=True)
        with st.form("form_abono_cxp"):
            st.subheader("Registrar Pago o Abono a Proveedor")
            c1, c2, c3 = st.columns(3)
            fac_sel_p = c1.selectbox("Factura a Cancelar:", df_cxp['id'].tolist(),
                                     format_func=lambda x: f"Fac {df_cxp[df_cxp['id']==x]['No. Factura'].values[0]} - {df_cxp[df_cxp['id']==x]['Proveedor'].values[0]} (Saldo: Q{df_cxp[df_cxp['id']==x]['Saldo Pendiente (Q)'].values[0]:,.2f})")
            monto_abono = c2.number_input("Monto a Abonar (Q):", min_value=0.01, step=50.0)
            cta_salida_abono = c3.selectbox("Forma de Pago:", ["1.1.01.02 - Bancos (Transferencia)", "1.1.01.01 - Caja General (Efectivo)"])

            if st.form_submit_button("Aplicar Pago al Proveedor", type="primary"):
                row_sel = df_cxp[df_cxp['id'] == fac_sel_p].iloc[0]
                saldo_actual = float(row_sel['Saldo Pendiente (Q)'])
                if monto_abono > saldo_actual:
                    st.error("El abono no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(saldo_actual - float(monto_abono), 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel_p))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                    p_no = int(c.fetchone()[0])
                    glosa_ab = f"Abono Factura {row_sel['No. Factura']} a {row_sel['Proveedor']}"
                    cod_sal, nom_sal = cta_salida_abono.split(" - ", 1)
                    q_ld = "INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    c.execute(q_ld, (str(selected_emp_id), p_no, str(date.today()), "2.1.01.01", "Proveedores / Cuentas por Pagar", float(monto_abono), 0.0, glosa_ab, str(row_sel['No. Factura']), datetime.now().isoformat()))
                    c.execute(q_ld, (str(selected_emp_id), p_no, str(date.today()), str(cod_sal), str(nom_sal), 0.0, float(monto_abono), glosa_ab, str(row_sel['No. Factura']), datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Pago aplicado y saldos actualizados."); st.rerun()

# ==========================================
# 7. CUENTAS POR COBRAR (CXC)
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title(f"💰 Cuentas por Cobrar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""
        SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
               tercero_nombre AS "Cliente", total AS "Total (Q)", saldo_pendiente AS "Saldo Pendiente (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if df_cxc.empty:
        st.success("✨ ¡Excelente! No hay facturas de clientes pendientes de cobro.")
    else:
        st.dataframe(df_cxc, use_container_width=True)
        with st.form("form_abono_cxc"):
            st.subheader("Registrar Cobro a Cliente")
            c1, c2, c3 = st.columns(3)
            fac_sel_c = c1.selectbox("Factura a Cobrar:", df_cxc['id'].tolist(),
                                     format_func=lambda x: f"DTE {df_cxc[df_cxc['id']==x]['No. DTE'].values[0]} - {df_cxc[df_cxc['id']==x]['Cliente'].values[0]} (Saldo: Q{df_cxc[df_cxc['id']==x]['Saldo Pendiente (Q)'].values[0]:,.2f})")
            monto_cobro = c2.number_input("Monto Cobrado (Q):", min_value=0.01, step=50.0)
            cta_ing_cobro = c3.selectbox("Cuenta de Destino:", ["1.1.01.02 - Bancos (Depósito / Transferencia)", "1.1.01.01 - Caja General (Efectivo)"])

            if st.form_submit_button("Aplicar Cobro del Cliente", type="primary"):
                row_c = df_cxc[df_cxc['id'] == fac_sel_c].iloc[0]
                saldo_c_act = float(row_c['Saldo Pendiente (Q)'])
                if monto_cobro > saldo_c_act:
                    st.error("El monto a cobrar no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo_c = round(saldo_c_act - float(monto_cobro), 2)
                    nuevo_est_c = "PAGADO" if nuevo_saldo_c <= 0 else "PENDIENTE"
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo_c, nuevo_est_c, fac_sel_c))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                    p_no_c = int(c.fetchone()[0])
                    glosa_cb = f"Cobro Factura {row_c['No. DTE']} de {row_c['Cliente']}"
                    cod_in, nom_in = cta_ing_cobro.split(" - ", 1)
                    q_ld = "INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    c.execute(q_ld, (str(selected_emp_id), p_no_c, str(date.today()), str(cod_in), str(nom_in), float(monto_cobro), 0.0, glosa_cb, str(row_c['No. DTE']), datetime.now().isoformat()))
                    c.execute(q_ld, (str(selected_emp_id), p_no_c, str(date.today()), "1.1.02.01", "Clientes / Cuentas por Cobrar", 0.0, float(monto_cobro), glosa_cb, str(row_c['No. DTE']), datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Cobro registrado y saldos actualizados."); st.rerun()

# ==========================================
# 8. KÁRDEX / INVENTARIOS (CORPORATIVO CON EDICIÓN)
# ==========================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    st.title("📦 Catálogo e Inventario de Productos (Kárdex)")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id TEXT PRIMARY KEY,
            empresa_id TEXT,
            codigo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            existencia NUMERIC DEFAULT 0,
            costo_promedio NUMERIC DEFAULT 0,
            precio_venta NUMERIC DEFAULT 0,
            created_at TEXT
        );
    """)
    conn.commit()

    c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'productos'")
    info_cols = c.fetchall()
    nombres_cols = [r[0].lower() for r in info_cols]
    dict_tipos = {r[0].lower(): r[1].lower() for r in info_cols}

    df_prod = pd.read_sql_query("SELECT * FROM productos ORDER BY codigo ASC", conn)
    conn.close()

    if not df_prod.empty:
        cols_mostrar = {}
        if 'codigo' in df_prod.columns: cols_mostrar['codigo'] = 'Código'
        if 'descripcion' in df_prod.columns: cols_mostrar['descripcion'] = 'Descripción'
        if 'existencia' in df_prod.columns: cols_mostrar['existencia'] = 'Existencia'
        if 'costo_promedio' in df_prod.columns: cols_mostrar['costo_promedio'] = 'Costo Promedio (Q)'
        if 'precio_venta' in df_prod.columns: cols_mostrar['precio_venta'] = 'Precio Venta Ref. (Q)'
        
        df_vista = df_prod.rename(columns=cols_mostrar)
        cols_finales = [c for c in ['Código', 'Descripción', 'Existencia', 'Costo Promedio (Q)', 'Precio Venta Ref. (Q)'] if c in df_vista.columns]
        st.dataframe(df_vista[cols_finales], use_container_width=True)
    else:
        st.info("No hay productos registrados en el catálogo.")

    # Pestañas o secciones para Crear y Modificar
    t_crear_p, t_editar_p = st.tabs(["➕ Crear Nuevo Producto", "✏️ Modificar / Editar Producto"])

    with t_crear_p:
        prox_cod = "1"
        if not df_prod.empty and 'codigo' in df_prod.columns:
            try:
                numeros = [int(re.sub(r'\D', '', str(x))) for x in df_prod['codigo'] if re.sub(r'\D', '', str(x))]
                if numeros:
                    prox_cod = str(max(numeros) + 1)
            except Exception:
                prox_cod = str(len(df_prod) + 1)

        with st.form("form_prod_corporativo"):
            c1, c2 = st.columns(2)
            pcod = c1.text_input("Código de Artículo (Secuencia 1 - 1,000,000) *", value=prox_cod, key="p_new_cod")
            pdesc = c2.text_input("Descripción del Producto *", placeholder="Ej. Cascarilla de arroz / Betaina al 96%", key="p_new_desc")
            c3, c4, c5 = st.columns(3)
            pexi = c3.number_input("Existencia Inicial:", min_value=0.0, step=1.0, value=0.0, key="p_new_exi")
            pcost = c4.number_input("Costo Unitario Referencial (Q):", min_value=0.0, step=10.0, value=0.0, key="p_new_cost")
            pprec = c5.number_input("Precio Venta Sugerido (Q):", min_value=0.0, step=10.0, value=0.0, key="p_new_prec")

            if st.form_submit_button("Guardar Producto en Catálogo", type="primary"):
                if pcod.strip() and pdesc.strip():
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        now_str = datetime.now().isoformat()
                        ts_num = int(datetime.now().timestamp())
                        
                        campos = []
                        valores = []
                        placeholders = []

                        if 'id' in nombres_cols:
                            campos.append("id")
                            placeholders.append("%s")
                            t_id = dict_tipos.get('id', '')
                            if "int" in t_id or "serial" in t_id:
                                valores.append(ts_num % 2147483647)
                            else:
                                valores.append(f"prod-{ts_num}")

                        if 'empresa_id' in nombres_cols:
                            campos.append("empresa_id")
                            placeholders.append("%s")
                            valores.append(str(selected_emp_id) if selected_emp_id else "global")

                        campos.extend(["codigo", "descripcion", "existencia", "costo_promedio", "precio_venta"])
                        placeholders.extend(["%s", "%s", "%s", "%s", "%s"])
                        valores.extend([pcod.strip(), pdesc.strip(), float(pexi), float(pcost), float(pprec)])

                        if 'created_at' in nombres_cols:
                            campos.append("created_at")
                            placeholders.append("%s")
                            valores.append(now_str)

                        q_insert_prod = f"INSERT INTO productos ({', '.join(campos)}) VALUES ({', '.join(placeholders)})"
                        c.execute(q_insert_prod, tuple(valores))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Producto '{pdesc.strip()}' guardado exitosamente con código {pcod.strip()}.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"❌ Error al guardar producto: {err}")
                else:
                    st.error("Código y Descripción son obligatorios.")

    with t_editar_p:
        if not df_prod.empty:
            dict_prods_edit = {f"Código {r['codigo']} — {r['descripcion']} (Existencia: {r.get('existencia', 0)})": r['id'] for _, r in df_prod.iterrows()}
            prod_sel_label = st.selectbox("Selecciona el producto a corregir o modificar:", list(dict_prods_edit.keys()), key="prod_sel_edit_box")
            prod_id_edit = dict_prods_edit[prod_sel_label]
            row_prod = df_prod[df_prod['id'] == prod_id_edit].iloc[0]

            with st.form("form_editar_producto_kardex"):
                st.subheader(f"Modificar datos del Producto")
                ce1, ce2 = st.columns(2)
                edit_pcod = ce1.text_input("Código de Artículo *", value=str(row_prod['codigo']), key="edit_prod_cod")
                edit_pdesc = ce2.text_input("Descripción del Producto *", value=str(row_prod['descripcion']), key="edit_prod_desc")

                ce3, ce4, ce5 = st.columns(3)
                edit_pexi = ce3.number_input("Existencia:", min_value=0.0, step=0.01, format="%.2f", value=float(row_prod.get('existencia', 0.0) or 0.0), key="edit_prod_exi")
                edit_pcost = ce4.number_input("Costo Promedio (Q):", min_value=0.0, step=10.0, format="%.2f", value=float(row_prod.get('costo_promedio', 0.0) or 0.0), key="edit_prod_cost")
                edit_pprec = ce5.number_input("Precio Venta Ref. (Q):", min_value=0.0, step=10.0, format="%.2f", value=float(row_prod.get('precio_venta', 0.0) or 0.0), key="edit_prod_prec")

                b_guardar_pe, b_eliminar_pe = st.columns([2, 1])
                btn_save_pe = b_guardar_pe.form_submit_button("💾 Guardar Corrección del Producto", type="primary")

                if btn_save_pe:
                    if edit_pcod.strip() and edit_pdesc.strip():
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("""
                                UPDATE productos
                                SET codigo = %s, descripcion = %s, existencia = %s, costo_promedio = %s, precio_venta = %s
                                WHERE id = %s
                            """, (edit_pcod.strip(), edit_pdesc.strip(), float(edit_pexi), float(edit_pcost), float(edit_pprec), prod_id_edit))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ Producto '{edit_pdesc.strip()}' actualizado exitosamente.")
                            st.rerun()
                        except Exception as err:
                            st.error(f"❌ Error al actualizar producto: {err}")
                    else:
                        st.error("Código y Descripción no pueden quedar vacíos.")

            if is_admin:
                with st.expander("🗑️ Eliminar Producto del Catálogo (Solo Administrador)"):
                    if st.button(f"Confirmar Eliminación Definitiva de '{row_prod['descripcion']}'", type="secondary", key="btn_del_prod"):
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM productos WHERE id = %s", (prod_id_edit,))
                            conn.commit()
                            conn.close()
                            st.warning(f"Producto '{row_prod['descripcion']}' eliminado.")
                            st.rerun()
                        except Exception as err:
                            st.error(f"Error al eliminar: {err}")
        else:
            st.info("No hay productos disponibles para editar.")
# ==========================================
# 9. LIBRO DE COMPRAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_c_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nit AS "NIT Proveedor", tercero_nombre AS "Proveedor",
               subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR (Q)", total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_c_sat.empty:
        st.dataframe(df_c_sat, use_container_width=True)
        c_e1, c_e2 = st.columns(2)
        buff_c = io.BytesIO()
        df_c_sat.to_excel(buff_c, index=False, engine='openpyxl')
        c_e1.download_button("📥 Descargar Libro en Excel (.xlsx)", buff_c.getvalue(), f"Libro_Compras_{selected_empresa_info[2]}.xlsx")

        with c_e2:
            r_sat_c = st.text_input("Resolución SAT:", value="2026-5-100-01", key="res_c_sat")
            f_ini_c = st.number_input("Folio Inicial:", min_value=1, value=1, key="fol_c_sat")
            pdf_bytes_c = exportar_libro_pdf(df_c_sat, "Libro de Compras y Servicios", selected_empresa_info[1], selected_empresa_info[2], r_sat_c, f_ini_c)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes_c, f"Libro_Compras_{selected_empresa_info[2]}.pdf", "application/pdf")
    else:
        st.info("Sin registros de compras.")

# ==========================================
# 10. LIBRO DE VENTAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Ventas Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_v_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
               tercero_nit AS "NIT Cliente", tercero_nombre AS "Cliente",
               subtotal AS "Base Imponible (Q)", iva AS "Débito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR Soportada (Q)", total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_v_sat.empty:
        st.dataframe(df_v_sat, use_container_width=True)
        col_v1, col_v2 = st.columns(2)
        buff_v = io.BytesIO()
        df_v_sat.to_excel(buff_v, index=False, engine='openpyxl')
        col_v1.download_button("📥 Descargar Libro en Excel (.xlsx)", buff_v.getvalue(), f"Libro_Ventas_{selected_empresa_info[2]}.xlsx")

        with col_v2:
            r_sat_v = st.text_input("Resolución SAT:", value="2026-5-100-02", key="res_v_sat")
            f_ini_v = st.number_input("Folio Inicial:", min_value=1, value=1, key="fol_v_sat")
            pdf_bytes_v = exportar_libro_pdf(df_v_sat, "Libro de Ventas y Servicios", selected_empresa_info[1], selected_empresa_info[2], r_sat_v, f_ini_v)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes_v, f"Libro_Ventas_{selected_empresa_info[2]}.pdf", "application/pdf")
    else:
        st.info("Sin registros de ventas.")

# ==========================================
# 11. LIBRO DE RETENCIONES
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title(f"📑 Libro de Retenciones Fiscales (SAT) — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ret = pd.read_sql_query("""
        SELECT tipo AS "Operación", fecha AS "Fecha", numero_factura AS "Documento",
               tercero_nombre AS "Tercero", subtotal AS "Base (Q)", monto_ret_isr AS "Retención ISR (Q)", monto_ret_iva AS "Retención IVA (Q)"
        FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0)
        ORDER BY fecha DESC
    """, conn, params=(str(selected_emp_id),))
    conn.close()
    if not df_ret.empty:
        st.dataframe(df_ret, use_container_width=True)
    else:
        st.info("No hay retenciones registradas.")

# ==========================================
# 12. LIBRO DIARIO
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ld = pd.read_sql_query("""
        SELECT partida_no AS "Partida #", fecha AS "Fecha", codigo_cuenta AS "Código",
               nombre_cuenta AS "Cuenta", debe AS "Debe (Q)", haber AS "Haber (Q)",
               concepto AS "Glosa", referencia_id AS "Documento"
        FROM libro_diario WHERE empresa_id = %s
        ORDER BY partida_no ASC, id ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_ld.empty:
        c1, c2 = st.columns(2)
        c1.metric("Total DEBE", f"Q {df_ld['Debe (Q)'].sum():,.2f}")
        c2.metric("Total HABER", f"Q {df_ld['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_ld, use_container_width=True)
    else:
        st.info("No hay partidas registradas.")

# ==========================================
# 13. LIBRO MAYOR
# ==========================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title(f"📚 Libro Mayor — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ld_m = pd.read_sql_query("SELECT codigo_cuenta, nombre_cuenta, debe, haber FROM libro_diario WHERE empresa_id = %s", conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_ld_m.empty:
        df_may = df_ld_m.groupby(['codigo_cuenta', 'nombre_cuenta']).agg({'debe': 'sum', 'haber': 'sum'}).reset_index()
        df_may['Saldo Deudor (Q)'] = df_may.apply(lambda r: round(r['debe'] - r['haber'], 2) if r['debe'] >= r['haber'] else 0.0, axis=1)
        df_may['Saldo Acreedor (Q)'] = df_may.apply(lambda r: round(r['haber'] - r['debe'], 2) if r['haber'] > r['debe'] else 0.0, axis=1)
        df_may.rename(columns={'codigo_cuenta': 'Código', 'nombre_cuenta': 'Cuenta', 'debe': 'Debe Acumulado (Q)', 'haber': 'Haber Acumulado (Q)'}, inplace=True)
        st.dataframe(df_may, use_container_width=True)
    else:
        st.info("Sin partidas para mayorizar.")

# ==========================================
# 14. BALANCE GENERAL (SALDOS)
# ==========================================
elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title(f"⚖️ Balance General de Saldos — {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_anios_b = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_b = df_anios_b['anio'].dropna().tolist() if not df_anios_b.empty else [str(datetime.now().year)]

    col_b1, col_b2, col_b3 = st.columns([2, 1.5, 1])
    modo_b = col_b1.radio("Visualizar Balance por:", ["📅 Por Mes", "📆 Por Año", "🌐 Saldo Acumulado (Mes a Mes)"], horizontal=True, key="bal_modo_rad")
    anio_b = col_b2.selectbox("Año Fiscal:", anios_b, key="bal_anio_sel")
    col_b3.color_picker("Color del Año:", value="#1E3A8A", key="bal_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_b == "🌐 Saldo Acumulado (Mes a Mes)":
        q_b_acum = """
            SELECT d.codigo_cuenta AS "Código", d.nombre_cuenta AS "Cuenta",
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_raw_b = pd.read_sql_query(q_b_acum, conn, params=(str(selected_emp_id), str(anio_b)))
        conn.close()

        if not df_raw_b.empty:
            df_raw_b['neto'] = df_raw_b['tot_debe'] - df_raw_b['tot_haber']
            pivot_b = df_raw_b.pivot_table(index=['Código', 'Cuenta'], columns='mes', values='neto', aggfunc='sum', fill_value=0.0).reset_index()
            for m_num, m_nom in meses_nombres.items():
                if m_num not in pivot_b.columns:
                    pivot_b[m_num] = 0.0
            cols_ord = [m for m in sorted(meses_nombres.keys())]
            pivot_b['TOTAL ANUAL ACUMULADO (Q)'] = pivot_b[cols_ord].sum(axis=1)
            pivot_b.rename(columns=meses_nombres, inplace=True)
            st.dataframe(pivot_b, use_container_width=True)

            b_xls = io.BytesIO()
            pivot_b.to_excel(b_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance Acumulado en Excel (.xlsx)", b_xls.getvalue(), f"Balance_Acumulado_{anio_b}.xlsx")
        else:
            st.info("Sin registros en este ejercicio fiscal.")
    else:
        cond_b = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_b = [str(selected_emp_id), str(anio_b)]
        if modo_b == "📅 Por Mes":
            mes_b = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="b_mes_sel")
            cond_b += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_b.append(mes_b)

        q_b = f"""
            SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
                   SUM(debe) AS "Total Debe (Q)", SUM(haber) AS "Total Haber (Q)"
            FROM libro_diario d
            WHERE {cond_b}
            GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta
        """
        df_b = pd.read_sql_query(q_b, conn, params=tuple(params_b))
        conn.close()

        if not df_b.empty:
            df_b['Saldo Deudor (Q)'] = df_b.apply(lambda r: round(r['Total Debe (Q)'] - r['Total Haber (Q)'], 2) if r['Total Debe (Q)'] >= r['Total Haber (Q)'] else 0.0, axis=1)
            df_b['Saldo Acreedor (Q)'] = df_b.apply(lambda r: round(r['Total Haber (Q)'] - r['Total Debe (Q)'], 2) if r['Total Haber (Q)'] > r['Total Debe (Q)'] else 0.0, axis=1)
            st.dataframe(df_b, use_container_width=True)

            c1, c2 = st.columns(2)
            c1.metric("Sumas Deudoras", f"Q {df_b['Saldo Deudor (Q)'].sum():,.2f}")
            c2.metric("Sumas Acreedoras", f"Q {df_b['Saldo Acreedor (Q)'].sum():,.2f}")

            b_xls2 = io.BytesIO()
            df_b.to_excel(b_xls2, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance en Excel (.xlsx)", b_xls2.getvalue(), f"Balance_{anio_b}.xlsx")
        else:
            st.info("Sin registros en el período seleccionado.")

# ==========================================
# 15. BALANCE DE SITUACIÓN FINANCIERA (CLASIFICADO CON GANANCIA)
# ==========================================
elif selected_menu == "🏛️ Balance Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title(f"🏛️ Balance de Situación Financiera — {selected_empresa_info[1]}")
    st.caption(f"Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios_s = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_s = df_anios_s['anio'].dropna().tolist() if not df_anios_s.empty else [str(datetime.now().year)]

    col_s1, col_s2, col_s3 = st.columns([2, 1.5, 1])
    modo_sit = col_s1.radio("Visualizar Situación Financiera por:", ["📅 Por Mes", "📆 Por Año", "🌐 Saldo Acumulado (Mes a Mes)"], horizontal=True, key="sit_modo_rad")
    anio_sit = col_s2.selectbox("Año Fiscal:", anios_s, key="sit_anio_sel")
    col_s3.color_picker("Color de Cabecera:", value="#1E3A8A", key="sit_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_sit == "🌐 Saldo Acumulado (Mes a Mes)":
        q_sit_acum = """
            SELECT d.codigo_cuenta, d.nombre_cuenta, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_sit_raw = pd.read_sql_query(q_sit_acum, conn, params=(str(selected_emp_id), str(anio_sit)))
        conn.close()

        if not df_sit_raw.empty:
            # Calcular saldo natural de cada cuenta por mes
            df_sit_raw['saldo'] = df_sit_raw.apply(
                lambda r: (r['tot_debe'] - r['tot_haber']) if r['tipo'] == 'Activo' else (r['tot_haber'] - r['tot_debe']), axis=1
            )
            # Matriz horizontal
            piv_sit = df_sit_raw[df_sit_raw['tipo'].isin(['Activo', 'Pasivo', 'Capital'])].pivot_table(
                index=['codigo_cuenta', 'nombre_cuenta', 'tipo'], columns='mes', values='saldo', aggfunc='sum', fill_value=0.0
            ).reset_index()
            for m_num in sorted(meses_nombres.keys()):
                if m_num not in piv_sit.columns: piv_sit[m_num] = 0.0

            cols_m = sorted(meses_nombres.keys())
            piv_sit['TOTAL ANUAL ACUMULADO (Q)'] = piv_sit[cols_m].sum(axis=1)

            # Inyectar fila de Ganancia Neta mes a mes para que cuadre exactamente
            fila_ganancia = {"codigo_cuenta": "3.2.01.01", "nombre_cuenta": "Ganancia Neta del Ejercicio (Después de ISR)", "tipo": "Capital"}
            for m_k in cols_m:
                sub_m = df_sit_raw[df_sit_raw['mes'] == m_k]
                ing_m = sub_m[sub_m['tipo'] == 'Ingreso']['saldo'].sum()
                gas_m = sub_m[sub_m['tipo'] == 'Gasto']['saldo'].sum()
                ut_m = ing_m - gas_m
                imp_m, gan_m, _ = calcular_impuesto_renta(selected_empresa_info[3], ing_m, ut_m)
                fila_ganancia[m_k] = gan_m
            fila_ganancia['TOTAL ANUAL ACUMULADO (Q)'] = sum([fila_ganancia[m] for m in cols_m])

            piv_sit = pd.concat([piv_sit, pd.DataFrame([fila_ganancia])], ignore_index=True)
            piv_sit.rename(columns={'codigo_cuenta': 'Código', 'nombre_cuenta': 'Rubro / Cuenta', 'tipo': 'Clasificación'}, inplace=True)
            piv_sit.rename(columns=meses_nombres, inplace=True)
            st.dataframe(piv_sit, use_container_width=True)

            s_xls = io.BytesIO()
            piv_sit.to_excel(s_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance de Situación en Excel (.xlsx)", s_xls.getvalue(), f"Situacion_Financiera_{anio_sit}.xlsx")
        else:
            st.info("Sin registros en este ejercicio fiscal.")
    else:
        cond_sit = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_sit = [str(selected_emp_id), str(anio_sit)]
        periodo_label = f"Año Fiscal {anio_sit}"
        if modo_sit == "📅 Por Mes":
            mes_sit = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="sit_mes_sel")
            cond_sit += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_sit.append(mes_sit)
            periodo_label = f"{meses_nombres[mes_sit]} {anio_sit}"

        q_s_rep = f"""
            SELECT n.codigo, n.nombre, n.tipo,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE {cond_sit}
            GROUP BY n.codigo, n.nombre, n.tipo
            ORDER BY n.codigo ASC
        """
        df_s_rep = pd.read_sql_query(q_s_rep, conn, params=tuple(params_sit))
        conn.close()

        if not df_s_rep.empty:
            df_s_rep['Saldo'] = df_s_rep.apply(
                lambda r: (r['tot_debe'] - r['tot_haber']) if r['tipo'] == 'Activo' else (r['tot_haber'] - r['tot_debe']), axis=1
            )
            # Cálculo de ganancia
            tot_ing = df_s_rep[df_s_rep['tipo'] == 'Ingreso']['Saldo'].sum()
            tot_gas = df_s_rep[df_s_rep['tipo'] == 'Gasto']['Saldo'].sum()
            ut_ant = tot_ing - tot_gas
            m_imp, ganancia_neta_sit, tasa_txt = calcular_impuesto_renta(selected_empresa_info[3], tot_ing, ut_ant)

            activos = df_s_rep[df_s_rep['tipo'] == 'Activo']
            pasivos = df_s_rep[df_s_rep['tipo'] == 'Pasivo']
            capital = df_s_rep[df_s_rep['tipo'] == 'Capital']

            tot_act = float(activos['Saldo'].sum())
            tot_pas = float(pasivos['Saldo'].sum()) + m_imp
            tot_pat = float(capital['Saldo'].sum()) + ganancia_neta_sit
            tot_pas_pat = tot_pas + tot_pat

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total ACTIVO", f"Q {tot_act:,.2f}")
            c2.metric("Total PASIVO", f"Q {tot_pas:,.2f}")
            c3.metric("Total PATRIMONIO (con Ganancia)", f"Q {tot_pat:,.2f}")
            diff_cuad = round(tot_act - tot_pas_pat, 2)
            c4.metric("Diferencia de Cuadratura", f"Q {diff_cuad:,.2f}", delta="Cuadrado ✅" if abs(diff_cuad) < 0.05 else "Revisar ⚠️")

            st.markdown("---")
            filas_sit_clas = []
            for _, r in activos.iterrows():
                filas_sit_clas.append({"Clasificación": "ACTIVO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            for _, r in pasivos.iterrows():
                filas_sit_clas.append({"Clasificación": "PASIVO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            if m_imp > 0:
                filas_sit_clas.append({"Clasificación": "PASIVO", "Cuenta": f"2.1.03.99 - Provisión ISR por Pagar ({tasa_txt})", "Monto (Q)": m_imp})
            for _, r in capital.iterrows():
                filas_sit_clas.append({"Clasificación": "PATRIMONIO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            filas_sit_clas.append({"Clasificación": "PATRIMONIO", "Cuenta": "3.2.01.01 - Ganancia Neta del Ejercicio", "Monto (Q)": ganancia_neta_sit})

            df_sit_final = pd.DataFrame(filas_sit_clas)
            st.dataframe(df_sit_final, use_container_width=True)

            col_ds1, col_ds2 = st.columns(2)
            s_xls2 = io.BytesIO()
            df_sit_final.to_excel(s_xls2, index=False, engine='openpyxl')
            col_ds1.download_button("📥 Descargar Balance Situación en Excel (.xlsx)", s_xls2.getvalue(), f"Situacion_Financiera_{periodo_label}.xlsx")
            pdf_s = exportar_reporte_pdf(df_sit_final, f"Balance de Situación Financiera - {periodo_label}", selected_empresa_info[1], selected_empresa_info[2])
            col_ds2.download_button("📄 Descargar Balance Situación en PDF", pdf_s, f"Situacion_Financiera_{periodo_label}.pdf", "application/pdf")
        else:
            st.info("Sin registros en este período.")

# ==========================================
# 16. ESTADO DE RESULTADOS (OFICIAL GUATEMALA)
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title(f"📉 Estado de Resultados Oficial — {selected_empresa_info[1]}")
    st.caption(f"Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios_er = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_er = df_anios_er['anio'].dropna().tolist() if not df_anios_er.empty else [str(datetime.now().year)]

    col_e1, col_e2, col_e3 = st.columns([2, 1.5, 1])
    modo_er = col_e1.radio("Visualizar Estado de Resultados por:", ["📅 Mes Específico", "📆 Año Completo", "🌐 Histórico Acumulado (Mes a Mes)"], horizontal=True, key="er_modo_rad")
    anio_er = col_e2.selectbox("Año Fiscal:", anios_er, key="er_anio_sel")
    col_e3.color_picker("Color de Cabecera:", value="#1E3A8A", key="er_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_er == "🌐 Histórico Acumulado (Mes a Mes)":
        q_er_acum = """
            SELECT d.codigo_cuenta, d.nombre_cuenta, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS total_mov
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND n.tipo IN ('Ingreso', 'Gasto')
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_er_raw = pd.read_sql_query(q_er_acum, conn, params=(str(selected_emp_id), str(anio_er)))
        conn.close()

        if not df_er_raw.empty:
            cols_meses_keys = sorted(meses_nombres.keys())
            
            # Pivot table estructurada de arriba hacia abajo
            # 1. Ingresos
            df_i = df_er_raw[df_er_raw['tipo'] == 'Ingreso'].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()
            # 2. Costo de ventas (5.1.01)
            df_c = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (df_er_raw['codigo_cuenta'].str.startswith('5.1.01'))].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()
            # 3. Gastos operativos
            df_g = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (~df_er_raw['codigo_cuenta'].str.startswith('5.1.01'))].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()

            filas_er_matriz = []
            
            # Función auxiliar para renglones
            def agregar_bloque(df_sub, nombre_bloque):
                if not df_sub.empty:
                    for m in cols_meses_keys:
                        if m not in df_sub.columns: df_sub[m] = 0.0
                    for _, r in df_sub.iterrows():
                        fila = {"Rubro Contable": f"{r['codigo_cuenta']} - {r['nombre_cuenta']}", "Sección": nombre_bloque}
                        for m in cols_meses_keys: fila[m] = float(r[m])
                        fila['TOTAL ACUMULADO (Q)'] = sum([fila[m] for m in cols_meses_keys])
                        filas_er_matriz.append(fila)

            # Renglones formales
            agregar_bloque(df_i, "(+) INGRESOS OPERACIONALES")
            agregar_bloque(df_c, "(-) COSTO DE VENTAS")
            agregar_bloque(df_g, "(-) GASTOS OPERATIVOS")

            # Totales calculados mes a mes
            fila_ing_tot = {"Rubro Contable": "TOTAL INGRESOS BRUTOS", "Sección": "RESUMEN"}
            fila_cost_tot = {"Rubro Contable": "TOTAL COSTO DE VENTAS", "Sección": "RESUMEN"}
            fila_bruta = {"Rubro Contable": "(=) UTILIDAD BRUTA EN VENTAS", "Sección": "RESUMEN"}
            fila_gas_tot = {"Rubro Contable": "TOTAL GASTOS OPERATIVOS", "Sección": "RESUMEN"}
            fila_op = {"Rubro Contable": "(=) UTILIDAD DE OPERACIÓN", "Sección": "RESUMEN"}
            fila_isr = {"Rubro Contable": f"(-) PROVISIÓN ISR ({selected_empresa_info[3]})", "Sección": "IMPUESTO"}
            fila_neta = {"Rubro Contable": "(=) UTILIDAD NETA DEL EJERCICIO", "Sección": "RESULTADO NETO"}

            for m in cols_meses_keys:
                sub_m = df_er_raw[df_er_raw['mes'] == m]
                ing_m = sub_m[sub_m['tipo'] == 'Ingreso']['total_mov'].sum()
                cost_m = sub_m[(sub_m['tipo'] == 'Gasto') & (sub_m['codigo_cuenta'].str.startswith('5.1.01'))]['total_mov'].sum()
                gas_m = sub_m[(sub_m['tipo'] == 'Gasto') & (~sub_m['codigo_cuenta'].str.startswith('5.1.01'))]['total_mov'].sum()
                
                bruta_m = ing_m - cost_m
                op_m = bruta_m - gas_m
                imp_m, net_m, _ = calcular_impuesto_renta(selected_empresa_info[3], ing_m, op_m)

                fila_ing_tot[m] = ing_m
                fila_cost_tot[m] = cost_m
                fila_bruta[m] = bruta_m
                fila_gas_tot[m] = gas_m
                fila_op[m] = op_m
                fila_isr[m] = imp_m
                fila_neta[m] = net_m

            for item in [fila_ing_tot, fila_cost_tot, fila_bruta, fila_gas_tot, fila_op, fila_isr, fila_neta]:
                item['TOTAL ACUMULADO (Q)'] = sum([item[m] for m in cols_meses_keys])
                filas_er_matriz.append(item)

            df_er_display = pd.DataFrame(filas_er_matriz)
            df_er_display.rename(columns=meses_nombres, inplace=True)
            st.dataframe(df_er_display, use_container_width=True)

            er_xls = io.BytesIO()
            df_er_display.to_excel(er_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Estado de Resultados Matricial en Excel (.xlsx)", er_xls.getvalue(), f"Estado_Resultados_Acumulado_{anio_er}.xlsx")
        else:
            st.info("Sin operaciones registradas en este ejercicio fiscal.")
    else:
        cond_er = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_er = [str(selected_emp_id), str(anio_er)]
        lbl_periodo = f"Año Fiscal Completo {anio_er}"
        if modo_er == "📅 Mes Específico":
            mes_er = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="er_mes_sel")
            cond_er += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_er.append(mes_er)
            lbl_periodo = f"{meses_nombres[mes_er]} {anio_er}"

        q_er_p = f"""
            SELECT d.codigo_cuenta AS "Código", d.nombre_cuenta AS "Cuenta", n.tipo,
                   SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS "Monto (Q)"
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE {cond_er} AND n.tipo IN ('Ingreso', 'Gasto')
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo
            ORDER BY d.codigo_cuenta
        """
        df_er_p = pd.read_sql_query(q_er_p, conn, params=tuple(params_er))
        conn.close()

        if not df_er_p.empty:
            tot_i = df_er_p[df_er_p['tipo'] == 'Ingreso']['Monto (Q)'].sum()
            tot_c = df_er_p[(df_er_p['tipo'] == 'Gasto') & (df_er_p['Código'].str.startswith('5.1.01'))]['Monto (Q)'].sum()
            tot_g = df_er_p[(df_er_p['tipo'] == 'Gasto') & (~df_er_p['Código'].str.startswith('5.1.01'))]['Monto (Q)'].sum()

            utilidad_bruta = tot_i - tot_c
            utilidad_op = utilidad_bruta - tot_g
            impuesto, ganancia_neta, tasa_desc = calcular_impuesto_renta(selected_empresa_info[3], tot_i, utilidad_op)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("(+) Ingresos Operacionales", f"Q {tot_i:,.2f}")
            c2.metric("(=) Utilidad Bruta", f"Q {utilidad_bruta:,.2f}")
            c3.metric(f"(-) Provisión ISR ({tasa_desc})", f"Q {impuesto:,.2f}")
            c4.metric("(=) Ganancia Neta Ejercicio", f"Q {ganancia_neta:,.2f}", delta="Ganancia" if ganancia_neta >= 0 else "Pérdida")

            st.markdown("---")
            filas_verticales = [
                {"Estructura": "INGRESOS", "Concepto": "Total Ingresos Operacionales", "Monto (Q)": tot_i},
                {"Estructura": "COSTOS", "Concepto": "(-) Costo de Ventas", "Monto (Q)": tot_c},
                {"Estructura": "MARGEN", "Concepto": "(=) Utilidad Bruta en Ventas", "Monto (Q)": utilidad_bruta},
                {"Estructura": "GASTOS", "Concepto": "(-) Gastos de Operación y Administración", "Monto (Q)": tot_g},
                {"Estructura": "OPERACIÓN", "Concepto": "(=) Utilidad en Operación", "Monto (Q)": utilidad_op},
                {"Estructura": "IMPUESTOS", "Concepto": f"(-) Provisión ISR ({tasa_desc})", "Monto (Q)": impuesto},
                {"Estructura": "RESULTADO NETO", "Concepto": "(=) Utilidad Neta del Ejercicio", "Monto (Q)": ganancia_neta}
            ]
            df_er_vert = pd.DataFrame(filas_verticales)
            st.dataframe(df_er_vert, use_container_width=True)

            col_de1, col_de2 = st.columns(2)
            er_xls2 = io.BytesIO()
            df_er_vert.to_excel(er_xls2, index=False, engine='openpyxl')
            col_de1.download_button("📥 Descargar Estado de Resultados en Excel (.xlsx)", er_xls2.getvalue(), f"Estado_Resultados_{lbl_periodo}.xlsx")
            pdf_er = exportar_reporte_pdf(df_er_vert, f"Estado de Resultados Oficial - {lbl_periodo}", selected_empresa_info[1], selected_empresa_info[2])
            col_de2.download_button("📄 Descargar Estado de Resultados en PDF", pdf_er, f"Estado_Resultados_{lbl_periodo}.pdf", "application/pdf")
        else:
            st.info("Sin operaciones registradas en el período.")

# ==========================================
# 17. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo, nombre, tipo FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(str(selected_emp_id),))
    if not df_nom.empty:
        df_nom = df_nom.rename(columns={
            'codigo': 'Código',
            'nombre': 'Nombre de Cuenta',
            'tipo': 'Tipo'
        })
    conn.close()

    if not df_nom.empty:
        st.dataframe(df_nom, use_container_width=True)

    with st.expander("➕ Crear Nueva Cuenta Contable"):
        with st.form("form_nueva_cta"):
            c1, c2, c3 = st.columns(3)
            cod_c = c1.text_input("Código (ej: 4.1.01.04)")
            nom_c = c2.text_input("Nombre de Cuenta")
            tip_c = c3.selectbox("Clasificación:", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if cod_c.strip() and nom_c.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)",
                              (str(selected_emp_id), cod_c.strip(), nom_c.strip(), tip_c))
                    conn.commit(); conn.close()
                    st.success("Cuenta agregada exitosamente."); st.rerun()

# ==========================================
# 18. CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👤 Control de Usuarios (Admin)":
    if not is_admin: st.error("Acceso restringido."); st.stop()
    st.title("👤 Control de Usuarios del Sistema")
    conn = get_db_connection()
    df_users = pd.read_sql_query("SELECT id, username, name, email, role, status, assigned_empresas FROM users ORDER BY name", conn)
    if not df_users.empty:
        df_users = df_users.rename(columns={
            'username': 'Usuario',
            'name': 'Nombre',
            'email': 'Correo',
            'role': 'Rol',
            'status': 'Estado',
            'assigned_empresas': 'Empresas Asignadas'
        })
    conn.close()
    st.dataframe(df_users, use_container_width=True)

# ==========================================
# 19. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.error("Acceso restringido."); st.stop()
    st.title("🏢 Gestión de Empresas")
    conn = get_db_connection()
    df_e = pd.read_sql_query("SELECT id, nombre, nit, regimen_isr FROM empresas ORDER BY nombre", conn)
    if not df_e.empty:
        df_e = df_e.rename(columns={
            'nombre': 'Razón Social',
            'nit': 'NIT',
            'regimen_isr': 'Régimen ISR'
        })
    conn.close()

    if not df_e.empty:
        st.dataframe(df_e, use_container_width=True)

    with st.expander("➕ Registrar Nueva Empresa"):
        with st.form("form_reg_empresa"):
            c1, c2, c3 = st.columns(3)
            nom_emp = c1.text_input("Nombre o Razón Social *")
            nit_emp = c2.text_input("NIT *")
            reg_emp = c3.selectbox("Régimen Fiscal:", ["Pequeño Contribuyente (5%)", "Opcional Simplificado (5% / 7%)", "General sobre Utilidades (25%)"])
            if st.form_submit_button("Guardar Empresa", type="primary"):
                if nom_emp.strip() and nit_emp.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    emp_uid = f"emp-{int(datetime.now().timestamp())}"
                    c.execute("""
                        INSERT INTO empresas (id, nombre, nit, direccion, telefono, regimen_isr, actividad_economica, created_at)
                        VALUES (%s, %s, %s, 'Guatemala', '', %s, 'General', %s)
                    """, (emp_uid, nom_emp.strip(), nit_emp.strip().upper(), reg_emp, datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    sembrar_nomenclatura_inicial(emp_uid)
                    st.success("Empresa creada y configurada exitosamente."); st.rerun()