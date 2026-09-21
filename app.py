import streamlit as st
import pandas as pd
import sqlite3
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

# ==========================================
# EXTRACTOR DE FACTURAS DTE SAT (FEL)
# ==========================================
def extraer_datos_dte_sat(archivo_pdf):
    lector = PdfReader(archivo_pdf)
    texto = ""
    for pagina in lector.pages:
        t = pagina.extract_text()
        if t:
            texto += t + "\n"
            
    datos = {
        "numero_dte": "",
        "serie": "",
        "fecha": None,
        "emisor_nombre": "",
        "emisor_nit": "",
        "receptor_nombre": "",
        "receptor_nit": "",
        "total": 0.0,
        "subtotal": 0.0
    }
    
    m_aut = re.search(r'(?:NÚMERO DE AUTORIZACIÓN|Autorización|UUID)[:\s]+([A-F0-9\-]{36}|[A-F0-9]{32})', texto, re.I)
    if m_aut:
        datos["numero_dte"] = m_aut.group(1).strip()
        
    m_ser = re.search(r'(?:SERIE|Serie)[:\s]+([A-F0-9]{8,})', texto)
    if m_ser:
        datos["serie"] = m_ser.group(1).strip()
        
    m_dte_num = re.search(r'(?:NÚMERO DTE|Número)[:\s]+([0-9]{5,})', texto)
    if m_dte_num and not datos["numero_dte"]:
        datos["numero_dte"] = m_dte_num.group(1).strip()

    m_fec = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', texto)
    if m_fec:
        f_str = m_fec.group(1)
        try:
            if "-" in f_str:
                datos["fecha"] = datetime.strptime(f_str, "%Y-%m-%d").date()
            else:
                datos["fecha"] = datetime.strptime(f_str, "%d/%m/%Y").date()
        except:
            pass

    nits = re.findall(r'(?:NIT|N\.I\.T\.)[:\s]*([0-9Kk\-]{4,12})', texto, re.I)
    if len(nits) >= 1:
        datos["emisor_nit"] = nits[0].replace("-", "").upper()
    if len(nits) >= 2:
        datos["receptor_nit"] = nits[1].replace("-", "").upper()

    montos = re.findall(r'(?:TOTAL|Gran Total|Total General)[\s:]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos:
        m_val = float(montos[-1].replace(",", ""))
        datos["total"] = m_val
        datos["subtotal"] = round(m_val / 1.12, 2)
        
    return datos

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

DB_NAME = "matgoz_sistema_prod.db"

def get_db_connection():
    return sqlite3.connect(DB_NAME)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# INICIALIZACIÓN DE TABLAS Y BASE DE DATOS
# ==========================================
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Usuarios y Empresas
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, name TEXT, email TEXT,
        role TEXT, status TEXT, assigned_empresas TEXT, created_at TEXT, reset_requested INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id TEXT PRIMARY KEY, nombre TEXT, nit TEXT UNIQUE, direccion TEXT, telefono TEXT,
        regimen_isr TEXT, actividad_economica TEXT, created_at TEXT
    )''')

    # Directorio Corporativo de Terceros (Global y permanente)
    c.execute('''CREATE TABLE IF NOT EXISTS terceros_corp (
        id TEXT PRIMARY KEY, tipo TEXT, nit TEXT UNIQUE, nombre TEXT,
        direccion TEXT, telefono TEXT, created_at TEXT
    )''')

    # Directorio de Terceros específico por empresa
    c.execute('''CREATE TABLE IF NOT EXISTS terceros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id TEXT NOT NULL,
        tipo TEXT NOT NULL,
        nit TEXT NOT NULL,
        nombre TEXT NOT NULL,
        direccion TEXT,
        telefono TEXT,
        email TEXT,
        dias_credito INTEGER DEFAULT 0,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id),
        UNIQUE(empresa_id, nit)
    )''')

    # Módulo de Bancos (Independiente por empresa)
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas_bancarias (
        id TEXT PRIMARY KEY, empresa_id TEXT, nombre_banco TEXT,
        numero_cuenta TEXT, tipo_cuenta TEXT, moneda TEXT DEFAULT 'GTQ', created_at TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id)
    )''')

    # Bitácora de Transacciones con Tarjeta
    c.execute('''CREATE TABLE IF NOT EXISTS bitacora_tarjetas (
        id TEXT PRIMARY KEY, empresa_id TEXT, factura_id TEXT, tipo_movimiento TEXT,
        tipo_tarjeta TEXT, marca TEXT, ultimos_4 TEXT, autorizacion TEXT,
        monto REAL, fecha TEXT, registrado_por TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id)
    )''')

    # Facturas
    c.execute('''CREATE TABLE IF NOT EXISTS facturas (
        id TEXT PRIMARY KEY, empresa_id TEXT, tipo TEXT, numero_factura TEXT, serie TEXT, fecha TEXT,
        tercero_nombre TEXT, tercero_nit TEXT, descripcion TEXT, tipo_gasto_ingreso TEXT,
        cuenta_nomenclatura TEXT, cuenta_pago TEXT, forma_pago TEXT, subtotal REAL, iva REAL,
        total REAL, aplica_ret_isr INTEGER, tasa_ret_isr REAL, monto_ret_isr REAL,
        aplica_ret_iva INTEGER, tasa_ret_iva REAL, monto_ret_iva REAL, total_a_pagar_cobrar REAL,
        saldo_pendiente REAL DEFAULT 0, estado_pago TEXT DEFAULT 'PAGADO',
        created_by TEXT, created_at TEXT, FOREIGN KEY (empresa_id) REFERENCES empresas(id)
    )''')

    # Catálogo Nomenclatura Contable
    c.execute('''CREATE TABLE IF NOT EXISTS nomenclatura (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT, codigo TEXT NOT NULL,
        nombre TEXT NOT NULL, tipo TEXT NOT NULL, UNIQUE(empresa_id, codigo)
    )''')

    # Libro Diario
    c.execute('''CREATE TABLE IF NOT EXISTS libro_diario (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT NOT NULL, partida_no INTEGER NOT NULL,
        fecha TEXT NOT NULL, codigo_cuenta TEXT NOT NULL, nombre_cuenta TEXT NOT NULL,
        debe REAL DEFAULT 0, haber REAL DEFAULT 0, concepto TEXT, referencia_id TEXT, created_at TEXT
    )''')

    # Inventarios Kárdex
    c.execute('''CREATE TABLE IF NOT EXISTS productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT NOT NULL, codigo TEXT NOT NULL,
        descripcion TEXT NOT NULL, precio_venta REAL DEFAULT 0, costo_promedio REAL DEFAULT 0,
        existencia REAL DEFAULT 0, UNIQUE(empresa_id, codigo)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS kardex (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT NOT NULL, fecha TEXT NOT NULL,
        producto_id INTEGER NOT NULL, tipo_movimiento TEXT NOT NULL, cantidad REAL NOT NULL,
        costo_unitario REAL NOT NULL, total REAL NOT NULL, documento_ref TEXT,
        FOREIGN KEY (producto_id) REFERENCES productos(id)
    )''')

    # Cartera CXC y CXP
    c.execute('''CREATE TABLE IF NOT EXISTS movimientos_cartera (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT NOT NULL, factura_id TEXT NOT NULL,
        fecha TEXT NOT NULL, tipo TEXT NOT NULL, monto REAL NOT NULL, metodo_pago TEXT,
        cuenta_afectada TEXT, observaciones TEXT, created_at TEXT
    )''')

    # Usuario admin por defecto
    c.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if c.fetchone()[0] == 0:
        c.execute('''INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)''',
                  ("usr-admin", "admin", hash_password("admin123"), "Administrador Principal",
                   "admin@matgoz.com", "admin", "active", "all", datetime.now().isoformat()))

    conn.commit()
    conn.close()

