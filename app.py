import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import io
import re
from datetime import datetime, date

# ReportLab para exportación PDF oficial SAT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

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
        st.error("No se encontró DATABASE_URL en los Secrets de Streamlit.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def inicializar_tablas_sistema():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS despensa_articulos (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL UNIQUE,
                categoria TEXT DEFAULT 'Cocina',
                unidad_medida TEXT DEFAULT 'Unidades',
                stock_actual NUMERIC DEFAULT 0,
                stock_minimo NUMERIC DEFAULT 1,
                created_at TEXT
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS despensa_movimientos (
                id TEXT PRIMARY KEY,
                articulo_id TEXT NOT NULL,
                tipo TEXT NOT NULL,
                cantidad NUMERIC NOT NULL,
                fecha DATE NOT NULL,
                registrado_por TEXT,
                detalle TEXT,
                created_at TEXT,
                FOREIGN KEY (articulo_id) REFERENCES despensa_articulos(id)
            );
        """)
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS terceros (
                id SERIAL PRIMARY KEY,
                empresa_id TEXT,
                tipo TEXT,
                nit TEXT UNIQUE,
                nombre TEXT,
                direccion TEXT,
                telefono TEXT,
                dias_credito INT DEFAULT 0
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS activos_fijos (
                id TEXT PRIMARY KEY,
                empresa_id TEXT NOT NULL,
                factura_id TEXT,
                codigo_activo TEXT,
                descripcion TEXT NOT NULL,
                categoria TEXT NOT NULL,
                fecha_adquisicion DATE NOT NULL,
                costo_adquisicion NUMERIC NOT NULL,
                created_at TEXT
            );
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass

inicializar_tablas_sistema()

def sembrar_nomenclatura_inicial(empresa_id):
    try:
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
                ("2.1.01.02", "Tarjetas de Crédito Corporativas por Pagar", "Pasivo"),
                ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
                ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
                ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
                ("3.1.01.01", "Capital Social / Individual", "Capital"),
                ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
                ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
                ("4.1.01.03", "Servicios de Transporte y Fletes", "Ingreso"),
                ("5.1.01.01", "Compras de Mercadería (Costo de Ventas)", "Costo"),
                ("5.1.02.01", "Sueldos y Prestaciones", "Gasto"),
                ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
                ("5.1.02.03", "Alquileres", "Gasto"),
                ("5.1.02.04", "Combustibles y Lubricantes", "Gasto"),
                ("5.1.02.99", "Gastos Generales", "Gasto"),
            ]
            c.executemany("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                          [(empresa_id, cod, nom, t) for cod, nom, t in base])
            conn.commit()
        conn.close()
    except Exception:
        pass

# ==========================================
# EXPORTACIÓN OFICIAL (PDF CON FOLIO Y EXCEL)
# ==========================================
def exportar_libro_pdf(df_datos, titulo_libro, empresa_nom, empresa_nit, resolucion_sat="2026-5-12345-001", folio_inicio=1):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter) if len(df_datos.columns) > 5 else letter,
        leftMargin=25, rightMargin=25, topMargin=35, bottomMargin=35
    )
    elementos = []
    estilos = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)
    
    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo_libro.upper()}</b>", estilo_sub))
    if resolucion_sat:
        elementos.append(Paragraph(f"Resolución SAT No.: {resolucion_sat}", estilo_sub))
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
    
    def agregar_pie(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        folio_actual = folio_inicio + canvas._pageNumber - 1
        pie_txt = f"Folio No. {folio_actual}  |  Resolución SAT: {resolucion_sat}"
        canvas.drawRightString(760 if len(df_datos.columns) > 5 else 580, 20, pie_txt)
        canvas.restoreState()

    doc.build(elementos, onFirstPage=agregar_pie, onLaterPages=agregar_pie)
    buffer.seek(0)
    return buffer

def exportar_reporte_excel(df_datos, nombre_hoja="Reporte"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_datos.to_excel(writer, sheet_name=nombre_hoja, index=False)
    buffer.seek(0)
    return buffer

# ==========================================
# LECTOR EXACTO DE FACTURAS PDF (FEL SAT)
# ==========================================
def extraer_datos_dte_sat(archivo_pdf):
    if not PdfReader:
        return {}
    lector = PdfReader(archivo_pdf)
    texto = ""
    for pagina in lector.pages:
        t = pagina.extract_text()
        if t:
            texto += t + "\n"
            
    datos = {
        "numero_dte": "",
        "serie": "",
        "fecha": date.today(),
        "emisor_nit": "",
        "emisor_nombre": "",
        "receptor_nit": "",
        "receptor_nombre": "",
        "total": 0.0,
        "subtotal": 0.0,
        "es_pequeno": False
    }
    
    if re.search(r'Pequeño\s+Contribuyente|No\s+genera\s+derecho\s+a\s+cr[eé]dito\s+fiscal', texto, re.I):
        datos["es_pequeno"] = True

    # Número de Autorización (UUID con o sin salto de línea)
    m_aut = re.search(r'(?:NÚMERO\s+DE\s+AUTORIZACI[OÓ]N|Autorizaci[oó]n|UUID)[\s:\n\r]+([A-F0-9\-]{32,36})', texto, re.I)
    if m_aut:
        datos["numero_dte"] = m_aut.group(1).strip()

    # Serie
    m_ser = re.search(r'(?:SERIE|Serie)[\s:\n\r]+([A-F0-9]+)', texto)
    if m_ser:
        datos["serie"] = m_ser.group(1).strip()

    # Número DTE
    m_dte_num = re.search(r'(?:N[uú]mero\s+de\s+DTE|N[UÚ]MERO\s+DTE)[\s:\n\r]+([0-9]+)', texto, re.I)
    if m_dte_num and not datos["numero_dte"]:
        datos["numero_dte"] = m_dte_num.group(1).strip()

    # Fecha de Emisión
    meses = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    m_fec_txt = re.search(r'Fecha(?:\s+y\s+hora)?\s+de\s+emisi[oó]n[\s:\n\r]+(\d{1,2})-([a-zA-Z]{3})-(\d{4})', texto, re.I)
    if m_fec_txt:
        d, mes_str, y = m_fec_txt.groups()
        mes_num = meses.get(mes_str.lower()[:3], 1)
        try:
            datos["fecha"] = date(int(y), mes_num, int(d))
        except Exception:
            pass
    else:
        m_fec = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', texto)
        if m_fec:
            f_str = m_fec.group(1)
            try:
                if "-" in f_str:
                    datos["fecha"] = datetime.strptime(f_str, "%Y-%m-%d").date()
                else:
                    datos["fecha"] = datetime.strptime(f_str, "%d/%m/%Y").date()
            except Exception:
                pass

    # Emisor (Proveedor en compras)
    m_nem = re.search(r'NIT\s+Emisor[\s:\n\r]+([0-9Kk\-]+)', texto, re.I)
    if m_nem:
        datos["emisor_nit"] = m_nem.group(1).replace("-", "").strip().upper()

    m_nomem = re.search(r'Nombre\s*(?:del)?\s*Emisor[\s:\n\r]+([^\n\r]+)', texto, re.I)
    if m_nomem:
        datos["emisor_nombre"] = m_nomem.group(1).strip()
    else:
        m_sub = re.search(r'NIT\s+Emisor[\s:\n\r]+[0-9Kk\-]+[\s\n\r]+([^\n\r]+)', texto, re.I)
        if m_sub:
            datos["emisor_nombre"] = m_sub.group(1).strip()

    # Receptor (Cliente en ventas)
    m_nrec = re.search(r'NIT\s+Receptor[\s:\n\r]+([0-9Kk\-]+|CF|C/?F)', texto, re.I)
    if m_nrec:
        r_val = m_nrec.group(1).replace("-", "").strip().upper()
        datos["receptor_nit"] = "CF" if r_val in ["CF", "C/F"] else r_val

    m_nomrec = re.search(r'Nombre\s*(?:del)?\s*Receptor[\s:\n\r]+([^\n\r]+)', texto, re.I)
    if m_nomrec:
        datos["receptor_nombre"] = m_nomrec.group(1).strip()
    elif datos["receptor_nit"] == "CF":
        datos["receptor_nombre"] = "CONSUMIDOR FINAL"
    else:
        m_sub2 = re.search(r'NIT\s+Receptor[\s:\n\r]+[0-9Kk\-]+[\s\n\r]+([^\n\r]+)', texto, re.I)
        if m_sub2:
            datos["receptor_nombre"] = m_sub2.group(1).strip()

    # Total de la Factura
    montos = re.findall(r'(?:TOTAL(?:ES)?|Gran\s+Total|Total\s+General)[\s:\n\r]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos:
        m_val = float(montos[-1].replace(",", ""))
        datos["total"] = m_val
        datos["subtotal"] = round(m_val / 1.12, 2)
        
    return datos

# ==========================================
# GESTIÓN DE ACCESO Y AUTENTICACIÓN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP Corporativo en la Nube</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.6, 1])
    with c2:
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            user_clean = u.strip().lower()
            pass_clean = p.strip()
            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = %s", (user_clean,))
            row = c.fetchone()
            
            claves_admin_validas = ["Matgozmaster2026JGK", "admin123", "Matgozmaster2026#"]
            
            if row:
                hash_guardado = row[7]
                es_valido = (hash_guardado == hash_password(pass_clean)) or (user_clean == "admin" and pass_clean in claves_admin_validas)
                if es_valido:
                    if user_clean == "admin" and hash_guardado != hash_password(pass_clean):
                        try:
                            c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(pass_clean), row[0]))
                            conn.commit()
                        except Exception:
                            pass
                    conn.close()
                    if row[5] == "pending_approval":
                        st.warning("Cuenta pendiente de autorización.")
                    elif row[5] == "suspended":
                        st.error("Cuenta suspendida.")
                    else:
                        st.session_state.user = {
                            "id": row[0], "username": row[1], "name": row[2],
                            "email": row[3], "role": row[4], "assigned_empresas": row[6]
                        }
                        st.rerun()
                else:
                    conn.close()
                    st.error("Credenciales incorrectas.")
            else:
                if user_clean == "admin" and pass_clean in claves_admin_validas:
                    try:
                        admin_id = "usr-admin"
                        c.execute("""
                            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at, reset_requested)
                            VALUES (%s, 'admin', %s, 'Administrador Principal', 'admin@matgoz.com', 'admin', 'active', 'all', %s, 0)
                            ON CONFLICT (username) DO UPDATE SET password_hash = %s, status = 'active'
                        """, (admin_id, hash_password(pass_clean), datetime.now().isoformat(), hash_password(pass_clean)))
                        conn.commit()
                        conn.close()
                        st.session_state.user = {
                            "id": admin_id, "username": "admin", "name": "Administrador Principal",
                            "email": "admin@matgoz.com", "role": "admin", "assigned_empresas": "all"
                        }
                        st.rerun()
                    except Exception as e_login:
                        conn.close()
                        st.error(f"Error de acceso: {e_login}")
                else:
                    conn.close()
                    st.error("Credenciales incorrectas.")

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
    ids = [x.strip() for x in curr_u["assigned_empresas"].split(",") if x.strip()]
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
        menu_options.extend(["👥 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])
    selected_menu = st.radio("Navegación:", menu_options)

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id:
        st.info("Para comenzar, registra o selecciona una empresa en la barra lateral.")
        st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen Fiscal: {selected_empresa_info[3]}")
    
    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT * FROM facturas WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    c_cur = conn.cursor()
    c_cur.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,))
    cxp_tot = c_cur.fetchone()[0] or 0.0
    c_cur.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,))
    cxc_tot = c_cur.fetchone()[0] or 0.0
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🛒 Compras Registradas", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Ventas Registradas", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c3.metric("💳 Cuentas por Pagar (CXP)", f"Q {float(cxp_tot):,.2f}")
    c4.metric("💰 Cuentas por Cobrar (CXC)", f"Q {float(cxc_tot):,.2f}")

# ==========================================
# 2. FACTURA DE VENTAS (MONTO TOTAL, DTE, EDICIÓN Y ELIMINACIÓN)
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title("📈 Factura de Ventas Emitidas")

    tab_v_emitir, tab_v_editar = st.tabs(["➕ Emitir Factura de Venta", "✏️ Modificar / Corregir Factura"])

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT nit, nombre FROM terceros WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre")
    clientes_corp = c.fetchall()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_debe = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    dict_cli = {f"{cl[1]} (NIT: {cl[0]})": cl for cl in clientes_corp}
    opc_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    opc_debe_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_debe.iterrows()]

    with tab_v_emitir:
        st.markdown("### 📥 Carga Inteligente de Factura Electrónica FEL (PDF SAT)")
        dte_cargado_v = st.file_uploader("Sube el PDF emitido por SAT (FEL)", type=["pdf"], key="pdf_venta")
        
        pre_v = {}
        if dte_cargado_v:
            try:
                pre_v = extraer_datos_dte_sat(dte_cargado_v)
                st.success("✅ Datos del DTE extraídos exitosamente.")
            except Exception:
                st.warning("No se pudieron leer automáticamente todos los campos.")

        # Buscador interactivo exterior de clientes
        st.markdown("#### 🔍 Buscador de Cliente Corporativo")
        cli_seleccionado_busc = st.selectbox(
            "Buscar cliente por Nombre o NIT:",
            ["-- Seleccionar o escribir nuevo --"] + list(dict_cli.keys()),
            key="busc_cli_v"
        )
        
        nit_precargado = ""
        nom_precargado = ""
        if cli_seleccionado_busc != "-- Seleccionar o escribir nuevo --":
            nit_precargado = dict_cli[cli_seleccionado_busc][0]
            nom_precargado = dict_cli[cli_seleccionado_busc][1]
        elif pre_v.get("receptor_nit"):
            nit_precargado = pre_v.get("receptor_nit")
            nom_precargado = pre_v.get("receptor_nombre")

        # Alerta si no está en el directorio
        if nit_precargado and nit_precargado not in ["CF", "C/F"]:
            if not any(cl[0].upper() == nit_precargado.upper() for cl in clientes_corp):
                st.warning(f"⚠️ El cliente con NIT **{nit_precargado}** no se encuentra en el directorio corporativo.")

        fk_v = dte_cargado_v.name if dte_cargado_v else f"v_{nit_precargado}"

        with st.form("form_emitir_venta", clear_on_submit=False):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / DTE Emitido *", value=pre_v.get("numero_dte", ""), key=f"v_num_{fk_v}")
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""), key=f"v_ser_{fk_v}")
            fecha_fac = c3.date_input("Fecha de Emisión *", value=pre_v.get("fecha") or date.today(), key=f"v_fec_{fk_v}")

            c4, c5 = st.columns(2)
            cli_nit = c4.text_input("NIT Cliente *", value=nit_precargado, key=f"v_cnit_{fk_v}")
            cli_nom = c5.text_input("Nombre / Razón Social *", value=nom_precargado, key=f"v_cnom_{fk_v}")

            cv1, cv2 = st.columns(2)
            cond_v = cv1.selectbox("Condición de Venta:", ["Contado", "Crédito"], key=f"v_cond_{fk_v}")
            if cond_v == "Contado":
                metodo_v = cv2.selectbox("Medio de Cobro:", ["Efectivo", "Depósito Bancario", "Tarjeta de Crédito / POS"], key=f"v_met_{fk_v}")
                idx_dv = next((i for i, val in enumerate(opc_debe_v) if ("1.1.01.01" in val if metodo_v=="Efectivo" else ("1.1.01.02" in val if metodo_v=="Depósito Bancario" else "1.1.01.03" in val))), 0)
            else:
                cv2.info("Asentado a Clientes / Cuentas por Cobrar.")
                idx_dv = next((i for i, val in enumerate(opc_debe_v) if "1.1.02.01" in val), 0)

            cta_debe_v = cv1.selectbox("Cuenta de Cargo (DEBE) *", opc_debe_v if opc_debe_v else ["1.1.01.01 - Caja"], index=idx_dv, key=f"v_cdebe_{fk_v}")
            cta_haber_v = cv2.selectbox("Cuenta de Ingreso (HABER) *", opc_ing if opc_ing else ["4.1.01.01 - Ventas"], key=f"v_chaber_{fk_v}")

            # INGRESO POR MONTO TOTAL DE FACTURA
            total_factura_v = st.number_input("Monto Total de la Factura (Q) *", min_value=0.0, value=float(pre_v.get("total", 0.0)), step=100.0, format="%.2f", key=f"v_tot_{fk_v}")

            es_pequeno_v = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera débito fiscal IVA)", value=pre_v.get("es_pequeno", False), key=f"v_peq_{fk_v}")

            if es_pequeno_v:
                subtotal_v = total_factura_v
                iva_v = 0.0
            else:
                subtotal_v = round(total_factura_v / 1.12, 2)
                iva_v = round(total_factura_v - subtotal_v, 2)

            r1, r2 = st.columns(2)
            aplica_isr_v = r1.checkbox("Cliente practicó Retención de ISR", value=False, key=f"v_rk_{fk_v}")
            tipo_isr_v = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key=f"v_rt_{fk_v}")
            aplica_iva_v = r2.checkbox("Cliente practicó Retención de IVA", value=False, key=f"v_rvk_{fk_v}")
            tipo_iva_v = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key=f"v_rvt_{fk_v}")

            m_isr_v = 0.0
            if aplica_isr_v and subtotal_v >= 2800.0 and tipo_isr_v != "No Aplica":
                m_isr_v = round(subtotal_v * 0.05, 2) if tipo_isr_v == "5% General" else round(1500.0 + (subtotal_v - 30000.0) * 0.07, 2)

            m_iva_v = 0.0
            if aplica_iva_v and tipo_iva_v != "No Aplica / No es Agente Retenedor":
                m_iva_v = round(iva_v * 0.15, 2) if "15%" in tipo_iva_v else round(iva_v, 2)

            liq_v = round(total_factura_v - m_isr_v - m_iva_v, 2)

            st.caption(f"Base Imponible: **Q {subtotal_v:,.2f}** | IVA Débito Fiscal: **Q {iva_v:,.2f}** | Líquido a Recibir: **Q {liq_v:,.2f}**")

            partida_v = [{"Cuenta": cta_debe_v, "DEBE": f"Q {liq_v:,.2f}", "HABER": "Q 0.00"}]
            if m_isr_v > 0: partida_v.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr_v:,.2f}", "HABER": "Q 0.00"})
            if m_iva_v > 0: partida_v.append({"Cuenta": "1.1.03.03 - Constancias Retención IVA", "DEBE": f"Q {m_iva_v:,.2f}", "HABER": "Q 0.00"})
            partida_v.append({"Cuenta": cta_haber_v, "DEBE": "Q 0.00", "HABER": f"Q {subtotal_v:,.2f}"})
            if iva_v > 0: partida_v.append({"Cuenta": "2.1.02.01 - IVA por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {iva_v:,.2f}"})
            st.table(pd.DataFrame(partida_v))

            if st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary"):
                if num_fac and cli_nom and cli_nit and total_factura_v > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                    saldo = liq_v if cond_v == "Crédito" else 0.0
                    estado = "PENDIENTE" if cond_v == "Crédito" else "PAGADO"
                    fp_desc = cond_v if cond_v == "Crédito" else f"Contado ({metodo_v})"

                    c.execute("""
                        INSERT INTO facturas VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, 'Venta', 'Ingreso',
                                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nom, cli_nit,
                          cta_haber_v, cta_debe_v, fp_desc, subtotal_v, iva_v, total_factura_v,
                          1 if m_isr_v > 0 else 0, 0.05 if m_isr_v > 0 else 0.0, m_isr_v,
                          1 if m_iva_v > 0 else 0, 0.15 if m_iva_v > 0 else 0.0, m_iva_v,
                          liq_v, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"Factura Venta {num_fac} a {cli_nom}"

                    cod_d, nom_d = cta_debe_v.split(" - ", 1)
                    cod_h, nom_h = cta_haber_v.split(" - ", 1)
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, liq_v, glosa, num_fac, now_str))
                    if m_isr_v > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.02', 'ISR Retenido por Acreditar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr_v, glosa, num_fac, now_str))
                    if m_iva_v > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.03', 'Constancias Retención IVA', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva_v, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, subtotal_v, glosa, num_fac, now_str))
                    if iva_v > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.02.01', 'IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva_v, glosa, num_fac, now_str))

                    try:
                        c.execute("""
                            INSERT INTO terceros (empresa_id, tipo, nit, nombre)
                            VALUES (%s, 'CLIENTE', %s, %s)
                            ON CONFLICT (nit) DO NOTHING
                        """, (selected_emp_id, cli_nit.strip().upper(), cli_nom.strip()))
                    except Exception:
                        pass

                    conn.commit()
                    conn.close()
                    st.success("Factura de venta emitida exitosamente.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, cliente, NIT y total.")

    with tab_v_editar:
        st.subheader("✏️ Modificar o Eliminar Factura de Venta")
        conn = get_db_connection()
        df_ventas_edit = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, total FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_ventas_edit.empty:
            st.info("No hay facturas de ventas para editar.")
        else:
            dict_v_edit = {f"Factura {r['numero_factura']} ({r['fecha']}) - {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_ventas_edit.iterrows()}
            fac_v_sel_label = st.selectbox("Seleccionar Factura a Modificar:", list(dict_v_edit.keys()))
            fac_v_id_sel = dict_v_edit[fac_v_sel_label]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT fecha, numero_factura, serie, tercero_nombre, tercero_nit, total, subtotal, iva FROM facturas WHERE id = %s", (fac_v_id_sel,))
            f_row = c.fetchone()
            conn.close()

            with st.form("form_edit_venta_act"):
                c1, c2 = st.columns(2)
                f_fec_corregida = c1.date_input("Fecha Correcta de Emisión:", value=f_row[0] if isinstance(f_row[0], date) else datetime.strptime(str(f_row[0]), "%Y-%m-%d").date())
                f_num_corregido = c2.text_input("Número DTE / Factura:", value=f_row[1])

                b_guardar_v, b_eliminar_v = st.columns([2, 1])
                if b_guardar_v.form_submit_button("💾 Guardar Corrección", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET fecha = %s, numero_factura = %s WHERE id = %s", (str(f_fec_corregida), f_num_corregido, fac_v_id_sel))
                    c.execute("UPDATE libro_diario SET fecha = %s, documento_ref = %s WHERE empresa_id = %s AND documento_ref = %s", (str(f_fec_corregida), f_num_corregido, selected_emp_id, f_row[1]))
                    conn.commit()
                    conn.close()
                    st.success("Factura y partida contable en Libro Diario actualizadas.")
                    st.rerun()

            if is_admin:
                st.divider()
                if st.button("🗑️ Eliminar Factura Definitivamente", type="secondary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND documento_ref = %s", (selected_emp_id, f_row[1]))
                    c.execute("DELETE FROM facturas WHERE id = %s", (fac_v_id_sel,))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y sus asientos contables eliminados definitivamente.")
                    st.rerun()

# ==========================================
# 3. FACTURA DE COMPRAS (FEL, NO FISCALES, BIEN/SERVICIO Y EDICIÓN)
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title("🛒 Factura de Compras y Gastos")

    tab_c_ingreso, tab_c_editar = st.tabs(["➕ Registrar Compra / Gasto", "✏️ Modificar / Corregir Factura"])

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT nit, nombre FROM terceros WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre")
    provs_corp = c.fetchall()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Costo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_haber = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    dict_prov = {f"{p[1]} (NIT: {p[0]})": p for p in provs_corp}
    opc_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opc_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_haber.iterrows()]

    with tab_c_ingreso:
        tipo_doc_compra = st.radio("Tipo de Comprobante:", ["Factura Electrónica FEL (SAT)", "Documento No Fiscal (Recibo / Vale / Caja Chica)"], horizontal=True)

        pre_c = {}
        if tipo_doc_compra == "Factura Electrónica FEL (SAT)":
            dte_cargado_c = st.file_uploader("Sube el PDF emitido por SAT (FEL)", type=["pdf"], key="pdf_compra")
            if dte_cargado_c:
                try:
                    pre_c = extraer_datos_dte_sat(dte_cargado_c)
                    st.success("✅ Factura FEL procesada con éxito.")
                except Exception:
                    st.warning("No se pudieron leer automáticamente todos los campos.")
        else:
            dte_cargado_c = None

        st.markdown("#### 🔍 Buscador de Proveedor Corporativo")
        prov_seleccionado_busc = st.selectbox(
            "Buscar proveedor por Nombre o NIT:",
            ["-- Seleccionar o escribir nuevo --"] + list(dict_prov.keys()),
            key="busc_prv_c"
        )
        
        nit_prov_precargado = ""
        nom_prov_precargado = ""
        if prov_seleccionado_busc != "-- Seleccionar o escribir nuevo --":
            nit_prov_precargado = dict_prov[prov_seleccionado_busc][0]
            nom_prov_precargado = dict_prov[prov_seleccionado_busc][1]
        elif pre_c.get("emisor_nit"):
            nit_prov_precargado = pre_c.get("emisor_nit")
            nom_prov_precargado = pre_c.get("emisor_nombre")

        if nit_prov_precargado and not any(p[0].upper() == nit_prov_precargado.upper() for p in provs_corp):
            st.warning(f"⚠️ El proveedor con NIT **{nit_prov_precargado}** no está registrado en el directorio corporativo.")

        fk_c = dte_cargado_c.name if dte_cargado_c else f"c_{nit_prov_precargado}"

        with st.form("form_compra_act", clear_on_submit=False):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / DTE / Vale *", value=pre_c.get("numero_dte", ""), key=f"c_num_{fk_c}")
            serie = c2.text_input("Serie", value=pre_c.get("serie", ""), key=f"c_ser_{fk_c}")
            fecha_fac = c3.date_input("Fecha de Compra *", value=pre_c.get("fecha") or date.today(), key=f"c_fec_{fk_c}")

            c4, c5 = st.columns(2)
            prov_nit = c4.text_input("NIT Proveedor / Emisor *", value=nit_prov_precargado, key=f"c_pnit_{fk_c}")
            prov_nom = c5.text_input("Nombre / Razón Social Proveedor *", value=nom_prov_precargado, key=f"c_pnom_{fk_c}")

            cb1, cb2 = st.columns(2)
            tipo_adquisicion = cb1.selectbox("Tipo de Adquisición:", ["Servicio", "Bien (Mercadería o Activo)"], key=f"c_ta_{fk_c}")
            destino_bien = cb2.selectbox("Destino de la Adquisición:", ["Gasto Operativo", "Mercadería (Inventario Kárdex)", "Activo Fijo / No Corriente"], key=f"c_dest_{fk_c}")

            cp1, cp2 = st.columns(2)
            cond_pago = cp1.selectbox("Condición de Pago:", ["Contado", "Crédito"], key=f"c_cond_{fk_c}")
            if cond_pago == "Contado":
                metodo_p = cp2.selectbox("Método de Pago:", ["Efectivo", "Transferencia Bancaria", "Tarjeta de Crédito Corporativa"], key=f"c_met_{fk_c}")
                idx_h = next((i for i, val in enumerate(opc_haber) if ("1.1.01.01" in val if metodo_p=="Efectivo" else ("1.1.01.02" in val if metodo_p=="Transferencia Bancaria" else "2.1.01.02" in val))), 0)
            else:
                cp2.info("Asentado a Cuentas por Pagar (Proveedores).")
                idx_h = next((i for i, val in enumerate(opc_haber) if "2.1.01.01" in val), 0)

            # Selección de cuenta Debe sugerida según destino
            idx_d_sug = 0
            if destino_bien == "Mercadería (Inventario Kárdex)":
                idx_d_sug = next((i for i, val in enumerate(opc_debe) if "5.1.01.01" in val or "1.1.04.01" in val), 0)
            elif destino_bien == "Activo Fijo / No Corriente":
                idx_d_sug = next((i for i, val in enumerate(opc_debe) if "Activo" in val), 0)

            cd1, cd2 = st.columns(2)
            cta_debe = cd1.selectbox("Cuenta de Cargo (DEBE) *", opc_debe if opc_debe else ["5.1.01.01 - Compras"], index=idx_d_sug, key=f"c_cdebe_{fk_c}")
            cta_haber = cd2.selectbox("Cuenta de Abono (HABER) *", opc_haber if opc_haber else ["2.1.01.01 - Proveedores"], index=idx_h, key=f"c_chaber_{fk_c}")

            subtotal_compra = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, value=float(pre_c.get("subtotal", 0.0)), step=100.0, format="%.2f", key=f"c_sub_{fk_c}")

            if tipo_doc_compra == "Factura Electrónica FEL (SAT)":
                es_peq_prov = st.checkbox("¿Factura de Pequeño Contribuyente? (No genera crédito fiscal IVA)", value=pre_c.get("es_pequeno", False), key=f"c_peq_{fk_c}")
                iva_compra = 0.0 if es_peq_prov else round(subtotal_compra * 0.12, 2)
                total_compra = round(subtotal_compra + iva_compra, 2)
            else:
                es_peq_prov = False
                iva_compra = 0.0
                total_compra = subtotal_compra

            r1, r2 = st.columns(2)
            aplica_isr_c = r1.checkbox("Practicar Retención ISR", value=(subtotal_compra >= 2800.0 and tipo_doc_compra == "Factura Electrónica FEL (SAT)"), key=f"c_rk_{fk_c}")
            tipo_isr_c = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Honorarios", "No Aplica"], index=0 if subtotal_compra >= 2800.0 else 3, key=f"c_rt_{fk_c}")
            aplica_iva_c = r2.checkbox("Practicar Retención IVA", value=False, key=f"c_rvk_{fk_c}")
            tipo_iva_c = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key=f"c_rvt_{fk_c}")

            m_isr_c = 0.0
            if aplica_isr_c and subtotal_compra >= 2800.0 and tipo_isr_c != "No Aplica":
                m_isr_c = round(subtotal_compra * 0.05, 2) if tipo_isr_c == "5% General" else round(1500.0 + (subtotal_compra - 30000.0) * 0.07, 2)

            m_iva_c = 0.0
            if aplica_iva_c and tipo_iva_c != "No Aplica / No es Agente Retenedor":
                m_iva_c = round(iva_compra * 0.15, 2) if "15%" in tipo_iva_c else round(iva_compra, 2)

            liq_c = round(total_compra - m_isr_c - m_iva_c, 2)

            partida_c = [{"Cuenta": cta_debe, "DEBE": f"Q {subtotal_compra:,.2f}", "HABER": "Q 0.00"}]
            if iva_compra > 0: partida_c.append({"Cuenta": "1.1.03.01 - IVA por Cobrar (Crédito)", "DEBE": f"Q {iva_compra:,.2f}", "HABER": "Q 0.00"})
            if m_isr_c > 0: partida_c.append({"Cuenta": "2.1.03.01 - Retenciones ISR por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_isr_c:,.2f}"})
            if m_iva_c > 0: partida_c.append({"Cuenta": "2.1.03.02 - Retenciones IVA por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_iva_c:,.2f}"})
            partida_c.append({"Cuenta": cta_haber, "DEBE": "Q 0.00", "HABER": f"Q {liq_c:,.2f}"})
            st.table(pd.DataFrame(partida_c))

            if st.form_submit_button("Guardar Factura de Compra y Asentar", type="primary"):
                if num_fac and prov_nom and prov_nit and subtotal_compra > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                    saldo = liq_c if cond_pago == "Crédito" else 0.0
                    estado = "PENDIENTE" if cond_pago == "Crédito" else "PAGADO"
                    fp_desc = cond_pago if cond_pago == "Crédito" else f"Contado ({metodo_p})"

                    c.execute("""
                        INSERT INTO facturas VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s,
                                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom, prov_nit,
                          cta_debe, cta_haber, fp_desc, subtotal_compra, iva_compra, total_compra,
                          1 if m_isr_c > 0 else 0, 0.05 if m_isr_c > 0 else 0.0, m_isr_c,
                          1 if m_iva_c > 0 else 0, 0.15 if m_iva_c > 0 else 0.0, m_iva_c,
                          liq_c, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"{'Factura Compra' if tipo_doc_compra == 'Factura Electrónica FEL (SAT)' else 'Comprobante No Fiscal'} {num_fac} de {prov_nom}"

                    cod_d, nom_d = cta_debe.split(" - ", 1)
                    cod_h, nom_h = cta_haber.split(" - ", 1)
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal_compra, glosa, num_fac, now_str))
                    if iva_compra > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.01', 'IVA por Cobrar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva_compra, glosa, num_fac, now_str))
                    if m_isr_c > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.03.01', 'Retenciones ISR por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr_c, glosa, num_fac, now_str))
                    if m_iva_c > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.03.02', 'Retenciones IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva_c, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, liq_c, glosa, num_fac, now_str))

                    if destino_bien == "Activo Fijo / No Corriente":
                        c.execute("""
                            INSERT INTO activos_fijos (id, empresa_id, factura_id, codigo_activo, descripcion, categoria, fecha_adquisicion, costo_adquisicion, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (f"act-{int(datetime.now().timestamp())}", selected_emp_id, fac_id, num_fac, f"Adquisición {prov_nom}", "Mobiliario y Equipo", str(fecha_fac), subtotal_compra, now_str))

                    try:
                        c.execute("""
                            INSERT INTO terceros (empresa_id, tipo, nit, nombre)
                            VALUES (%s, 'PROVEEDOR', %s, %s)
                            ON CONFLICT (nit) DO NOTHING
                        """, (selected_emp_id, prov_nit.strip().upper(), prov_nom.strip()))
                    except Exception:
                        pass

                    conn.commit()
                    conn.close()
                    st.success("Compra asentada exitosamente en el sistema.")
                    st.rerun()

    with tab_c_editar:
        st.subheader("✏️ Modificar o Eliminar Factura de Compra")
        conn = get_db_connection()
        df_compras_edit = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, total FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_compras_edit.empty:
            st.info("No hay facturas de compras para editar.")
        else:
            dict_c_edit = {f"Factura {r['numero_factura']} ({r['fecha']}) - {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_compras_edit.iterrows()}
            fac_c_sel_label = st.selectbox("Seleccionar Factura a Modificar:", list(dict_c_edit.keys()))
            fac_c_id_sel = dict_c_edit[fac_c_sel_label]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT fecha, numero_factura, serie, tercero_nombre, tercero_nit, total FROM facturas WHERE id = %s", (fac_c_id_sel,))
            fc_row = c.fetchone()
            conn.close()

            with st.form("form_edit_compra_act"):
                c1, c2 = st.columns(2)
                fc_fec_corregida = c1.date_input("Fecha Correcta:", value=fc_row[0] if isinstance(fc_row[0], date) else datetime.strptime(str(fc_row[0]), "%Y-%m-%d").date())
                fc_num_corregido = c2.text_input("Número DTE / Factura:", value=fc_row[1])

                b_guardar_c, b_eliminar_c = st.columns([2, 1])
                if b_guardar_c.form_submit_button("💾 Guardar Corrección", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET fecha = %s, numero_factura = %s WHERE id = %s", (str(fc_fec_corregida), fc_num_corregido, fac_c_id_sel))
                    c.execute("UPDATE libro_diario SET fecha = %s, documento_ref = %s WHERE empresa_id = %s AND documento_ref = %s", (str(fc_fec_corregida), fc_num_corregido, selected_emp_id, fc_row[1]))
                    conn.commit()
                    conn.close()
                    st.success("Factura de compra y asiento contable actualizados.")
                    st.rerun()

            if is_admin:
                st.divider()
                if st.button("🗑️ Eliminar Factura de Compra Definitivamente", type="secondary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND documento_ref = %s", (selected_emp_id, fc_row[1]))
                    c.execute("DELETE FROM facturas WHERE id = %s", (fac_c_id_sel,))
                    conn.commit()
                    conn.close()
                    st.warning("Factura de compra eliminada del sistema.")
                    st.rerun()

# ==========================================
# 4. KÁRDEX E INVENTARIOS CORPORATIVOS (UNIFICADO)
# ==========================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    if not selected_emp_id: st.stop()
    st.title("📦 Catálogo de Artículos e Inventario Corporativo")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(CAST(NULLIF(regexp_replace(codigo, '[^0-9]', '', 'g'), '') AS INTEGER)), 0) + 1 FROM productos")
    sig_codigo = c.fetchone()[0] or 1
    conn.close()

    with st.expander("➕ Registrar Nuevo Artículo en Catálogo Corporativo", expanded=False):
        with st.form("form_prod_corp", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            p_cod = c1.text_input("Código de Artículo (Secuencia 1 - 1,000,000) *", value=str(sig_codigo))
            p_nom = c2.text_input("Descripción del Producto *")
            p_pre = c3.number_input("Precio de Venta Sugerido (Referencial Q)", min_value=0.0, step=10.0)

            if st.form_submit_button("Guardar en Catálogo Corporativo", type="primary"):
                if p_cod and p_nom:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        p_id = f"prod-{int(datetime.now().timestamp())}"
                        c.execute("""
                            INSERT INTO productos (id, empresa_id, codigo, descripcion, existencia, costo_promedio, precio_venta, created_at)
                            VALUES (%s, 'CORP', %s, %s, 0, 0, %s, %s)
                        """, (p_id, p_cod.strip(), p_nom.strip(), p_pre, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Artículo '{p_nom}' guardado a nivel corporativo.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al guardar producto: {err}")
                    finally:
                        conn.close()

    conn = get_db_connection()
    # Consulta corporativa sin filtrar por empresa_id para que el producto 1 y todos estén visibles
    df_art_corp = pd.read_sql_query("""
        SELECT codigo AS "Código", descripcion AS "Descripción", existencia AS "Stock Actual", 
               costo_promedio AS "Costo Promedio (Q)", precio_venta AS "Precio Venta Sugerido (Q)" 
        FROM productos ORDER BY CAST(NULLIF(regexp_replace(codigo, '[^0-9]', '', 'g'), '') AS INTEGER) ASC
    """, conn)
    conn.close()

    if df_art_corp.empty:
        st.info("No hay productos registrados en el catálogo corporativo.")
    else:
        st.dataframe(df_art_corp, use_container_width=True)

# ==========================================
# 5. ESTADO DE RESULTADOS (OFICIAL CONTABLE CON DESGLOSE MENSUAL)
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title(f"📉 Estado de Resultados — {selected_empresa_info[1]}")
    st.caption(f"Régimen SAT: **{selected_empresa_info[3]}**")

    col_v1, col_v2 = st.columns([2, 1])
    modo_er = col_v1.radio("Visualización del Período:", ["🌐 Histórico Acumulado (Mes a Mes)", "📅 Mes Específico", "📆 Año Completo"], horizontal=True)
    anio_sel = col_v2.selectbox("Año Fiscal:", [2026, 2025, 2024], index=0)

    conn = get_db_connection()
    query_er = """
        SELECT n.codigo, n.nombre, n.tipo,
               EXTRACT(MONTH FROM d.fecha::date) AS mes,
               COALESCE(SUM(d.debe), 0) AS debe,
               COALESCE(SUM(d.haber), 0) AS haber
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND EXTRACT(YEAR FROM d.fecha::date) = %s AND (n.tipo IN ('Ingreso', 'Gasto', 'Costo'))
        GROUP BY n.codigo, n.nombre, n.tipo, EXTRACT(MONTH FROM d.fecha::date)
        ORDER BY n.codigo
    """
    df_er_raw = pd.read_sql_query(query_er, conn, params=(selected_emp_id, anio_sel))
    conn.close()

    meses_nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

    if modo_er == "📅 Mes Específico":
        mes_num_sel = st.selectbox("Seleccionar Mes:", range(1, 13), format_func=lambda x: f"{x:02d} - {meses_nombres[x-1]}")
        df_er_raw = df_er_raw[df_er_raw["mes"] == mes_num_sel]

    # Cálculos clasificados
    df_ing = df_er_raw[df_er_raw["tipo"] == "Ingreso"]
    df_costo = df_er_raw[df_er_raw["tipo"] == "Costo"]
    df_gasto = df_er_raw[df_er_raw["tipo"] == "Gasto"]

    tot_ing = float(df_ing["haber"].sum() - df_ing["debe"].sum()) if not df_ing.empty else 0.0
    tot_cos = float(df_costo["debe"].sum() - df_costo["haber"].sum()) if not df_costo.empty else 0.0
    util_bruta = tot_ing - tot_cos
    tot_gas = float(df_gasto["debe"].sum() - df_gasto["haber"].sum()) if not df_gasto.empty else 0.0
    util_operacion = util_bruta - tot_gas

    # Cálculo legal ISR según régimen SAT
    reg_emp = selected_empresa_info[3]
    if "Pequeño Contribuyente" in reg_emp:
        impuesto_isr = round(tot_ing * 0.05, 2)
    elif "Sobre las Utilidades" in reg_emp:
        impuesto_isr = round(util_operacion * 0.25, 2) if util_operacion > 0 else 0.0
    else: # Opcional Simplificado
        impuesto_isr = round(tot_ing * 0.05, 2) if tot_ing <= 30000 else round(1500.0 + (tot_ing - 30000.0) * 0.07, 2)

    util_neta = util_operacion - impuesto_isr

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("(+) Ingresos Operacionales", f"Q {tot_ing:,.2f}")
    c2.metric("(=) Utilidad Bruta", f"Q {util_bruta:,.2f}")
    c3.metric("(-) Impuesto ISR (SAT)", f"Q {impuesto_isr:,.2f}")
    c4.metric("(=) Utilidad Neta Ejercicio", f"Q {util_neta:,.2f}")

    # Estructura Contable Formal Vertical
    filas_er = []
    filas_er.append({"Clasificación": "1. INGRESOS OPERACIONALES", "Cuenta": "Ventas / Servicios Netos", "Monto (Q)": tot_ing})
    filas_er.append({"Clasificación": "2. COSTO DE VENTAS", "Cuenta": "Compras e Inventario Consumido", "Monto (Q)": -tot_cos})
    filas_er.append({"Clasificación": "3. MARGEN BRUTO", "Cuenta": "(=) UTILIDAD BRUTA EN VENTAS", "Monto (Q)": util_bruta})
    filas_er.append({"Clasificación": "4. GASTOS DE OPERACIÓN", "Cuenta": "Sueldos, Alquileres, Combustibles, etc.", "Monto (Q)": -tot_gas})
    filas_er.append({"Clasificación": "5. RESULTADO OPERATIVO", "Cuenta": "(=) UTILIDAD EN OPERACIÓN", "Monto (Q)": util_operacion})
    filas_er.append({"Clasificación": "6. IMPUESTOS", "Cuenta": f"(-) Provisión Impuesto ISR ({reg_emp})", "Monto (Q)": -impuesto_isr})
    filas_er.append({"Clasificación": "7. RESULTADO FINAL", "Cuenta": "(=) UTILIDAD NETA DEL EJERCICIO", "Monto (Q)": util_neta})

    df_er_vertical = pd.DataFrame(filas_er)
    st.table(df_er_vertical.style.format({"Monto (Q)": "Q {:,.2f}"}))

    col_e1, col_e2 = st.columns(2)
    excel_er = exportar_reporte_excel(df_er_vertical, "Estado_Resultados")
    col_e1.download_button("📥 Descargar Estado de Resultados Excel", excel_er, f"Estado_Resultados_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    pdf_er = exportar_libro_pdf(df_er_vertical, "Estado de Resultados Oficial", selected_empresa_info[1], selected_empresa_info[2])
    col_e2.download_button("📄 Descargar Estado de Resultados PDF", pdf_er, f"Estado_Resultados_{selected_empresa_info[1]}.pdf", "application/pdf")

# ==========================================
# 6. LIBROS TRIBUTARIOS SAT (CON EXCEL Y PDF FOLIADO)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_compras_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "Factura", tercero_nit AS "NIT Proveedor",
               tercero_nombre AS "Proveedor", subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR (Q)", monto_ret_iva AS "Ret. IVA (Q)", total AS "Total Factura (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_compras_sat.empty:
        st.info("Sin registros de compras.")
    else:
        st.dataframe(df_compras_sat, use_container_width=True)
        c1, c2, c3 = st.columns([1.5, 1.5, 1])
        res_sat = c1.text_input("Resolución SAT:", value="2026-5-12345-001", key="sat_comp")
        fol_ini = c2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_comp")
        
        pdf_c = exportar_libro_pdf(df_compras_sat, "Libro de Compras SAT", selected_empresa_info[1], selected_empresa_info[2], res_sat, fol_ini)
        excel_c = exportar_reporte_excel(df_compras_sat, "Libro_Compras")
        
        c3.download_button("📥 Descargar Excel", excel_c, f"Libro_Compras_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("📄 Descargar Libro Compras PDF Foliado", pdf_c, f"Libro_Compras_{selected_empresa_info[1]}.pdf", "application/pdf")

elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Ventas Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ventas_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "Factura", tercero_nit AS "NIT Cliente",
               tercero_nombre AS "Cliente", subtotal AS "Base Imponible (Q)", iva AS "Débito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR Soportada (Q)", total AS "Total Factura (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_ventas_sat.empty:
        st.info("Sin registros de ventas.")
    else:
        st.dataframe(df_ventas_sat, use_container_width=True)
        c1, c2, c3 = st.columns([1.5, 1.5, 1])
        res_sat_v = c1.text_input("Resolución SAT:", value="2026-5-12345-001", key="sat_vent")
        fol_ini_v = c2.number_input("Folio Inicial:", min_value=1, value=1, key="fol_vent")

        pdf_v = exportar_libro_pdf(df_ventas_sat, "Libro de Ventas SAT", selected_empresa_info[1], selected_empresa_info[2], res_sat_v, fol_ini_v)
        excel_v = exportar_reporte_excel(df_ventas_sat, "Libro_Ventas")

        c3.download_button("📥 Descargar Excel", excel_v, f"Libro_Ventas_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("📄 Descargar Libro Ventas PDF Foliado", pdf_v, f"Libro_Ventas_{selected_empresa_info[1]}.pdf", "application/pdf")

# ==========================================
# 7. LIBRO DIARIO, MAYOR Y BALANCES
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("""
        SELECT partida_no AS "Partida #", fecha AS "Fecha", codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta", 
               debe AS "Debe (Q)", haber AS "Haber (Q)", concepto AS "Glosa" 
        FROM libro_diario WHERE empresa_id = %s ORDER BY partida_no ASC, id ASC
    """, conn, params=(selected_emp_id,))
    conn.close()
    if not df_d.empty:
        c1, c2 = st.columns(2)
        c1.metric("Total DEBE", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total HABER", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)
        pdf_d = exportar_libro_pdf(df_d, "Libro Diario General", selected_empresa_info[1], selected_empresa_info[2])
        st.download_button("📄 Descargar Libro Diario PDF", pdf_d, f"Libro_Diario_{selected_empresa_info[1]}.pdf", "application/pdf")
    else:
        st.info("Sin registros en el Libro Diario.")

elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title("📚 Libro Mayor")
    conn = get_db_connection()
    df_m = pd.read_sql_query("""
        SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
               ROUND(SUM(debe)::numeric, 2) AS "Total Debe (Q)", ROUND(SUM(haber)::numeric, 2) AS "Total Haber (Q)",
               ROUND((SUM(debe) - SUM(haber))::numeric, 2) AS "Saldo (Q)"
        FROM libro_diario WHERE empresa_id = %s GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta
    """, conn, params=(selected_emp_id,))
    conn.close()
    if not df_m.empty:
        st.dataframe(df_m, use_container_width=True)
        pdf_m = exportar_libro_pdf(df_m, "Libro Mayor", selected_empresa_info[1], selected_empresa_info[2])
        st.download_button("📄 Descargar Libro Mayor PDF", pdf_m, f"Libro_Mayor_{selected_empresa_info[1]}.pdf", "application/pdf")
    else:
        st.info("Sin registros en el Libro Mayor.")

elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    conn = get_db_connection()
    query_b = """
        SELECT n.codigo, n.nombre, n.tipo, COALESCE(SUM(d.debe), 0) AS total_debe, COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s GROUP BY n.codigo, n.nombre, n.tipo HAVING (COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0) ORDER BY n.codigo
    """
    df_b = pd.read_sql_query(query_b, conn, params=(selected_emp_id,))
    conn.close()
    if not df_b.empty:
        df_b['Saldo Deudor'] = df_b.apply(lambda r: round(float(r['total_debe']) - float(r['total_haber']), 2) if float(r['total_debe']) > float(r['total_haber']) else 0.0, axis=1)
        df_b['Saldo Acreedor'] = df_b.apply(lambda r: round(float(r['total_haber']) - float(r['total_debe']), 2) if float(r['total_haber']) > float(r['total_debe']) else 0.0, axis=1)
        df_b_view = df_b[['codigo', 'nombre', 'tipo', 'Saldo Deudor', 'Saldo Acreedor']].rename(columns={'codigo': 'Código', 'nombre': 'Cuenta', 'tipo': 'Tipo'})
        st.dataframe(df_b_view, use_container_width=True)

        col_d1, col_d2 = st.columns(2)
        excel_b = exportar_reporte_excel(df_b_view, "Balance_Saldos")
        col_d1.download_button("📥 Descargar Balance de Saldos Excel", excel_b, f"Balance_Saldos_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_b = exportar_libro_pdf(df_b_view, "Balance General de Saldos", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar Balance de Saldos PDF", pdf_b, f"Balance_Saldos_{selected_empresa_info[1]}.pdf", "application/pdf")
    else:
        st.info("Sin movimientos para generar el Balance General.")

elif selected_menu == "🏛️ Balance Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title("🏛️ Balance de Situación Financiera Clasificado")
    conn = get_db_connection()
    q_activo = """
        SELECT n.codigo AS "Código", n.nombre AS "Cuenta", ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS "Monto (Q)"
        FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND n.tipo = 'Activo' GROUP BY n.codigo, n.nombre HAVING COALESCE(SUM(d.debe - d.haber), 0) <> 0 ORDER BY n.codigo
    """
    q_pasivo = """
        SELECT n.codigo AS "Código", n.nombre AS "Cuenta", ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS "Monto (Q)"
        FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND n.tipo = 'Pasivo' GROUP BY n.codigo, n.nombre HAVING COALESCE(SUM(d.haber - d.debe), 0) <> 0 ORDER BY n.codigo
    """
    q_capital = """
        SELECT n.codigo AS "Código", n.nombre AS "Cuenta", ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS "Monto (Q)"
        FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND n.tipo = 'Capital' GROUP BY n.codigo, n.nombre HAVING COALESCE(SUM(d.haber - d.debe), 0) <> 0 ORDER BY n.codigo
    """
    df_act = pd.read_sql_query(q_activo, conn, params=(selected_emp_id,))
    df_pas = pd.read_sql_query(q_pasivo, conn, params=(selected_emp_id,))
    df_cap = pd.read_sql_query(q_capital, conn, params=(selected_emp_id,))
    conn.close()

    tot_act = float(df_act["Monto (Q)"].sum()) if not df_act.empty else 0.0
    tot_pas = float(df_pas["Monto (Q)"].sum()) if not df_pas.empty else 0.0
    tot_cap = float(df_cap["Monto (Q)"].sum()) if not df_cap.empty else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Activo", f"Q {tot_act:,.2f}")
    c2.metric("Total Pasivo", f"Q {tot_pas:,.2f}")
    c3.metric("Total Patrimonio Neto", f"Q {tot_cap:,.2f}")

    st.subheader("1. Activos")
    st.dataframe(df_act, use_container_width=True)
    st.subheader("2. Pasivos")
    st.dataframe(df_pas, use_container_width=True)
    st.subheader("3. Capital y Patrimonio")
    st.dataframe(df_cap, use_container_width=True)

# ==========================================
# 8. CUENTAS POR COBRAR Y POR PAGAR
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("💳 Control de Cuentas por Pagar (CXP)")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""
        SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'
    """, conn, params=(selected_emp_id,))
    
    if df_cxp.empty:
        st.success("✨ Todas las facturas de proveedores están canceladas al día.")
    else:
        st.dataframe(df_cxp, use_container_width=True)
        with st.form("form_abono_cxp"):
            fac_sel = st.selectbox("Seleccionar Factura a Pagar:", df_cxp['id'].tolist(), format_func=lambda x: f"Fac {df_cxp[df_cxp['id']==x]['numero_factura'].values[0]} - {df_cxp[df_cxp['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxp[df_cxp['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_abono = st.number_input("Monto a Pagar (Q)", min_value=0.01, step=50.0)
            metodo_pago = st.selectbox("Forma de Pago:", ["Transferencia Bancaria (Bancos)", "Efectivo (Caja)"])
            cta_origen = "1.1.01.02" if "Bancos" in metodo_pago else "1.1.01.01"

            if st.form_submit_button("Aplicar Pago", type="primary"):
                row_f = df_cxp[df_cxp['id'] == fac_sel].iloc[0]
                if monto_abono > row_f['saldo_pendiente']:
                    st.error("El abono no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(row_f['saldo_pendiente'] - monto_abono, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.01.01', 'Proveedores / Cuentas por Pagar', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), monto_abono, f"Abono Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, 'Salida de Fondos', 0, %s, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), cta_origen, monto_abono, f"Pago Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("Pago registrado exitosamente.")
                    st.rerun()
    conn.close()

elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title("💰 Control de Cuentas por Cobrar (CXC)")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""
        SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'
    """, conn, params=(selected_emp_id,))
    
    if df_cxc.empty:
        st.success("✨ Todos los clientes se encuentran al día con sus pagos.")
    else:
        st.dataframe(df_cxc, use_container_width=True)
        with st.form("form_abono_cxc"):
            fac_sel = st.selectbox("Seleccionar Factura a Cobrar:", df_cxc['id'].tolist(), format_func=lambda x: f"Fac {df_cxc[df_cxc['id']==x]['numero_factura'].values[0]} - {df_cxc[df_cxc['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxc[df_cxc['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_cobro = st.number_input("Monto Recibido (Q)", min_value=0.01, step=50.0)
            metodo_cobro = st.selectbox("Ingreso a:", ["Depósito Bancario (Bancos)", "Efectivo (Caja)"])
            cta_destino = "1.1.01.02" if "Bancos" in metodo_cobro else "1.1.01.01"

            if st.form_submit_button("Aplicar Cobro", type="primary"):
                row_v = df_cxc[df_cxc['id'] == fac_sel].iloc[0]
                if monto_cobro > row_v['saldo_pendiente']:
                    st.error("El cobro no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(row_v['saldo_pendiente'] - monto_cobro, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, 'Ingreso de Fondos', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), cta_destino, monto_cobro, f"Cobro Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.02.01', 'Clientes / Cuentas por Cobrar', 0, %s, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), monto_cobro, f"Cancelación Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("Cobro registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

# ==========================================
# 9. RETENCIONES, DIRECTOTIO, DESPENSA Y ADMIN
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title("📑 Libro de Retenciones Practicadas y Recibidas")
    conn = get_db_connection()
    df_r = pd.read_sql_query("""
        SELECT tipo AS "Operación", fecha AS "Fecha", numero_factura AS "Documento", tercero_nombre AS "Tercero", 
               subtotal AS "Base (Q)", monto_ret_isr AS "Retención ISR (Q)", monto_ret_iva AS "Retención IVA (Q)" 
        FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC
    """, conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_r, use_container_width=True)

elif selected_menu == "👥 Directorio Terceros (Clientes/Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")

    with st.expander("➕ Registrar Tercero Corporativo", expanded=True):
        with st.form("form_tercero", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            t_tipo = col1.selectbox("Tipo de Entidad:", ["CLIENTE", "PROVEEDOR", "AMBOS"])
            t_nit = col2.text_input("NIT * (Sin guiones o CF)")
            t_nom = col3.text_input("Razón Social / Nombre Comercial *")
            col4, col5, col6 = st.columns(3)
            t_dir = col4.text_input("Dirección")
            t_tel = col5.text_input("Teléfono")
            t_dias = col6.number_input("Días de Crédito", min_value=0, step=15)
            
            if st.form_submit_button("Guardar Tercero Corporativo", type="primary"):
                if t_nit and t_nom:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c.execute("""
                            INSERT INTO terceros (empresa_id, tipo, nit, nombre, direccion, telefono, dias_credito)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre, tipo = EXCLUDED.tipo, direccion = EXCLUDED.direccion
                        """, (selected_emp_id, t_tipo, t_nit.strip().upper(), t_nom.strip(), t_dir, t_tel, t_dias))
                        conn.commit()
                        st.success(f"Tercero '{t_nom}' registrado corporativamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al guardar: {e}")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el NIT y el Nombre.")

    conn = get_db_connection()
    df_t = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre\", direccion AS \"Dirección\", telefono AS \"Teléfono\", dias_credito AS \"Días Crédito\" FROM terceros ORDER BY nombre", conn)
    conn.close()
    st.dataframe(df_t, use_container_width=True)

elif selected_menu == "🏠 Despensa Familiar (Cocina)":
    st.title("🏠 Control de Despensa y Cocina Familiar")
    tab_stock, tab_ingreso, tab_rebaja, tab_nuevo = st.tabs(["📦 Stock Actual", "🛒 Registrar Ingreso", "🍳 Rebajar Consumo", "➕ Nuevo Producto Hogar"])

    conn = get_db_connection()
    df_articulos = pd.read_sql_query("SELECT id, nombre, categoria, unidad_medida, stock_actual, stock_minimo FROM despensa_articulos ORDER BY nombre ASC", conn)
    conn.close()

    with tab_stock:
        if df_articulos.empty:
            st.info("Aún no tienes productos registrados en la despensa.")
        else:
            st.dataframe(df_articulos, use_container_width=True)

    with tab_ingreso:
        if not df_articulos.empty:
            with st.form("form_ingreso_desp", clear_on_submit=True):
                opc_d = {f"{r['nombre']} (Stock: {r['stock_actual']} {r['unidad_medida']})": r["id"] for _, r in df_articulos.iterrows()}
                p_sel = st.selectbox("Producto:", list(opc_d.keys()))
                cant = st.number_input("Cantidad:", min_value=0.25, step=1.0, value=1.0)
                if st.form_submit_button("Ingresar a Stock"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual + %s WHERE id = %s", (cant, opc_d[p_sel]))
                    conn.commit()
                    conn.close()
                    st.success("Stock actualizado.")
                    st.rerun()

    with tab_rebaja:
        if not df_articulos.empty:
            with st.form("form_rebaja_desp", clear_on_submit=True):
                opc_r = {f"{r['nombre']} (Disponible: {r['stock_actual']} {r['unidad_medida']})": (r["id"], r["stock_actual"]) for _, r in df_articulos.iterrows()}
                p_r_sel = st.selectbox("Producto consumido:", list(opc_r.keys()))
                cant_r = st.number_input("Cantidad consumida:", min_value=0.25, step=1.0, value=1.0)
                if st.form_submit_button("Aplicar Rebaja"):
                    pid, stk = opc_r[p_r_sel]
                    if cant_r > stk:
                        st.error("No puedes rebajar más de lo disponible.")
                    else:
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual - %s WHERE id = %s", (cant_r, pid))
                        conn.commit()
                        conn.close()
                        st.success("Rebaja aplicada.")
                        st.rerun()

    with tab_nuevo:
        with st.form("form_nuevo_art_hogar", clear_on_submit=True):
            nom_art = st.text_input("Nombre de Artículo *")
            cat_art = st.selectbox("Categoría:", ["Granos y Abarrotes", "Lácteos y Huevos", "Carnes", "Limpieza", "Otros"])
            if st.form_submit_button("Guardar Artículo"):
                if nom_art:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("INSERT INTO despensa_articulos (id, nombre, categoria, stock_actual, stock_minimo, created_at) VALUES (%s, %s, %s, 0, 1, %s)",
                              (f"art-{int(datetime.now().timestamp())}", nom_art.strip(), cat_art, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("Artículo agregado.")
                    st.rerun()

elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title("🗂️ Nomenclatura Contable")
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores")
    conn = get_db_connection()
    df_u = pd.read_sql_query("SELECT username AS \"Usuario\", name AS \"Nombre\", email AS \"Correo\", role AS \"Rol\", status AS \"Estado\" FROM users ORDER BY username", conn)
    conn.close()
    st.dataframe(df_u, use_container_width=True)

elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro y Consulta de Empresas")
    tab_crear_e, tab_lista_e = st.tabs(["➕ Registrar Empresa", "📋 Empresas Registradas"])

    with tab_crear_e:
        with st.form("f_emp_nueva"):
            c1, c2 = st.columns(2)
            en = c1.text_input("Razón Social *")
            nt = c2.text_input("NIT *")
            c3, c4 = st.columns(2)
            dr = c3.text_input("Dirección")
            tl = c4.text_input("Teléfono")
            rg = st.selectbox("Régimen de ISR SAT", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades de Actividades Lucrativas (25%)", "Pequeño Contribuyente (5%)"])
            if st.form_submit_button("Guardar Empresa", type="primary"):
                if en.strip() and nt.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        eid = f"emp-{int(datetime.now().timestamp())}"
                        c.execute("INSERT INTO empresas (id, nombre, nit, direccion, telefono, regimen_isr, actividad_economica, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                  (eid, en.strip(), nt.strip(), dr.strip(), tl.strip(), rg, "Servicios Contables", datetime.now().isoformat()))
                        conn.commit()
                        sembrar_nomenclatura_inicial(eid)
                        st.success(f"Empresa '{en}' registrada exitosamente.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error: {err}")
                    finally:
                        conn.close()

    with tab_lista_e:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, nombre, nit, direccion, telefono, regimen_isr FROM empresas ORDER BY nombre")
        emps = c.fetchall()
        conn.close()
        for emp in emps:
            e_id, nom, nit, dire, tel, reg = emp
            with st.expander(f"🏢 {nom} (NIT: {nit})"):
                st.write(f"**Dirección:** {dire or 'N/A'} | **Tel:** {tel or 'N/A'} | **Régimen:** {reg}")