init_db()

def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = ?", (empresa_id,))
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
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Prestaciones", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres", "Gasto"),
            ("5.1.02.04", "Combustibles", "Gasto"),
            ("5.1.02.99", "Gastos Generales", "Gasto"),
        ]
        c.executemany("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (?, ?, ?, ?)",
                      [(empresa_id, cod, nom, t) for cod, nom, t in base])
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
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE username = ? AND password_hash = ?", (u, hash_password(p)))
            row = c.fetchone()
            conn.close()
            if row:
                if row[5] == "pending_approval":
                    st.warning("Cuenta pendiente de autorización.")
                elif row[5] == "suspended":
                    st.error("Cuenta suspendida.")
                else:
                    st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                    st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")

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
        c.execute(f"SELECT id, nombre, nit, regimen_isr FROM empresas WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY nombre", ids)
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
        "🏦 Cuentas Bancarias",
        "⚖️ Transacciones Contables (Partidas Manuales)",
        "👥 Directorio de Terceros",
        "💳 Cuentas por Pagar (Proveedores)",
        "💰 Cuentas por Cobrar (Clientes)",
        "📦 Control de Inventarios (Kárdex)",
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
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id:
        st.info("Para comenzar, selecciona o registra una empresa.")
        st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen Fiscal: {selected_empresa_info[3]}")
    
    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT * FROM facturas WHERE empresa_id = ?", conn, params=(selected_emp_id,))
    cxp_tot = conn.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = ? AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,)).fetchone()[0]
    cxc_tot = conn.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM facturas WHERE empresa_id = ? AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'", (selected_emp_id,)).fetchone()[0]
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🛒 Compras del Mes", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Ventas del Mes", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c3.metric("💳 Cuentas por Pagar (CXP)", f"Q {cxp_tot:,.2f}")
    c4.metric("💰 Cuentas por Cobrar (CXC)", f"Q {cxc_tot:,.2f}")

# ==========================================
# 2. FACTURA DE COMPRAS (MANUAL / PDF + TARJETAS)
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
    st.title(f"🛒 Facturas de Compras — {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    prov_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre", conn)
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

            c6, c7 = st.columns(2)
            tipo_gasto = c6.selectbox("Concepto General", ["Mercaderías e Inventarios", "Servicios Profesionales", "Arrendamientos", "Suministros", "Combustibles", "Otros Gastos"])
            cuenta_nom = c7.selectbox("Cuenta de Nomenclatura (Debe) *", opciones_cuentas if opciones_cuentas else ["5.1.01.01 - Compras de Mercadería"])

            subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_datos.get("subtotal", 0.0)))

            st.markdown("---")
            st.markdown("#### Forma de Pago")
            forma_pago = st.selectbox("Método de Pago:", ["Contado - Efectivo", "Contado - Transferencia Bancaria", "Contado - Tarjeta", "Crédito (Cuentas por Pagar)"])

            datos_tarjeta = {}
            if forma_pago == "Contado - Tarjeta":
                st.info("💳 Completa los datos de la tarjeta para la bitácora:")
                t1, t2, t3, t4 = st.columns(4)
                datos_tarjeta["tipo"] = t1.selectbox("Tipo:", ["Crédito", "Débito"])
                datos_tarjeta["marca"] = t2.selectbox("Marca:", ["Visa", "MasterCard", "American Express"])
                datos_tarjeta["ultimos4"] = t3.text_input("Últimos 4 dígitos:", max_chars=4)
                datos_tarjeta["auth"] = t4.text_input("No. Autorización:")

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
                    
                    # 1. Guardar o actualizar automáticamente en el Directorio Corporativo
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (?, 'PROVEEDOR', ?, ?, 'Guatemala', '', ?)
                        ON CONFLICT(nit) DO UPDATE SET nombre=excluded.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prov_nit.strip().upper(), prov_nombre.strip(), datetime.now().isoformat()))

                    # 2. Guardar Factura
                    saldo = liq if forma_pago.startswith("Crédito") else 0.0
                    estado = "PENDIENTE" if forma_pago.startswith("Crédito") else "PAGADO"
                    
                    c.execute('''
                        INSERT INTO facturas VALUES (?, ?, 'COMPRA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nombre, prov_nit, tipo_gasto, tipo_gasto,
                          cuenta_nom, forma_pago, forma_pago, subtotal, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                          liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    # 3. Bitácora de tarjeta si aplica
                    if forma_pago == "Contado - Tarjeta":
                        c.execute('''
                            INSERT INTO bitacora_tarjetas VALUES (?, ?, ?, 'COMPRA', ?, ?, ?, ?, ?, ?, ?)
                        ''', (f"tarj-{int(datetime.now().timestamp())}", selected_emp_id, fac_id, datos_tarjeta.get("tipo", "Débito"),
                              datos_tarjeta.get("marca", "Visa"), datos_tarjeta.get("ultimos4", "0000"), datos_tarjeta.get("auth", "000000"),
                              liq, str(fecha_fac), curr_u['username']))

                    # 4. Asiento Contable en el Diario
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"Factura Compra {num_fac} de {prov_nombre}"
                    cod_d, nom_d = cuenta_nom.split(" - ", 1)
                    cta_h = "2.1.01.01" if forma_pago.startswith("Crédito") else ("1.1.01.02" if "Transferencia" in forma_pago else ("2.1.01.02" if "Tarjeta" in forma_pago else "1.1.01.01"))
                    nom_h = "Proveedores" if forma_pago.startswith("Crédito") else ("Bancos" if "Transferencia" in forma_pago else ("Tarjetas Corporativas" if "Tarjeta" in forma_pago else "Caja General"))

                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal, glosa, num_fac, now_str))
                    if iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.01', 'IVA por Cobrar', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))
                    if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.03.01', 'Retenciones ISR por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                    if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.03.02', 'Retenciones IVA por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cta_h, nom_h, liq, glosa, num_fac, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura registrada con éxito.")
                    st.rerun()
                else:
                    st.error("Por favor completa los campos obligatorios.")

    tab_fc, tab_tarj_c = st.tabs(["📋 Compras Registradas", "💳 Bitácora de Pagos con Tarjeta"])
    with tab_fc:
        conn = get_db_connection()
        df_c = pd.read_sql_query("SELECT fecha AS 'Fecha', serie AS 'Serie', numero_factura AS 'No. Factura', tercero_nombre AS 'Proveedor', subtotal AS 'Subtotal', iva AS 'IVA', total AS 'Total', total_a_pagar_cobrar AS 'Líquido', estado_pago AS 'Estado' FROM facturas WHERE empresa_id = ? AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_c, use_container_width=True)

    with tab_tarj_c:
        conn = get_db_connection()
        df_tc = pd.read_sql_query("SELECT fecha AS 'Fecha', tipo_tarjeta AS 'Tipo', marca AS 'Marca', ultimos_4 AS 'Últimos 4', autorizacion AS 'Autorización', monto AS 'Monto Q', registrado_por AS 'Usuario' FROM bitacora_tarjetas WHERE empresa_id = ? AND tipo_movimiento = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_tc, use_container_width=True)

# ==========================================
# 3. FACTURA DE VENTAS (MANUAL / PDF + TARJETAS)
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
    st.title(f"📈 Facturación de Ventas — {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
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

            st.markdown("---")
            st.markdown("#### Forma de Cobro")
            forma_cobro = st.selectbox("Método de Cobro:", ["Contado - Efectivo", "Contado - Transferencia / Depósito", "Contado - Tarjeta / POS", "Crédito (Cuentas por Cobrar)"])

            datos_tarjeta_v = {}
            if forma_cobro == "Contado - Tarjeta / POS":
                st.info("💳 Registra la información de la tarjeta cobrada:")
                vt1, vt2, vt3, vt4 = st.columns(4)
                datos_tarjeta_v["tipo"] = vt1.selectbox("Tipo:", ["Crédito", "Débito"], key="vt_tipo")
                datos_tarjeta_v["marca"] = vt2.selectbox("Marca:", ["Visa", "MasterCard", "American Express"], key="vt_marca")
                datos_tarjeta_v["ultimos4"] = vt3.text_input("Últimos 4 dígitos:", max_chars=4, key="vt_u4")
                datos_tarjeta_v["auth"] = vt4.text_input("No. Autorización / Voucher:", key="vt_auth")

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

                    # 1. Guardar o actualizar en el Directorio Corporativo
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (?, 'CLIENTE', ?, ?, 'Guatemala', '', ?)
                        ON CONFLICT(nit) DO UPDATE SET nombre=excluded.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", cli_nit.strip().upper(), cli_nombre.strip(), datetime.now().isoformat()))

                    # 2. Guardar Factura
                    c.execute('''
                        INSERT INTO facturas VALUES (?, ?, 'VENTA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nombre, cli_nit, concepto, concepto,
                          cuenta_ingreso, forma_cobro, forma_cobro, subtotal, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                          liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    # 3. Bitácora de tarjeta si aplica
                    if forma_cobro == "Contado - Tarjeta / POS":
                        c.execute('''
                            INSERT INTO bitacora_tarjetas VALUES (?, ?, ?, 'VENTA', ?, ?, ?, ?, ?, ?, ?)
                        ''', (f"tarj-{int(datetime.now().timestamp())}", selected_emp_id, fac_id, datos_tarjeta_v.get("tipo", "Crédito"),
                              datos_tarjeta_v.get("marca", "Visa"), datos_tarjeta_v.get("ultimos4", "0000"), datos_tarjeta_v.get("auth", "000000"),
                              liq, str(fecha_fac), curr_u['username']))

                    # 4. Asiento Contable en el Diario
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    glosa = f"Factura Venta {num_fac} a {cli_nombre}"
                    cod_h, nom_h = cuenta_ingreso.split(" - ", 1)
                    cta_d = "1.1.02.01" if forma_cobro.startswith("Crédito") else ("1.1.01.02" if "Transferencia" in forma_cobro else ("1.1.01.03" if "Tarjeta" in forma_cobro else "1.1.01.01"))
                    nom_d = "Clientes / Cuentas por Cobrar" if forma_cobro.startswith("Crédito") else ("Bancos" if "Transferencia" in forma_cobro else ("Tarjetas por Cobrar" if "Tarjeta" in forma_cobro else "Caja General"))

                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cta_d, nom_d, liq, glosa, num_fac, now_str))
                    if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.02', 'ISR Retenido por Acreditar', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                    if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.03', 'Constancias Retención IVA', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, subtotal, glosa, num_fac, now_str))
                    if iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.02.01', 'IVA por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Venta registrada y asentada con éxito.")
                    st.rerun()
                else:
                    st.error("Completa los datos obligatorios.")

    tab_fv, tab_tarj_v = st.tabs(["📋 Ventas Registradas", "💳 Bitácora de Cobros con Tarjeta"])
    with tab_fv:
        conn = get_db_connection()
        df_v = pd.read_sql_query("SELECT fecha AS 'Fecha', serie AS 'Serie', numero_factura AS 'No. Factura', tercero_nombre AS 'Cliente', subtotal AS 'Subtotal', iva AS 'IVA', total AS 'Total', total_a_pagar_cobrar AS 'Líquido', estado_pago AS 'Estado' FROM facturas WHERE empresa_id = ? AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_v, use_container_width=True)

    with tab_tarj_v:
        conn = get_db_connection()
        df_tv = pd.read_sql_query("SELECT fecha AS 'Fecha', tipo_tarjeta AS 'Tipo', marca AS 'Marca', ultimos_4 AS 'Últimos 4', autorizacion AS 'Autorización', monto AS 'Monto Q', registrado_por AS 'Usuario' FROM bitacora_tarjetas WHERE empresa_id = ? AND tipo_movimiento = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_tv, use_container_width=True)

# ==========================================
# 4. MÓDULO DE BANCOS (INDEPENDIENTE POR EMPRESA)
# ==========================================
elif selected_menu == "🏦 Cuentas Bancarias":
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
        
    st.title(f"🏦 Gestión de Cuentas Bancarias — {selected_empresa_info[1]}")
    st.caption("Cuentas bancarias independientes registradas para esta empresa.")
    
    with st.expander("➕ Registrar Nueva Cuenta Bancaria", expanded=True):
        with st.form("form_bancos", clear_on_submit=True):
            cb1, cb2 = st.columns(2)
            banco = cb1.selectbox("Entidad Bancaria", [
                "Banco Industrial", "BAC Credomatic", "Banrural", 
                "G&T Continental", "Interbanco", "Banco Promerica", "Otro"
            ])
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
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (f"cta-{int(datetime.now().timestamp())}", selected_emp_id, banco, num_cta.strip(), tipo_cta, moneda[:3], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("✅ Cuenta bancaria registrada con éxito.")
                    st.rerun()
                else:
                    st.error("El número de cuenta es obligatorio.")

    conn = get_db_connection()
    df_ctas = pd.read_sql_query(
        "SELECT nombre_banco AS 'Banco', numero_cuenta AS 'Número de Cuenta', tipo_cuenta AS 'Tipo', moneda AS 'Moneda', created_at AS 'Fecha de Registro' FROM cuentas_bancarias WHERE empresa_id = ? ORDER BY created_at DESC", 
        conn, params=(selected_emp_id,)
    )
    conn.close()
    
    st.subheader("Cuentas Bancarias Registradas")
    if not df_ctas.empty:
        st.dataframe(df_ctas, use_container_width=True)
    else:
        st.info("No hay cuentas bancarias registradas en esta empresa.")

# ==========================================
# 5. TRANSACCIONES CONTABLES (PARTIDAS MANUALES)
# ==========================================
elif selected_menu == "⚖️ Transacciones Contables (Partidas Manuales)":
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
        
    st.title(f"⚖️ Transacciones Contables — {selected_empresa_info[1]}")
    st.caption("Crea partidas manuales de ajuste, apertura, transferencias o regularizaciones con validación de partida doble.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    
    opciones_cuentas = [f"{r['codigo']} - {r['nombre']}" for _, r in df_nom.iterrows()]

    if not opciones_cuentas:
        st.warning("Primero debes registrar cuentas en la Nomenclatura Contable.")
        st.stop()

    if "partida_manual_lineas" not in st.session_state:
        st.session_state.partida_manual_lineas = []

    with st.expander("➕ Elaborar Nueva Partida Contable", expanded=True):
        col_f1, col_f2 = st.columns([1, 2])
        p_fecha = col_f1.date_input("Fecha de la Partida", value=date.today(), key="pm_fecha")
        p_concepto = col_f2.text_input("Concepto / Glosa General *", placeholder="Ej: Registro por ajuste de depreciación / traslado bancario", key="pm_concepto")
        
        st.markdown("##### 📌 Agregar Renglones a la Partida")
        col_l1, col_l2, col_l3, col_l4 = st.columns([2.5, 1, 1, 0.8])
        cta_sel = col_l1.selectbox("Cuenta Contable", opciones_cuentas, key="pm_cta")
        monto_debe = col_l2.number_input("Debe (Q)", min_value=0.0, step=10.0, format="%.2f", key="pm_debe")
        monto_haber = col_l3.number_input("Haber (Q)", min_value=0.0, step=10.0, format="%.2f", key="pm_haber")
        
        if col_l4.button("➕ Añadir Línea", use_container_width=True):
            if monto_debe > 0 and monto_haber > 0:
                st.error("Una misma línea no puede tener valores en el Debe y el Haber al mismo tiempo.")
            elif monto_debe == 0 and monto_haber == 0:
                st.error("Ingresa un monto en el Debe o en el Haber.")
            else:
                cod, nom = cta_sel.split(" - ", 1)
                st.session_state.partida_manual_lineas.append({
                    "codigo": cod,
                    "nombre": nom,
                    "debe": monto_debe,
                    "haber": monto_haber
                })
                st.rerun()

        if st.session_state.partida_manual_lineas:
            st.markdown("##### Vista Previa del Asiento:")
            df_prev = pd.DataFrame(st.session_state.partida_manual_lineas)
            st.dataframe(df_prev[["codigo", "nombre", "debe", "haber"]], use_container_width=True)
            
            tot_debe = round(df_prev["debe"].sum(), 2)
            tot_haber = round(df_prev["haber"].sum(), 2)
            diferencia = round(abs(tot_debe - tot_haber), 2)
            
            c_sum1, c_sum2, c_sum3 = st.columns(3)
            c_sum1.metric("Total Debe", f"Q {tot_debe:,.2f}")
            c_sum2.metric("Total Haber", f"Q {tot_haber:,.2f}")
            c_sum3.metric("Diferencia", f"Q {diferencia:,.2f}", delta_color="inverse")

            btn_col1, btn_col2 = st.columns(2)
            if btn_col1.button("🗑️ Limpiar Renglones"):
                st.session_state.partida_manual_lineas = []
                st.rerun()

            if btn_col2.button("💾 Asentar Partida en el Libro Diario", type="primary"):
                if not p_concepto.strip():
                    st.error("Ingresa el Concepto / Glosa de la partida.")
                elif tot_debe == 0 or tot_haber == 0:
                    st.error("La partida no puede registrarse en Q0.00.")
                elif tot_debe != tot_haber:
                    st.error(f"La partida no está cuadrada. Hay una diferencia de Q{diferencia:,.2f}.")
                else:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    
                    for r in st.session_state.partida_manual_lineas:
                        c.execute('''
                            INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (selected_emp_id, p_no, str(p_fecha), r["codigo"], r["nombre"], r["debe"], r["haber"], p_concepto.strip(), f"MANUAL-{p_no}", now_str))
                    
                    conn.commit()
                    conn.close()
                    st.session_state.partida_manual_lineas = []
                    st.success(f"✅ Partida No. {p_no} registrada con éxito.")
                    st.rerun()

# ==========================================
# 6. DIRECTORIO DE TERCEROS (CORPORATIVO GLOBAL)
# ==========================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    st.caption("Los terceros registrados aquí están disponibles globalmente en todas tus empresas.")

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
                        conn.execute('''
                            INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (f"terc-{int(datetime.now().timestamp())}", t_tipo, t_nit, t_nom, t_dir, t_tel, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"✅ Tercero '{t_nom}' registrado corporativamente.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un tercero registrado con este NIT.")
                    finally:
                        conn.close()
                else:
                    st.error("El NIT y el Nombre son obligatorios.")

    conn = get_db_connection()
    df_t = pd.read_sql_query("SELECT tipo AS 'Tipo', nit AS 'NIT', nombre AS 'Nombre / Razón Social', direccion AS 'Dirección', telefono AS 'Teléfono' FROM terceros_corp ORDER BY nombre ASC", conn)
    conn.close()
    st.dataframe(df_t, use_container_width=True)

# ==========================================
# 7. CUENTAS POR PAGAR (CXP)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title(f"💳 Control de Cuentas por Pagar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
                                  FROM facturas WHERE empresa_id = ? AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxp.empty:
        st.success("✨ Todas las facturas de proveedores están completamente canceladas.")
    else:
        st.dataframe(df_cxp, use_container_width=True)
        with st.form("form_abono_cxp"):
            st.subheader("Registrar Pago o Abono a Proveedor")
            fac_sel = st.selectbox("Seleccionar Factura a Pagar:", df_cxp['id'].tolist(), format_func=lambda x: f"Fac {df_cxp[df_cxp['id']==x]['numero_factura'].values[0]} - {df_cxp[df_cxp['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxp[df_cxp['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_abono = st.number_input("Monto a Pagar (Q)", min_value=0.01, step=50.0)
            metodo_pago = st.selectbox("Forma de Pago:", ["Transferencia Bancaria (Bancos)", "Efectivo (Caja)"])
            cta_origen = "1.1.01.02" if "Bancos" in metodo_pago else "1.1.01.01"

            if st.form_submit_button("Aplicar Pago", type="primary"):
                row_f = df_cxp[df_cxp['id'] == fac_sel].iloc[0]
                if monto_abono > row_f['saldo_pendiente']:
                    st.error("El abono no puede ser mayor al saldo pendiente.")
                else:
                    nuevo_saldo = round(row_f['saldo_pendiente'] - monto_abono, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = ?, estado_pago = ? WHERE id = ?", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.01.01', 'Proveedores / Cuentas por Pagar', ?, 0, ?, ?, ?)",
                              (selected_emp_id, p_no, str(date.today()), monto_abono, f"Abono Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, 'Salida de Fondos', 0, ?, ?, ?, ?)",
                              (selected_emp_id, p_no, str(date.today()), cta_origen, monto_abono, f"Pago Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    st.success("Pago registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

# ==========================================
# 8. CUENTAS POR COBRAR (CXC)
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title(f"💰 Control de Cuentas por Cobrar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
                                  FROM facturas WHERE empresa_id = ? AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxc.empty:
        st.success("✨ Todos los clientes están solventes al día.")
    else:
        st.dataframe(df_cxc, use_container_width=True)
        with st.form("form_abono_cxc"):
            st.subheader("Registrar Cobro a Cliente")
            fac_sel = st.selectbox("Seleccionar Factura a Cobrar:", df_cxc['id'].tolist(), format_func=lambda x: f"Fac {df_cxc[df_cxc['id']==x]['numero_factura'].values[0]} - {df_cxc[df_cxc['id']==x]['tercero_nombre'].values[0]} (Saldo: Q{df_cxc[df_cxc['id']==x]['saldo_pendiente'].values[0]:,.2f})")
            monto_cobro = st.number_input("Monto Recibido (Q)", min_value=0.01, step=50.0)
            metodo_cobro = st.selectbox("Ingreso a:", ["Depósito Bancario (Bancos)", "Efectivo (Caja)"])
            cta_destino = "1.1.01.02" if "Bancos" in metodo_cobro else "1.1.01.01"

            if st.form_submit_button("Aplicar Cobro", type="primary"):
                row_v = df_cxc[df_cxc['id'] == fac_sel].iloc[0]
                if monto_cobro > row_v['saldo_pendiente']:
                    st.error("El cobro no puede ser mayor al saldo adeudado.")
                else:
                    nuevo_saldo = round(row_v['saldo_pendiente'] - monto_cobro, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = ?, estado_pago = ? WHERE id = ?", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, 'Ingreso de Fondos', ?, 0, ?, ?, ?)",
                              (selected_emp_id, p_no, str(date.today()), cta_destino, monto_cobro, f"Cobro Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.02.01', 'Clientes / Cuentas por Cobrar', 0, ?, ?, ?, ?)",
                              (selected_emp_id, p_no, str(date.today()), monto_cobro, f"Cancelación parcial Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    conn.commit()
                    st.success("Cobro registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

# ==========================================
# 9. INVENTARIO (KÁRDEX)
# ==========================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    if not selected_emp_id: st.stop()
    st.title("📦 Inventario y Kárdex Ponderado")

    with st.expander("➕ Crear Nuevo Producto en Inventario"):
        with st.form("form_prod"):
            c1, c2, c3 = st.columns(3)
            p_cod = c1.text_input("Código de Artículo *")
            p_nom = c2.text_input("Descripción *")
            p_pre = c3.number_input("Precio de Venta Sugerido", min_value=0.0)
            if st.form_submit_button("Crear Producto"):
                if p_cod and p_nom:
                    conn = get_db_connection()
                    try:
                        conn.execute("INSERT INTO productos (empresa_id, codigo, descripcion, precio_venta) VALUES (?, ?, ?, ?)",
                                     (selected_emp_id, p_cod.strip(), p_nom.strip(), p_pre))
                        conn.commit()
                        st.success("Producto creado con éxito.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un producto con este código.")
                    conn.close()

    conn = get_db_connection()
    df_p = pd.read_sql_query("SELECT id, codigo AS 'Código', descripcion AS 'Descripción', existencia AS 'Stock Actual', costo_promedio AS 'Costo Promedio (Q)', precio_venta AS 'Precio Venta (Q)' FROM productos WHERE empresa_id = ?", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_p, use_container_width=True)

# ==========================================
# 10. LIBRO DE COMPRAS (SAT + PDF)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_compras_sat = pd.read_sql_query("""
        SELECT fecha AS 'Fecha', serie AS 'Serie', numero_factura AS 'Factura', tercero_nit AS 'NIT Proveedor',
               tercero_nombre AS 'Proveedor', subtotal AS 'Base Imponible (Q)', iva AS 'Crédito Fiscal (Q)',
               monto_ret_isr AS 'Ret. ISR (Q)', monto_ret_iva AS 'Ret. IVA (Q)', total AS 'Total Factura (Q)'
        FROM facturas WHERE empresa_id = ? AND tipo = 'COMPRA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_compras_sat.empty:
        st.info("Sin registros de compras.")
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

# ==========================================
# 11. LIBRO DE VENTAS (SAT + PDF)
# ==========================================
elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Ventas Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ventas_sat = pd.read_sql_query("""
        SELECT fecha AS 'Fecha', serie AS 'Serie', numero_factura AS 'Factura', tercero_nit AS 'NIT Cliente',
               tercero_nombre AS 'Cliente', subtotal AS 'Base Imponible (Q)', iva AS 'Débito Fiscal (Q)',
               monto_ret_isr AS 'Ret. ISR Soportada (Q)', total AS 'Total Factura (Q)'
        FROM facturas WHERE empresa_id = ? AND tipo = 'VENTA' ORDER BY fecha ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_ventas_sat.empty:
        st.info("Sin registros de ventas.")
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

# ==========================================
# 12. LIBRO DE RETENCIONES
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title("📑 Libro de Retenciones Fiscales (SAT Guatemala)")
    conn = get_db_connection()
    df_r = pd.read_sql_query("SELECT tipo AS 'Operación', fecha AS 'Fecha', numero_factura AS 'Documento', tercero_nombre AS 'Tercero', subtotal AS 'Base (Q)', monto_ret_isr AS 'Retención ISR (Q)', monto_ret_iva AS 'Retención IVA (Q)' FROM facturas WHERE empresa_id = ? AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_r.empty:
        st.dataframe(df_r, use_container_width=True)
    else:
        st.info("No hay retenciones registradas en esta empresa.")

# ==========================================
# 13. LIBRO DIARIO
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("SELECT partida_no AS 'Partida #', fecha AS 'Fecha', codigo_cuenta AS 'Código', nombre_cuenta AS 'Cuenta', debe AS 'Debe (Q)', haber AS 'Haber (Q)', concepto AS 'Glosa' FROM libro_diario WHERE empresa_id = ? ORDER BY partida_no ASC, id ASC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_d.empty:
        c1, c2 = st.columns(2)
        c1.metric("Total DEBE", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total HABER", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)
    else:
        st.info("No hay partidas registradas en el Libro Diario.")

# ==========================================
# 14. LIBRO MAYOR
# ==========================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title("📚 Libro Mayor General")
    conn = get_db_connection()
    df_m = pd.read_sql_query("""SELECT codigo_cuenta AS 'Código', nombre_cuenta AS 'Cuenta',
                                       ROUND(SUM(debe), 2) AS 'Total Debe (Q)', ROUND(SUM(haber), 2) AS 'Total Haber (Q)',
                                       ROUND(SUM(debe) - SUM(haber), 2) AS 'Saldo (Q)'
                                FROM libro_diario WHERE empresa_id = ? GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta""", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_m, use_container_width=True)

# ==========================================
# 15. BALANCE GENERAL
# ==========================================
elif selected_menu == "🏛️ Balance General":
    if not selected_emp_id: st.stop()
    st.title("🏛️ Balance General de Saldos")
    conn = get_db_connection()
    query = """
        SELECT n.codigo, n.nombre, n.tipo, COALESCE(SUM(d.debe), 0) AS total_debe, COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = ? GROUP BY n.codigo, n.nombre, n.tipo HAVING total_debe > 0 OR total_haber > 0 ORDER BY n.codigo
    """
    df_b = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()
    if not df_b.empty:
        df_b['Saldo Deudor'] = df_b.apply(lambda r: round(r['total_debe'] - r['total_haber'], 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
        df_b['Saldo Acreedor'] = df_b.apply(lambda r: round(r['total_haber'] - r['total_debe'], 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)
        st.dataframe(df_b[['codigo', 'nombre', 'tipo', 'Saldo Deudor', 'Saldo Acreedor']], use_container_width=True)
    else:
        st.info("No hay movimientos contables registrados para generar el Balance.")

# ==========================================
# 16. ESTADO DE RESULTADOS
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados (Pérdidas y Ganancias)")
    conn = get_db_connection()
    q_i = "SELECT n.nombre, ROUND(COALESCE(SUM(d.haber - d.debe), 0), 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = ? AND n.tipo = 'Ingreso' GROUP BY n.nombre"
    q_g = "SELECT n.nombre, ROUND(COALESCE(SUM(d.debe - d.haber), 0), 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = ? AND n.tipo = 'Gasto' GROUP BY n.nombre"
    df_i = pd.read_sql_query(q_i, conn, params=(selected_emp_id,))
    df_g = pd.read_sql_query(q_g, conn, params=(selected_emp_id,))
    conn.close()
    tot_i = df_i['monto'].sum() if not df_i.empty else 0.0
    tot_g = df_g['monto'].sum() if not df_g.empty else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos Totales", f"Q {tot_i:,.2f}")
    c2.metric("Costos y Gastos", f"Q {tot_g:,.2f}")
    c3.metric("Utilidad Neta", f"Q {tot_i - tot_g:,.2f}")

# ==========================================
# 17. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
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
                    conn.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (?, ?, ?, ?)", (selected_emp_id, cod_c.strip(), nom_c.strip(), tip_c))
                    conn.commit()
                    st.success("Cuenta guardada exitosamente.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("El código ya existe para esta empresa.")
                finally:
                    conn.close()
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS 'Código', nombre AS 'Cuenta', tipo AS 'Tipo' FROM nomenclatura WHERE empresa_id = ? ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 18. CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👤 Control de Usuarios (Admin)":
    if not is_admin: 
        st.stop()
    st.title("👤 Control de Operadores y Asignación de Empresas")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_emps = c.fetchall()
    conn.close()

    dict_emps = {e[0]: f"{e[1]} (NIT: {e[2]})" for e in all_emps}
    tab_crear, tab_registrados = st.tabs(["➕ Crear Nuevo Usuario", "📋 Usuarios Registrados y Accesos"])

    with tab_crear:
        with st.form("crear_usuario_form"):
            c1, c2 = st.columns(2)
            u_user = c1.text_input("Usuario (ej: operador1) *")
            u_nombre = c2.text_input("Nombre Completo *")

            c3, c4 = st.columns(2)
            u_email = c3.text_input("Correo Electrónico")
            u_pass = c4.text_input("Contraseña de Acceso *", type="password")

            rol_opcion = st.selectbox("Perfil / Rol:", ["Operador (Solo Registra Facturas)", "Supervisor (Auditoría y Reportes)"])
            rol_final = "supervisor" if "Supervisor" in rol_opcion else "operator"

            st.markdown("##### 🏢 Empresas a las que tendrá acceso:")
            empresas_seleccionadas = []
            for emp in all_emps:
                if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"new_emp_{emp[0]}", value=True):
                    empresas_seleccionadas.append(emp[0])

            if st.form_submit_button("✅ Crear Usuario", type="primary"):
                if u_user.strip() and u_nombre.strip() and u_pass.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        assigned_txt = ",".join(empresas_seleccionadas)
                        uid = f"usr-{int(datetime.now().timestamp())}"
                        c.execute('''
                            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (uid, u_user.strip(), hash_password(u_pass.strip()), u_nombre.strip(), u_email.strip(), rol_final, "active", assigned_txt, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Usuario '@{u_user}' creado exitosamente.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("El nombre de usuario ya existe.")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el Usuario, Nombre y Contraseña.")

    with tab_registrados:
        st.subheader("Directorio de Cuentas")
        if not users_list:
            st.info("No hay operadores registrados aún.")
        else:
            for u in users_list:
                uid, uname, name, email, role, status, assigned = u
                with st.expander(f"👤 {name} (@{uname}) — Perfil: {role.upper()}"):
                    col1, col2 = st.columns([1.5, 2])
                    with col1:
                        st.write(f"**Usuario:** `{uname}`")
                        st.write(f"**Correo:** {email or 'Sin registrar'}")
                        st.write(f"**Estado:** `{status}`")
                        nueva_clave = st.text_input(f"Restablecer clave de @{uname}", type="password", key=f"pwd_{uid}")
                        if st.button(f"🔑 Actualizar Clave", key=f"btn_pwd_{uid}"):
                            if nueva_clave.strip():
                                conn = get_db_connection()
                                c = conn.cursor()
                                c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(nueva_clave.strip()), uid))
                                conn.commit()
                                conn.close()
                                st.success("Clave cambiada con éxito.")
                            else:
                                st.error("Ingresa una contraseña válida.")

                    with col2:
                        st.markdown("**🏢 Empresas Autorizadas:**")
                        ids_asig = [x.strip() for x in (assigned or "").split(",") if x.strip()]
                        for eid in ids_asig:
                            st.markdown(f"- **{dict_emps.get(eid, 'Empresa no encontrada')}**")
                        st.markdown("---")
                        st.caption("Modificar permisos de empresas:")
                        nuevos_accesos = []
                        for emp in all_emps:
                            tiene = emp[0] in ids_asig
                            if st.checkbox(f"{emp[1]}", value=tiene, key=f"chk_{uid}_{emp[0]}"):
                                nuevos_accesos.append(emp[0])
                        if st.button(f"💾 Guardar Empresas para @{uname}", key=f"btn_save_{uid}"):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("UPDATE users SET assigned_empresas = ? WHERE id = ?", (",".join(nuevos_accesos), uid))
                            conn.commit()
                            conn.close()
                            st.success("Permisos actualizados.")
                            st.rerun()

# ==========================================
# 19. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: 
        st.error("Acceso exclusivo para el Administrador.")
        st.stop()
        
    st.title("🏢 Gestión y Modificación de Empresas Fiscales")
    tab_nueva_emp, tab_lista_emp = st.tabs(["➕ Registrar Nueva Empresa", "📋 Consultar y Modificar Empresas"])
    
    with tab_nueva_emp:
        with st.form("form_nueva_empresa"):
            st.subheader("➕ Datos de la Nueva Empresa")
            c1, c2 = st.columns(2)
            e_nom = c1.text_input("Razón Social / Nombre Comercial *")
            e_nit = c2.text_input("NIT de la Empresa *")
            
            c3, c4 = st.columns(2)
            e_dir = c3.text_input("Dirección Fiscal")
            e_tel = c4.text_input("Teléfono de Contacto")
            
            e_reg = st.selectbox("Régimen de ISR SAT", [
                "Opcional Simplificado (5% y 7%)",
                "Sobre las Utilidades de Actividades Lucrativas (25%)",
                "Pequeño Contribuyente (5%)"
            ])
            
            if st.form_submit_button("✅ Guardar Empresa", type="primary"):
                if e_nom.strip() and e_nit.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        eid = f"emp-{int(datetime.now().timestamp())}"
                        c.execute('''
                            INSERT INTO empresas (id, nombre, nit, direccion, telefono, regimen_isr, actividad_economica, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (eid, e_nom.strip(), e_nit.strip(), e_dir.strip(), e_tel.strip(), e_reg, "Servicios Contables", datetime.now().isoformat()))
                        conn.commit()
                        sembrar_nomenclatura_inicial(eid)
                        st.success(f"✅ Empresa '{e_nom}' registrada con éxito.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe una empresa registrada con ese número de NIT.")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el Nombre y el NIT.")

    with tab_lista_emp:
        st.subheader("Directorio de Entidades Registradas")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, nombre, nit, direccion, telefono, regimen_isr, created_at FROM empresas ORDER BY nombre")
        empresas_registradas = c.fetchall()
        conn.close()
        
        if not empresas_registradas:
            st.info("No hay empresas registradas aún.")
        else:
            for emp in empresas_registradas:
                e_id, nom, nit, dire, tel, reg, fec_crea = emp
                with st.expander(f"🏢 {nom} — NIT: {nit}", expanded=False):
                    st.caption(f"Fecha de Creación: {fec_crea or 'No disponible'}")
                    with st.form(f"form_modificar_{e_id}"):
                        c1, c2 = st.columns(2)
                        nuevo_nom = c1.text_input("Nombre / Razón Social *", value=nom)
                        nuevo_nit = c2.text_input("NIT Fiscal *", value=nit)
                        
                        c3, c4 = st.columns(2)
                        nueva_dir = c3.text_input("Dirección Fiscal", value=dire or "")
                        nuevo_tel = c4.text_input("Teléfono", value=tel or "")
                        
                        regimenes = [
                            "Opcional Simplificado (5% y 7%)",
                            "Sobre las Utilidades de Actividades Lucrativas (25%)",
                            "Pequeño Contribuyente (5%)"
                        ]
                        idx_reg = regimenes.index(reg) if reg in regimenes else 0
                        nuevo_reg = st.selectbox("Régimen Tributario SAT", regimenes, index=idx_reg)
                        
                        if st.form_submit_button("💾 Guardar Cambios de la Empresa", type="primary"):
                            if nuevo_nom.strip() and nuevo_nit.strip():
                                conn = get_db_connection()
                                c = conn.cursor()
                                try:
                                    c.execute('''
                                        UPDATE empresas 
                                        SET nombre = ?, nit = ?, direccion = ?, telefono = ?, regimen_isr = ?
                                        WHERE id = ?
                                    ''', (nuevo_nom.strip(), nuevo_nit.strip(), nueva_dir.strip(), nuevo_tel.strip(), nuevo_reg, e_id))
                                    conn.commit()
                                    st.success(f"Datos de '{nuevo_nom}' actualizados exitosamente.")
                                    st.rerun()
                                except sqlite3.IntegrityError:
                                    st.error("El NIT ingresado ya le pertenece a otra empresa.")
                                finally:
                                    conn.close()
                            else:
                                st.error("El Nombre y el NIT no pueden quedar vacíos.")
                    
                    st.markdown("---")
                    if st.button(f"🗑️ Eliminar Empresa '{nom}'", key=f"del_emp_{e_id}"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("DELETE FROM facturas WHERE empresa_id = ?", (e_id,))
                        c.execute("DELETE FROM libro_diario WHERE empresa_id = ?", (e_id,))
                        c.execute("DELETE FROM nomenclatura WHERE empresa_id = ?", (e_id,))
                        c.execute("DELETE FROM cuentas_bancarias WHERE empresa_id = ?", (e_id,))
                        c.execute("DELETE FROM bitacora_tarjetas WHERE empresa_id = ?", (e_id,))
                        c.execute("DELETE FROM empresas WHERE id = ?", (e_id,))
                        conn.commit()
                        conn.close()
                        st.warning(f"Empresa '{nom}' y todos sus registros vinculados fueron eliminados.")
                        st.rerun()