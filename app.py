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
            CREATE TABLE IF NOT EXISTS libro_diario (
                id SERIAL PRIMARY KEY,
                empresa_id TEXT NOT NULL,
                partida_no INTEGER NOT NULL,
                fecha DATE NOT NULL,
                codigo_cuenta TEXT NOT NULL,
                nombre_cuenta TEXT NOT NULL,
                debe NUMERIC DEFAULT 0,
                haber NUMERIC DEFAULT 0,
                concepto TEXT,
                referencia_id TEXT,
                created_at TEXT
            );
        """)
        # Asegurar columnas compatibles para evitar errores de UndefinedColumn
        try:
            c.execute("ALTER TABLE libro_diario ADD COLUMN IF NOT EXISTS referencia_id TEXT;")
            c.execute("ALTER TABLE libro_diario ADD COLUMN IF NOT EXISTS documento_ref TEXT;")
            conn.commit()
        except Exception:
            conn.rollback()

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
            CREATE TABLE IF NOT EXISTS nomenclatura (
                id TEXT PRIMARY KEY,
                empresa_id TEXT NOT NULL,
                codigo TEXT NOT NULL,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL
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
                ("1.2.01.01", "Mobiliario y Equipo", "Activo"),
                ("1.2.01.02", "Vehículos de Reparto", "Activo"),
                ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
                ("2.1.01.02", "Tarjetas de Crédito Corporativas por Pagar", "Pasivo"),
                ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
                ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
                ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
                ("2.1.03.03", "Provisión Impuesto Sobre la Renta por Pagar", "Pasivo"),
                ("3.1.01.01", "Capital Social / Individual", "Capital"),
                ("3.1.02.01", "Resultados de Ejercicios Anteriores", "Capital"),
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
            c.executemany("""
                INSERT INTO nomenclatura (id, empresa_id, codigo, nombre, tipo) 
                VALUES (%s, %s, %s, %s, %s)
            """, [(f"nom-{empresa_id}-{cod}", empresa_id, cod, nom, t) for cod, nom, t in base])
            conn.commit()
        conn.close()
    except Exception:
        pass

def calcular_impuesto_renta(regimen_empresa, total_ingresos, utilidad_antes_impuestos):
    impuesto = 0.0
    tasa_desc = "0%"
    if "Pequeño Contribuyente" in str(regimen_empresa):
        impuesto = round(total_ingresos * 0.05, 2)
        tasa_desc = "5% s/ Ingresos Brutos"
    elif "Opcional Simplificado" in str(regimen_empresa):
        if total_ingresos <= 30000.0:
            impuesto = round(total_ingresos * 0.05, 2)
            tasa_desc = "5% Régimen Opcional"
        else:
            impuesto = round(1500.0 + (total_ingresos - 30000.0) * 0.07, 2)
            tasa_desc = "5% hasta Q30k y 7% s/ Excedente"
    elif "Sobre las Utilidades" in str(regimen_empresa):
        if utilidad_antes_impuestos > 0:
            impuesto = round(utilidad_antes_impuestos * 0.25, 2)
            tasa_desc = "25% s/ Renta Imponible"
        else:
            impuesto = 0.0
            tasa_desc = "25% (Pérdida - Q0.00)"
    else:
        impuesto = 0.0
        tasa_desc = "No configurado"
    ganancia_neta = round(utilidad_antes_impuestos - impuesto, 2)
    return impuesto, ganancia_neta, tasa_desc

# ==========================================
# EXPORTACIONES OFICIALES (PDF CON FOLIO Y EXCEL)
# ==========================================
def exportar_libro_pdf(df_datos, titulo_libro, empresa_nom, empresa_nit, resolucion_sat="2026-5-12345-001", folio_inicio=1):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter) if len(df_datos.columns) > 4 else letter,
        leftMargin=20, rightMargin=20, topMargin=30, bottomMargin=30
    )
    elementos = []
    estilos = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=11, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)
    
    elementos.append(Paragraph(f"<b>{empresa_nom.upper()}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {empresa_nit} | <b>{titulo_libro.upper()}</b>", estilo_sub))
    if resolucion_sat:
        elementos.append(Paragraph(f"Resolución SAT No.: {resolucion_sat}", estilo_sub))
    elementos.append(Spacer(1, 8))
    
    cols = [Paragraph(f"<b>{c}</b>", ParagraphStyle('H', parent=estilos['Normal'], fontSize=6.5, fontName='Helvetica-Bold')) for c in df_datos.columns]
    datos_tabla = [cols]
    
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            texto = f"Q {val:,.2f}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val if val is not None else "")
            fila_txt.append(Paragraph(texto, ParagraphStyle('C', parent=estilos['Normal'], fontSize=6.5)))
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
        canvas.setFont('Helvetica', 7.5)
        folio_actual = folio_inicio + canvas._pageNumber - 1
        pie_txt = f"Folio No. {folio_actual}  |  Resolución SAT: {resolucion_sat}"
        canvas.drawRightString(760 if len(df_datos.columns) > 4 else 580, 15, pie_txt)
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
# LECTOR PDF FACTURA SAT (FEL)
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

    m_aut = re.search(r'(?:NÚMERO\s+DE\s+AUTORIZACI[OÓ]N|Autorizaci[oó]n|UUID)[\s:\n\r]+([A-F0-9\-]{32,36})', texto, re.I)
    if m_aut:
        datos["numero_dte"] = m_aut.group(1).strip()

    m_ser = re.search(r'(?:SERIE|Serie)[\s:\n\r]+([A-F0-9]+)', texto)
    if m_ser:
        datos["serie"] = m_ser.group(1).strip()

    m_dte_num = re.search(r'(?:N[uú]mero\s+de\s+DTE|N[UÚ]MERO\s+DTE)[\s:\n\r]+([0-9]+)', texto, re.I)
    if m_dte_num and not datos["numero_dte"]:
        datos["numero_dte"] = m_dte_num.group(1).strip()

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

    montos = re.findall(r'(?:TOTAL(?:ES)?|Gran\s+Total|Total\s+General)[\s:\n\r]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos:
        m_val = float(montos[-1].replace(",", ""))
        datos["total"] = m_val
        datos["subtotal"] = round(m_val / 1.12, 2)
        
    return datos

# ==========================================
# MANEJO DE SESIÓN Y LOGIN
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
# SIDEBAR
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
# 2. FACTURA DE VENTAS (MONTO TOTAL)
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

            total_factura_v = st.number_input("Monto Total de la Factura (Q) *", min_value=0.0, value=float(pre_v.get("total", 0.0)), step=100.0, format="%.2f", key=f"v_tot_{fk_v}")

            es_pequeno_v = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera débito fiscal IVA)", value=pre_v.get("es_pequeno", False), key=f"v_peq_{fk_v}")

            if es_pequeno_v:
                subtotal_v = float(total_factura_v)
                iva_v = 0.0
            else:
                subtotal_v = round(float(total_factura_v) / 1.12, 2)
                iva_v = round(float(total_factura_v) - subtotal_v, 2)

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

            liq_v = round(float(total_factura_v) - m_isr_v - m_iva_v, 2)

            st.caption(f"Base Imponible: **Q {subtotal_v:,.2f}** | IVA Débito Fiscal: **Q {iva_v:,.2f}** | Líquido a Recibir: **Q {liq_v:,.2f}**")

            partida_v = [{"Cuenta": cta_debe_v, "DEBE": f"Q {liq_v:,.2f}", "HABER": "Q 0.00"}]
            if m_isr_v > 0: partida_v.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr_v:,.2f}", "HABER": "Q 0.00"})
            if m_iva_v > 0: partida_v.append({"Cuenta": "1.1.03.03 - Constancias Retención IVA", "DEBE": f"Q {m_iva_v:,.2f}", "HABER": "Q 0.00"})
            partida_v.append({"Cuenta": cta_haber_v, "DEBE": "Q 0.00", "HABER": f"Q {subtotal_v:,.2f}"})
            if iva_v > 0: partida_v.append({"Cuenta": "2.1.02.01 - IVA por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {iva_v:,.2f}"})
            st.table(pd.DataFrame(partida_v))

            if st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary"):
                if not num_fac or not cli_nombre or not cli_nit or float(total) <= 0:
                    st.error("⚠️ Completa los campos obligatorios: Número DTE, Cliente, NIT y Total válido.")
                else:
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        
                        fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                        now_str = datetime.now().isoformat()
                        
                        # 1. Inserción protegida en facturas
                        c.execute('''
                            INSERT INTO facturas (
                                id, empresa_id, tipo, numero_factura, serie, fecha,
                                tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso,
                                subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr,
                                aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar,
                                created_by, created_at, estado_pago, saldo_pendiente, forma_pago, cuenta_pago
                            ) VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PAGADO', 0.0, 'Contado', %s)
                        ''', (
                            str(fac_id), str(selected_emp_id), str(num_fac).strip(), str(serie).strip(), str(fecha_fac),
                            str(cli_nombre).strip(), str(cli_nit).strip(), "Venta de Servicios / Transporte", "Ingreso",
                            float(subtotal), float(iva), float(total),
                            1 if aplica_isr else 0, float(t_isr), float(m_isr),
                            1 if aplica_iva else 0, float(t_iva), float(m_iva),
                            float(liq), str(curr_u['username']), str(now_str), str(cuenta_debe_cobro)
                        ))

                        # 2. Correlativo de partida en Libro Diario
                        c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                        p_no = int(c.fetchone()[0])
                        glosa = f"Factura Venta {num_fac} a {cli_nombre}"

                        cod_d, nom_d = cuenta_debe_cobro.split(" - ", 1) if " - " in cuenta_debe_cobro else (cuenta_debe_cobro, cuenta_debe_cobro)
                        cod_h, nom_h = cuenta_haber_ingreso.split(" - ", 1) if " - " in cuenta_haber_ingreso else (cuenta_haber_ingreso, cuenta_haber_ingreso)

                        # Consulta universal con columna referencia_id (Supabase)
                        q_ld = """
                            INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """

                        # Renglón 1: Cobro en Caja / Bancos (Debe)
                        c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_d), str(nom_d), float(liq), 0.0, glosa, str(num_fac), str(now_str)))

                        # Renglón 2: Retención ISR si aplica (Debe)
                        if float(m_isr) > 0:
                            c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.02', 'ISR Retenido por Acreditar', float(m_isr), 0.0, glosa, str(num_fac), str(now_str)))

                        # Renglón 3: Retención IVA si aplica (Debe)
                        if float(m_iva) > 0:
                            c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.03', 'Constancias Retención IVA', float(m_iva), 0.0, glosa, str(num_fac), str(now_str)))

                        # Renglón 4: Ingreso por Transporte / Ventas (Haber)
                        c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_h), str(nom_h), 0.0, float(subtotal), glosa, str(num_fac), str(now_str)))

                        # Renglón 5: IVA Débito Fiscal si aplica (Haber)
                        if float(iva) > 0:
                            c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_fac), '2.1.02.01', 'IVA por Pagar (Débito Fiscal)', 0.0, float(iva), glosa, str(num_fac), str(now_str)))

                        conn.commit()
                        conn.close()
                        st.success("✅ ¡Factura guardada y asentada exitosamente en todos los módulos!")
                        st.rerun()

                    except Exception as err:
                        st.error(f"❌ Error al guardar en base de datos: {err}")

# ==========================================
# 3. FACTURA DE COMPRAS
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

            idx_d_sug = 0
            if destino_bien == "Mercadería (Inventario Kárdex)":
                idx_d_sug = next((i for i, val in enumerate(opc_debe) if "5.1.01.01" in val or "1.1.04.01" in val), 0)
            elif destino_bien == "Activo Fijo / No Corriente":
                idx_d_sug = next((i for i, val in enumerate(opc_debe) if "1.2." in val or "Activo" in val), 0)

            cd1, cd2 = st.columns(2)
            cta_debe = cd1.selectbox("Cuenta de Cargo (DEBE) *", opc_debe if opc_debe else ["5.1.01.01 - Compras"], index=idx_d_sug, key=f"c_cdebe_{fk_c}")
            cta_haber = cd2.selectbox("Cuenta de Abono (HABER) *", opc_haber if opc_haber else ["2.1.01.01 - Proveedores"], index=idx_h, key=f"c_chaber_{fk_c}")

            subtotal_compra = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, value=float(pre_c.get("subtotal", 0.0)), step=100.0, format="%.2f", key=f"c_sub_{fk_c}")

            if tipo_doc_compra == "Factura Electrónica FEL (SAT)":
                es_peq_prov = st.checkbox("¿Factura de Pequeño Contribuyente? (No genera crédito fiscal IVA)", value=pre_c.get("es_pequeno", False), key=f"c_peq_{fk_c}")
                iva_compra = 0.0 if es_peq_prov else round(float(subtotal_compra) * 0.12, 2)
                total_compra = round(float(subtotal_compra) + iva_compra, 2)
            else:
                es_peq_prov = False
                iva_compra = 0.0
                total_compra = float(subtotal_compra)

            r1, r2 = st.columns(2)
            aplica_isr_c = r1.checkbox("Practicar Retención ISR", value=(subtotal_compra >= 2800.0 and tipo_doc_compra == "Factura Electrónica FEL (SAT)"), key=f"c_rk_{fk_c}")
            tipo_isr_c = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Honorarios", "No Aplica"], index=0 if subtotal_compra >= 2800.0 else 3, key=f"c_rt_{fk_c}")
            aplica_iva_c = r2.checkbox("Practicar Retención IVA", value=False, key=f"c_rvk_{fk_c}")
            tipo_iva_c = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key=f"c_rvt_{fk_c}")

            m_isr_c = 0.0
            if aplica_isr_c and subtotal_compra >= 2800.0 and tipo_isr_c != "No Aplica":
                m_isr_c = round(float(subtotal_compra) * 0.05, 2) if tipo_isr_c == "5% General" else round(1500.0 + (float(subtotal_compra) - 30000.0) * 0.07, 2)

            m_iva_c = 0.0
            if aplica_iva_c and tipo_iva_c != "No Aplica / No es Agente Retenedor":
                m_iva_c = round(iva_compra * 0.15, 2) if "15%" in tipo_iva_c else round(iva_compra, 2)

            liq_c = round(float(total_compra) - m_isr_c - m_iva_c, 2)

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
                    saldo = float(liq_c) if cond_pago == "Crédito" else 0.0
                    estado = "PENDIENTE" if cond_pago == "Crédito" else "PAGADO"
                    fp_desc = cond_pago if cond_pago == "Crédito" else f"Contado ({metodo_p})"

                    c.execute("""
                        INSERT INTO facturas VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s,
                                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom, prov_nit,
                          cta_debe, cta_haber, fp_desc, float(subtotal_compra), float(iva_compra), float(total_compra),
                          1 if m_isr_c > 0 else 0, 0.05 if m_isr_c > 0 else 0.0, float(m_isr_c),
                          1 if m_iva_c > 0 else 0, 0.15 if m_iva_c > 0 else 0.0, float(m_iva_c),
                          float(liq_c), saldo, estado, curr_u['username'], datetime.now().isoformat()))

                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = int(c.fetchone()[0] or 1)
                    now_str = datetime.now().isoformat()
                    glosa = f"{'Factura Compra' if tipo_doc_compra == 'Factura Electrónica FEL (SAT)' else 'Comprobante No Fiscal'} {num_fac} de {prov_nom}"

                    cod_d, nom_d = cta_debe.split(" - ", 1)
                    cod_h, nom_h = cta_haber.split(" - ", 1)

                    q_ins_ld = """
                        INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, float(subtotal_compra), 0.0, glosa, fac_id, now_str))
                    if iva_compra > 0:
                        c.execute(q_ins_ld, (selected_emp_id, p_no, str(fecha_fac), '1.1.03.01', 'IVA por Cobrar', float(iva_compra), 0.0, glosa, fac_id, now_str))
                    if m_isr_c > 0:
                        c.execute(q_ins_ld, (selected_emp_id, p_no, str(fecha_fac), '2.1.03.01', 'Retenciones ISR por Pagar', 0.0, float(m_isr_c), glosa, fac_id, now_str))
                    if m_iva_c > 0:
                        c.execute(q_ins_ld, (selected_emp_id, p_no, str(fecha_fac), '2.1.03.02', 'Retenciones IVA por Pagar', 0.0, float(m_iva_c), glosa, fac_id, now_str))
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, 0.0, float(liq_c), glosa, fac_id, now_str))

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
                    c.execute("UPDATE libro_diario SET fecha = %s, concepto = %s WHERE referencia_id = %s", (str(fc_fec_corregida), f"Factura Compra {fc_num_corregido}", fac_c_id_sel))
                    conn.commit()
                    conn.close()
                    st.success("Factura de compra y asiento contable actualizados.")
                    st.rerun()

            if is_admin:
                st.divider()
                if st.button("🗑️ Eliminar Factura de Compra Definitivamente", type="secondary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s", (fac_c_id_sel,))
                    c.execute("DELETE FROM facturas WHERE id = %s", (fac_c_id_sel,))
                    conn.commit()
                    conn.close()
                    st.warning("Factura de compra eliminada del sistema.")
                    st.rerun()

# ==========================================
# 4. KÁRDEX E INVENTARIOS CORPORATIVOS
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
                        """, (p_id, p_cod.strip(), p_nom.strip(), float(p_pre), datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Artículo '{p_nom}' guardado a nivel corporativo.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al guardar producto: {err}")
                    finally:
                        conn.close()

    conn = get_db_connection()
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
# 5. ESTADO DE RESULTADOS
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title(f"📉 Estado de Resultados — {selected_empresa_info[1]}")
    st.caption(f"Régimen SAT: **{selected_empresa_info[3]}**")

    col_v1, col_v2, col_v3 = st.columns([2, 1, 1])
    modo_er = col_v1.radio("Visualización:", ["🌐 Histórico Acumulado (Mes a Mes)", "📅 Mes Específico", "📆 Año Completo"], horizontal=True)
    anio_sel = col_v2.selectbox("Año Fiscal:", [2026, 2025, 2024], index=0)
    color_filtro = col_v3.color_picker("Color Distintivo:", "#1E3A8A")

    meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    conn = get_db_connection()
    q_data_er = """
        SELECT n.codigo, n.nombre, n.tipo,
               EXTRACT(MONTH FROM d.fecha::date)::int AS mes,
               COALESCE(SUM(d.debe), 0) AS debe,
               COALESCE(SUM(d.haber), 0) AS haber
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND EXTRACT(YEAR FROM d.fecha::date) = %s AND (n.tipo IN ('Ingreso', 'Gasto', 'Costo'))
        GROUP BY n.codigo, n.nombre, n.tipo, EXTRACT(MONTH FROM d.fecha::date)
        ORDER BY n.codigo
    """
    df_raw_er = pd.read_sql_query(q_data_er, conn, params=(selected_emp_id, anio_sel))
    conn.close()

    reg_emp = selected_empresa_info[3]

    if modo_er == "🌐 Histórico Acumulado (Mes a Mes)":
        st.markdown(f"<div style='border-left: 5px solid {color_filtro}; padding-left: 10px; font-weight: bold;'>Desglose Mensual Oficial Enero a Diciembre ({anio_sel})</div>", unsafe_allow_html=True)
        
        cuentas_unicas = df_raw_er[['codigo', 'nombre', 'tipo']].drop_duplicates()
        
        filas_matriz = []
        totales_ing_m = {m: 0.0 for m in range(1, 13)}
        totales_cos_m = {m: 0.0 for m in range(1, 13)}
        totales_gas_m = {m: 0.0 for m in range(1, 13)}

        filas_matriz.append({"Concepto Contable": "1. (+) INGRESOS OPERACIONALES", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_unicas[cuentas_unicas['tipo'] == 'Ingreso'].iterrows():
            fila_c = {"Concepto Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_er[(df_raw_er['codigo'] == r['codigo']) & (df_raw_er['mes'] == m)]
                val = float(sub['haber'].sum() - sub['debe'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                totales_ing_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_matriz.append(fila_c)
        filas_matriz.append({"Concepto Contable": "  (=) SUBTOTAL INGRESOS", **{meses_nombres[m-1]: round(totales_ing_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(totales_ing_m.values()), 2)})

        filas_matriz.append({"Concepto Contable": "2. (-) COSTO DE VENTAS", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_unicas[cuentas_unicas['tipo'] == 'Costo'].iterrows():
            fila_c = {"Concepto Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_er[(df_raw_er['codigo'] == r['codigo']) & (df_raw_er['mes'] == m)]
                val = float(sub['debe'].sum() - sub['haber'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                totales_cos_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_matriz.append(fila_c)
        filas_matriz.append({"Concepto Contable": "  (=) SUBTOTAL COSTO DE VENTAS", **{meses_nombres[m-1]: round(totales_cos_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(totales_cos_m.values()), 2)})

        util_bruta_m = {m: totales_ing_m[m] - totales_cos_m[m] for m in range(1, 13)}
        filas_matriz.append({"Concepto Contable": "3. (=) UTILIDAD BRUTA EN VENTAS", **{meses_nombres[m-1]: round(util_bruta_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(util_bruta_m.values()), 2)})

        filas_matriz.append({"Concepto Contable": "4. (-) GASTOS DE OPERACIÓN Y ADMINISTRACIÓN", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_unicas[cuentas_unicas['tipo'] == 'Gasto'].iterrows():
            fila_c = {"Concepto Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_er[(df_raw_er['codigo'] == r['codigo']) & (df_raw_er['mes'] == m)]
                val = float(sub['debe'].sum() - sub['haber'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                totales_gas_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_matriz.append(fila_c)
        filas_matriz.append({"Concepto Contable": "  (=) SUBTOTAL GASTOS OPERATIVOS", **{meses_nombres[m-1]: round(totales_gas_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(totales_gas_m.values()), 2)})

        util_op_m = {m: util_bruta_m[m] - totales_gas_m[m] for m in range(1, 13)}
        filas_matriz.append({"Concepto Contable": "5. (=) UTILIDAD DE OPERACIÓN", **{meses_nombres[m-1]: round(util_op_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(util_op_m.values()), 2)})

        isr_m = {}
        ganancia_m = {}
        for m in range(1, 13):
            imp, net, _ = calcular_impuesto_renta(reg_emp, totales_ing_m[m], util_op_m[m])
            isr_m[m] = imp
            ganancia_m[m] = net

        tot_anual_ing = sum(totales_ing_m.values())
        tot_anual_op = sum(util_op_m.values())
        tot_anual_isr, tot_anual_net, tasa_txt = calcular_impuesto_renta(reg_emp, tot_anual_ing, tot_anual_op)

        filas_matriz.append({"Concepto Contable": f"6. (-) PROVISIÓN ISR ({tasa_txt})", **{meses_nombres[m-1]: round(isr_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(tot_anual_isr, 2)})
        filas_matriz.append({"Concepto Contable": "7. (=) UTILIDAD NETA DEL EJERCICIO", **{meses_nombres[m-1]: round(ganancia_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(tot_anual_net, 2)})

        df_er_display = pd.DataFrame(filas_matriz)
        st.dataframe(df_er_display, use_container_width=True, hide_index=True)

        col_d1, col_d2 = st.columns(2)
        excel_er = exportar_reporte_excel(df_er_display, "Estado_Resultados_Meses")
        col_d1.download_button("📥 Descargar Excel Mes a Mes", excel_er, f"ER_Mensual_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_er = exportar_libro_pdf(df_er_display, "Estado de Resultados Mensual", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar PDF Oficial Mes a Mes", pdf_er, f"ER_Mensual_{selected_empresa_info[1]}.pdf", "application/pdf")

    else:
        if modo_er == "📅 Mes Específico":
            mes_sel_er = st.selectbox("Mes:", range(1, 13), format_func=lambda x: f"{x:02d} - {meses_nombres[x-1]}")
            df_er_periodo = df_raw_er[df_raw_er["mes"] == mes_sel_er]
            lbl_periodo = f"Mes de {meses_nombres[mes_sel_er-1]} {anio_sel}"
        else:
            df_er_periodo = df_raw_er
            lbl_periodo = f"Año Fiscal {anio_sel}"

        tot_ing_p = float(df_er_periodo[df_er_periodo['tipo']=='Ingreso']['haber'].sum() - df_er_periodo[df_er_periodo['tipo']=='Ingreso']['debe'].sum())
        tot_cos_p = float(df_er_periodo[df_er_periodo['tipo']=='Costo']['debe'].sum() - df_er_periodo[df_er_periodo['tipo']=='Costo']['haber'].sum())
        util_bruta_p = tot_ing_p - tot_cos_p
        tot_gas_p = float(df_er_periodo[df_er_periodo['tipo']=='Gasto']['debe'].sum() - df_er_periodo[df_er_periodo['tipo']=='Gasto']['haber'].sum())
        util_op_p = util_bruta_p - tot_gas_p
        imp_p, util_neta_p, desc_isr_p = calcular_impuesto_renta(reg_emp, tot_ing_p, util_op_p)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("(+) Ingresos Operacionales", f"Q {tot_ing_p:,.2f}")
        c2.metric("(=) Utilidad Bruta", f"Q {util_bruta_p:,.2f}")
        c3.metric(f"(-) Provisión ISR ({desc_isr_p})", f"Q {imp_p:,.2f}")
        c4.metric("(=) Ganancia Neta Ejercicio", f"Q {util_neta_p:,.2f}")

        filas_p = [
            {"Clasificación": "1. INGRESOS OPERACIONALES", "Detalle": "Ventas y Servicios", "Monto (Q)": tot_ing_p},
            {"Clasificación": "2. COSTO DE VENTAS", "Detalle": "Costo de Adquisición de Mercaderías", "Monto (Q)": -tot_cos_p},
            {"Clasificación": "3. MARGEN BRUTO", "Detalle": "(=) UTILIDAD BRUTA EN VENTAS", "Monto (Q)": util_bruta_p},
            {"Clasificación": "4. GASTOS DE OPERACIÓN", "Detalle": "Gastos Administrativos y Operativos", "Monto (Q)": -tot_gas_p},
            {"Clasificación": "5. RESULTADO OPERATIVO", "Detalle": "(=) UTILIDAD DE OPERACIÓN", "Monto (Q)": util_op_p},
            {"Clasificación": "6. IMPUESTOS", "Detalle": f"(-) Impuesto Sobre la Renta ({desc_isr_p})", "Monto (Q)": -imp_p},
            {"Clasificación": "7. RESULTADO FINAL", "Detalle": "(=) UTILIDAD NETA DEL EJERCICIO", "Monto (Q)": util_neta_p}
        ]
        df_p_view = pd.DataFrame(filas_p)
        st.table(df_p_view.style.format({"Monto (Q)": "Q {:,.2f}"}))

        col_d1, col_d2 = st.columns(2)
        excel_p = exportar_reporte_excel(df_p_view, "Estado_Resultados")
        col_d1.download_button("📥 Descargar Excel", excel_p, f"ER_{lbl_periodo}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_p = exportar_libro_pdf(df_p_view, f"Estado de Resultados ({lbl_periodo})", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar PDF", pdf_p, f"ER_{lbl_periodo}.pdf", "application/pdf")

# ==========================================
# 6. BALANCE DE SITUACIÓN FINANCIERA
# ==========================================
elif selected_menu == "🏛️ Balance Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title(f"🏛️ Balance de Situación Financiera Clasificado — {selected_empresa_info[1]}")
    st.caption(f"Estructura Patrimonial: **Activo = Pasivo + Patrimonio Neto (Con Ganancia del Ejercicio)**")

    col_b1, col_b2, col_b3 = st.columns([2, 1, 1])
    modo_bsf = col_b1.radio("Visualización:", ["🌐 Histórico Acumulado (Mes a Mes)", "📅 Mes Específico", "📆 Año Completo"], horizontal=True)
    anio_bsf = col_b2.selectbox("Año Fiscal:", [2026, 2025, 2024], index=0, key="anio_bsf")
    color_bsf = col_b3.color_picker("Color Distintivo:", "#1E3A8A", key="col_bsf")

    meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    conn = get_db_connection()
    q_bsf = """
        SELECT n.codigo, n.nombre, n.tipo,
               EXTRACT(MONTH FROM d.fecha::date)::int AS mes,
               COALESCE(SUM(d.debe), 0) AS debe,
               COALESCE(SUM(d.haber), 0) AS haber
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s AND EXTRACT(YEAR FROM d.fecha::date) = %s
        GROUP BY n.codigo, n.nombre, n.tipo, EXTRACT(MONTH FROM d.fecha::date)
        ORDER BY n.codigo
    """
    df_raw_bsf = pd.read_sql_query(q_bsf, conn, params=(selected_emp_id, anio_bsf))
    conn.close()

    reg_emp = selected_empresa_info[3]

    if modo_bsf == "🌐 Histórico Acumulado (Mes a Mes)":
        st.markdown(f"<div style='border-left: 5px solid {color_bsf}; padding-left: 10px; font-weight: bold;'>Balance de Situación Financiera Matricial Enero a Diciembre ({anio_bsf})</div>", unsafe_allow_html=True)

        cuentas_balance = df_raw_bsf[['codigo', 'nombre', 'tipo']].drop_duplicates()

        ganancia_neta_m = {}
        for m in range(1, 13):
            sub_m = df_raw_bsf[df_raw_bsf['mes'] == m]
            ing_m = float(sub_m[sub_m['tipo'] == 'Ingreso']['haber'].sum() - sub_m[sub_m['tipo'] == 'Ingreso']['debe'].sum())
            cos_m = float(sub_m[sub_m['tipo'] == 'Costo']['debe'].sum() - sub_m[sub_m['tipo'] == 'Costo']['haber'].sum())
            gas_m = float(sub_m[sub_m['tipo'] == 'Gasto']['debe'].sum() - sub_m[sub_m['tipo'] == 'Gasto']['haber'].sum())
            util_op = (ing_m - cos_m) - gas_m
            _, net, _ = calcular_impuesto_renta(reg_emp, ing_m, util_op)
            ganancia_neta_m[m] = net

        tot_anual_ing = float(df_raw_bsf[df_raw_bsf['tipo'] == 'Ingreso']['haber'].sum() - df_raw_bsf[df_raw_bsf['tipo'] == 'Ingreso']['debe'].sum())
        tot_anual_cos = float(df_raw_bsf[df_raw_bsf['tipo'] == 'Costo']['debe'].sum() - df_raw_bsf[df_raw_bsf['tipo'] == 'Costo']['haber'].sum())
        tot_anual_gas = float(df_raw_bsf[df_raw_bsf['tipo'] == 'Gasto']['debe'].sum() - df_raw_bsf[df_raw_bsf['tipo'] == 'Gasto']['haber'].sum())
        tot_anual_util_op = (tot_anual_ing - tot_anual_cos) - tot_anual_gas
        _, tot_anual_net, _ = calcular_impuesto_renta(reg_emp, tot_anual_ing, tot_anual_util_op)

        filas_bsf_mat = []
        tot_act_corr_m = {m: 0.0 for m in range(1, 13)}
        tot_act_nocorr_m = {m: 0.0 for m in range(1, 13)}
        tot_pas_corr_m = {m: 0.0 for m in range(1, 13)}
        tot_pas_nocorr_m = {m: 0.0 for m in range(1, 13)}
        tot_cap_m = {m: 0.0 for m in range(1, 13)}

        filas_bsf_mat.append({"Rubro / Cuenta Contable": "1. ACTIVO CORRIENTE", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_balance[(cuentas_balance['tipo'] == 'Activo') & (cuentas_balance['codigo'].str.startswith('1.1.'))].iterrows():
            fila_c = {"Rubro / Cuenta Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_bsf[(df_raw_bsf['codigo'] == r['codigo']) & (df_raw_bsf['mes'] == m)]
                val = float(sub['debe'].sum() - sub['haber'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                tot_act_corr_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_bsf_mat.append(fila_c)
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "  (=) SUBTOTAL ACTIVO CORRIENTE", **{meses_nombres[m-1]: round(tot_act_corr_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_act_corr_m.values()), 2)})

        filas_bsf_mat.append({"Rubro / Cuenta Contable": "2. ACTIVO NO CORRIENTE", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_balance[(cuentas_balance['tipo'] == 'Activo') & (~cuentas_balance['codigo'].str.startswith('1.1.'))].iterrows():
            fila_c = {"Rubro / Cuenta Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_bsf[(df_raw_bsf['codigo'] == r['codigo']) & (df_raw_bsf['mes'] == m)]
                val = float(sub['debe'].sum() - sub['haber'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                tot_act_nocorr_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_bsf_mat.append(fila_c)
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "  (=) SUBTOTAL ACTIVO NO CORRIENTE", **{meses_nombres[m-1]: round(tot_act_nocorr_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_act_nocorr_m.values()), 2)})

        tot_activo_m = {m: tot_act_corr_m[m] + tot_act_nocorr_m[m] for m in range(1, 13)}
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "TOTAL ACTIVO", **{meses_nombres[m-1]: round(tot_activo_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_activo_m.values()), 2)})

        filas_bsf_mat.append({"Rubro / Cuenta Contable": "3. PASIVO CORRIENTE", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_balance[(cuentas_balance['tipo'] == 'Pasivo') & (cuentas_balance['codigo'].str.startswith('2.1.'))].iterrows():
            fila_c = {"Rubro / Cuenta Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_bsf[(df_raw_bsf['codigo'] == r['codigo']) & (df_raw_bsf['mes'] == m)]
                val = float(sub['haber'].sum() - sub['debe'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                tot_pas_corr_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_bsf_mat.append(fila_c)
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "  (=) SUBTOTAL PASIVO CORRIENTE", **{meses_nombres[m-1]: round(tot_pas_corr_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_pas_corr_m.values()), 2)})

        filas_bsf_mat.append({"Rubro / Cuenta Contable": "4. PASIVO NO CORRIENTE", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_balance[(cuentas_balance['tipo'] == 'Pasivo') & (~cuentas_balance['codigo'].str.startswith('2.1.'))].iterrows():
            fila_c = {"Rubro / Cuenta Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_bsf[(df_raw_bsf['codigo'] == r['codigo']) & (df_raw_bsf['mes'] == m)]
                val = float(sub['haber'].sum() - sub['debe'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                tot_pas_nocorr_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_bsf_mat.append(fila_c)
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "  (=) SUBTOTAL PASIVO NO CORRIENTE", **{meses_nombres[m-1]: round(tot_pas_nocorr_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_pas_nocorr_m.values()), 2)})

        tot_pasivo_m = {m: tot_pas_corr_m[m] + tot_pas_nocorr_m[m] for m in range(1, 13)}
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "TOTAL PASIVO", **{meses_nombres[m-1]: round(tot_pasivo_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_pasivo_m.values()), 2)})

        filas_bsf_mat.append({"Rubro / Cuenta Contable": "5. PATRIMONIO NETO", **{meses_nombres[m-1]: "" for m in range(1, 13)}, "TOTAL ACUMULADO": ""})
        for _, r in cuentas_balance[cuentas_balance['tipo'] == 'Capital'].iterrows():
            fila_c = {"Rubro / Cuenta Contable": f"  {r['codigo']} - {r['nombre']}"}
            acum_cta = 0.0
            for m in range(1, 13):
                sub = df_raw_bsf[(df_raw_bsf['codigo'] == r['codigo']) & (df_raw_bsf['mes'] == m)]
                val = float(sub['haber'].sum() - sub['debe'].sum()) if not sub.empty else 0.0
                fila_c[meses_nombres[m-1]] = round(val, 2)
                acum_cta += val
                tot_cap_m[m] += val
            fila_c["TOTAL ACUMULADO"] = round(acum_cta, 2)
            filas_bsf_mat.append(fila_c)

        filas_bsf_mat.append({
            "Rubro / Cuenta Contable": "  (+) GANANCIA / (PÉRDIDA) NETA DEL EJERCICIO",
            **{meses_nombres[m-1]: round(ganancia_neta_m[m], 2) for m in range(1, 13)},
            "TOTAL ACUMULADO": round(tot_anual_net, 2)
        })

        tot_patrimonio_m = {m: tot_cap_m[m] + ganancia_neta_m[m] for m in range(1, 13)}
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "TOTAL PATRIMONIO NETO", **{meses_nombres[m-1]: round(tot_patrimonio_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_patrimonio_m.values()), 2)})

        tot_pas_pat_m = {m: tot_pasivo_m[m] + tot_patrimonio_m[m] for m in range(1, 13)}
        filas_bsf_mat.append({"Rubro / Cuenta Contable": "TOTAL PASIVO + PATRIMONIO NETO", **{meses_nombres[m-1]: round(tot_pas_pat_m[m], 2) for m in range(1, 13)}, "TOTAL ACUMULADO": round(sum(tot_pas_pat_m.values()), 2)})

        df_bsf_display = pd.DataFrame(filas_bsf_mat)
        st.dataframe(df_bsf_display, use_container_width=True, hide_index=True)

        col_d1, col_d2 = st.columns(2)
        excel_bsf = exportar_reporte_excel(df_bsf_display, "BSF_Mensual")
        col_d1.download_button("📥 Descargar Balance Situación Excel Mes a Mes", excel_bsf, f"BSF_Mensual_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_bsf = exportar_libro_pdf(df_bsf_display, "Balance de Situación Financiera Mensual", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar Balance Situación PDF Mes a Mes", pdf_bsf, f"BSF_Mensual_{selected_empresa_info[1]}.pdf", "application/pdf")

    else:
        if modo_bsf == "📅 Mes Específico":
            mes_sel_b = st.selectbox("Mes:", range(1, 13), format_func=lambda x: f"{x:02d} - {meses_nombres[x-1]}", key="mes_b_sel")
            df_bsf_periodo = df_raw_bsf[df_raw_bsf["mes"] == mes_sel_b]
            lbl_bsf = f"Mes de {meses_nombres[mes_sel_b-1]} {anio_bsf}"
        else:
            df_bsf_periodo = df_raw_bsf
            lbl_bsf = f"Año Fiscal {anio_bsf}"

        tot_ing_b = float(df_bsf_periodo[df_bsf_periodo['tipo']=='Ingreso']['haber'].sum() - df_bsf_periodo[df_bsf_periodo['tipo']=='Ingreso']['debe'].sum())
        tot_cos_b = float(df_bsf_periodo[df_bsf_periodo['tipo']=='Costo']['debe'].sum() - df_bsf_periodo[df_bsf_periodo['tipo']=='Costo']['haber'].sum())
        tot_gas_b = float(df_bsf_periodo[df_bsf_periodo['tipo']=='Gasto']['debe'].sum() - df_bsf_periodo[df_bsf_periodo['tipo']=='Gasto']['haber'].sum())
        util_op_b = (tot_ing_b - tot_cos_b) - tot_gas_b
        _, ganancia_ejercicio_b, _ = calcular_impuesto_renta(reg_emp, tot_ing_b, util_op_b)

        act_corr = float(df_bsf_periodo[(df_bsf_periodo['tipo']=='Activo') & (df_bsf_periodo['codigo'].str.startswith('1.1.'))]['debe'].sum() - df_bsf_periodo[(df_bsf_periodo['tipo']=='Activo') & (df_bsf_periodo['codigo'].str.startswith('1.1.'))]['haber'].sum())
        act_nocorr = float(df_bsf_periodo[(df_bsf_periodo['tipo']=='Activo') & (~df_bsf_periodo['codigo'].str.startswith('1.1.'))]['debe'].sum() - df_bsf_periodo[(df_bsf_periodo['tipo']=='Activo') & (~df_bsf_periodo['codigo'].str.startswith('1.1.'))]['haber'].sum())
        tot_activo_b = act_corr + act_nocorr

        pas_corr = float(df_bsf_periodo[(df_bsf_periodo['tipo']=='Pasivo') & (df_bsf_periodo['codigo'].str.startswith('2.1.'))]['haber'].sum() - df_bsf_periodo[(df_bsf_periodo['tipo']=='Pasivo') & (df_bsf_periodo['codigo'].str.startswith('2.1.'))]['debe'].sum())
        pas_nocorr = float(df_bsf_periodo[(df_bsf_periodo['tipo']=='Pasivo') & (~df_bsf_periodo['codigo'].str.startswith('2.1.'))]['haber'].sum() - df_bsf_periodo[(df_bsf_periodo['tipo']=='Pasivo') & (~df_bsf_periodo['codigo'].str.startswith('2.1.'))]['debe'].sum())
        tot_pasivo_b = pas_corr + pas_nocorr

        capital_b = float(df_bsf_periodo[df_bsf_periodo['tipo']=='Capital']['haber'].sum() - df_bsf_periodo[df_bsf_periodo['tipo']=='Capital']['debe'].sum())
        tot_patrimonio_b = capital_b + ganancia_ejercicio_b
        tot_pas_pat_b = tot_pasivo_b + tot_patrimonio_b

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Activo", f"Q {tot_activo_b:,.2f}")
        c2.metric("Total Pasivo", f"Q {tot_pasivo_b:,.2f}")
        c3.metric("Ganancia del Ejercicio", f"Q {ganancia_ejercicio_b:,.2f}")
        c4.metric("Pasivo + Patrimonio", f"Q {tot_pas_pat_b:,.2f}")

        filas_b_view = [
            {"Sección": "1. ACTIVO", "Cuenta / Rubro": "Subtotal Activo Corriente (Caja, Bancos, Clientes, Inventarios)", "Monto (Q)": act_corr},
            {"Sección": "1. ACTIVO", "Cuenta / Rubro": "Subtotal Activo No Corriente (Propiedad, Planta y Equipo)", "Monto (Q)": act_nocorr},
            {"Sección": "1. ACTIVO", "Cuenta / Rubro": "TOTAL ACTIVO", "Monto (Q)": tot_activo_b},
            {"Sección": "2. PASIVO", "Cuenta / Rubro": "Subtotal Pasivo Corriente (Proveedores, Impuestos y Retenciones)", "Monto (Q)": pas_corr},
            {"Sección": "2. PASIVO", "Cuenta / Rubro": "Subtotal Pasivo No Corriente (Deudas a Largo Plazo)", "Monto (Q)": pas_nocorr},
            {"Sección": "2. PASIVO", "Cuenta / Rubro": "TOTAL PASIVO", "Monto (Q)": tot_pasivo_b},
            {"Sección": "3. PATRIMONIO", "Cuenta / Rubro": "Capital Social / Individual", "Monto (Q)": capital_b},
            {"Sección": "3. PATRIMONIO", "Cuenta / Rubro": "(+) Ganancia Neta del Ejercicio (del Estado de Resultados)", "Monto (Q)": ganancia_ejercicio_b},
            {"Sección": "3. PATRIMONIO", "Cuenta / Rubro": "TOTAL PATRIMONIO NETO", "Monto (Q)": tot_patrimonio_b},
            {"Sección": "TOTAL", "Cuenta / Rubro": "TOTAL PASIVO + PATRIMONIO NETO", "Monto (Q)": tot_pas_pat_b}
        ]
        df_b_view_tbl = pd.DataFrame(filas_b_view)
        st.table(df_b_view_tbl.style.format({"Monto (Q)": "Q {:,.2f}"}))

        col_d1, col_d2 = st.columns(2)
        excel_b = exportar_reporte_excel(df_b_view_tbl, "Balance_Situacion")
        col_d1.download_button("📥 Descargar Excel", excel_b, f"BSF_{lbl_bsf}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_b = exportar_libro_pdf(df_b_view_tbl, f"Balance de Situación Financiera ({lbl_bsf})", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar PDF", pdf_b, f"BSF_{lbl_bsf}.pdf", "application/pdf")

# ==========================================
# 7. BALANCE GENERAL DE SALDOS
# ==========================================
elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    conn = get_db_connection()
    query_bs = """
        SELECT n.codigo, n.nombre, n.tipo, COALESCE(SUM(d.debe), 0) AS total_debe, COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s GROUP BY n.codigo, n.nombre, n.tipo HAVING (COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0) ORDER BY n.codigo
    """
    df_bs = pd.read_sql_query(query_bs, conn, params=(selected_emp_id,))
    conn.close()
    if not df_bs.empty:
        df_bs['Saldo Deudor'] = df_bs.apply(lambda r: round(float(r['total_debe']) - float(r['total_haber']), 2) if float(r['total_debe']) > float(r['total_haber']) else 0.0, axis=1)
        df_bs['Saldo Acreedor'] = df_bs.apply(lambda r: round(float(r['total_haber']) - float(r['total_debe']), 2) if float(r['total_haber']) > float(r['total_debe']) else 0.0, axis=1)
        df_bs_view = df_bs[['codigo', 'nombre', 'tipo', 'Saldo Deudor', 'Saldo Acreedor']].rename(columns={'codigo': 'Código', 'nombre': 'Cuenta', 'tipo': 'Tipo'})
        st.dataframe(df_bs_view, use_container_width=True)

        col_d1, col_d2 = st.columns(2)
        excel_bs = exportar_reporte_excel(df_bs_view, "Balance_Saldos")
        col_d1.download_button("📥 Descargar Balance de Saldos Excel", excel_bs, f"Balance_Saldos_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf_bs = exportar_libro_pdf(df_bs_view, "Balance General de Saldos", selected_empresa_info[1], selected_empresa_info[2])
        col_d2.download_button("📄 Descargar Balance de Saldos PDF", pdf_bs, f"Balance_Saldos_{selected_empresa_info[1]}.pdf", "application/pdf")
    else:
        st.info("Sin movimientos para generar el Balance General de Saldos.")

# ==========================================
# 8. LIBROS TRIBUTARIOS SAT
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
# 9. LIBRO DIARIO, MAYOR Y RETENCIONES
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

# ==========================================
# 10. CXC, CXP Y DIRECTORIO
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
                    nuevo_saldo = round(float(row_f['saldo_pendiente']) - float(monto_abono), 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = int(c.fetchone()[0] or 1)
                    now_str = datetime.now().isoformat()

                    q_ins_ld = """
                        INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(date.today()), '2.1.01.01', 'Proveedores / Cuentas por Pagar', float(monto_abono), 0.0, f"Abono Fac {row_f['numero_factura']}", fac_sel, now_str))
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(date.today()), cta_origen, 'Salida de Fondos', 0.0, float(monto_abono), f"Pago Fac {row_f['numero_factura']}", fac_sel, now_str))
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
                    nuevo_saldo = round(float(row_v['saldo_pendiente']) - float(monto_cobro), 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = int(c.fetchone()[0] or 1)
                    now_str = datetime.now().isoformat()

                    q_ins_ld = """
                        INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(date.today()), cta_destino, 'Ingreso de Fondos', float(monto_cobro), 0.0, f"Cobro Fac {row_v['numero_factura']}", fac_sel, now_str))
                    c.execute(q_ins_ld, (selected_emp_id, p_no, str(date.today()), '1.1.02.01', 'Clientes / Cuentas por Cobrar', 0.0, float(monto_cobro), f"Cancelación Fac {row_v['numero_factura']}", fac_sel, now_str))
                    conn.commit()
                    conn.close()
                    st.success("Cobro registrado y saldos actualizados.")
                    st.rerun()
    conn.close()

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
                        """, (selected_emp_id, t_tipo, t_nit.strip().upper(), t_nom.strip(), t_dir, t_tel, int(t_dias)))
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

# ==========================================
# 11. DESPENSA FAMILIAR Y COCINA
# ==========================================
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
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual + %s WHERE id = %s", (float(cant), opc_d[p_sel]))
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
                        c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual - %s WHERE id = %s", (float(cant_r), pid))
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

# ==========================================
# 12. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")

    with st.expander("➕ Crear Nueva Cuenta Contable", expanded=True):
        with st.form("form_cta_nom", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod_c = c1.text_input("Código de Cuenta * (ej: 5.1.02.15)")
            nom_c = c2.text_input("Nombre de Cuenta * (ej: Papelería y Útiles)")
            tip_c = c3.selectbox("Tipo de Cuenta *", ["Activo", "Pasivo", "Capital", "Ingreso", "Costo", "Gasto"])
            
            if st.form_submit_button("✅ Guardar Cuenta Contable", type="primary"):
                if cod_c.strip() and nom_c.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c_id = f"nom-{selected_emp_id}-{cod_c.strip()}"
                        c.execute("DELETE FROM nomenclatura WHERE empresa_id = %s AND codigo = %s", (selected_emp_id, cod_c.strip()))
                        c.execute("""
                            INSERT INTO nomenclatura (id, empresa_id, codigo, nombre, tipo)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (c_id, selected_emp_id, cod_c.strip(), nom_c.strip(), tip_c))
                        conn.commit()
                        st.success(f"Cuenta contable '{cod_c.strip()} - {nom_c.strip()}' guardada exitosamente.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al registrar cuenta contable: {err}")
                    finally:
                        conn.close()
                else:
                    st.error("Por favor completa el Código y el Nombre de la cuenta.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("""
        SELECT codigo AS "Código", nombre AS "Nombre de la Cuenta", tipo AS "Tipo de Saldo" 
        FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC
    """, conn, params=(selected_emp_id,))
    conn.close()

    if df_nom.empty:
        st.info("No hay cuentas contables registradas para esta empresa.")
    else:
        st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 13. CONTROL DE USUARIOS (CREAR Y VISUALIZAR)
# ==========================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores y Asignación de Empresas")
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE role != 'admin' ORDER BY name ASC")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre ASC")
    all_emps = c.fetchall()
    conn.close()

    dict_emps_lookup = {e[0]: f"{e[1]} (NIT: {e[2]})" for e in all_emps}

    tab_crear_u, tab_ver_u = st.tabs(["➕ Crear Nuevo Usuario", "📋 Directorio de Usuarios y Accesos"])

    with tab_crear_u:
        with st.form("form_crear_operador", clear_on_submit=True):
            c1, c2 = st.columns(2)
            u_user = c1.text_input("Usuario (ej: operador1) *")
            u_nombre = c2.text_input("Nombre Completo *")

            c3, c4 = st.columns(2)
            u_email = c3.text_input("Correo Electrónico")
            u_pass = c4.text_input("Contraseña de Acceso *", type="password")

            rol_opcion = st.selectbox("Perfil / Rol de Acceso:", ["Operador (Solo Registra Facturas)", "Supervisor (Auditoría y Reportes)"])
            rol_final = "supervisor" if "Supervisor" in rol_opcion else "operator"

            st.markdown("##### 🏢 Empresas Autorizadas para este Usuario:")
            empresas_seleccionadas = []
            for emp in all_emps:
                if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"new_emp_{emp[0]}", value=True):
                    empresas_seleccionadas.append(emp[0])

            if st.form_submit_button("✅ Crear y Activar Usuario", type="primary"):
                if u_user.strip() and u_nombre.strip() and u_pass.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        assigned_txt = ",".join(empresas_seleccionadas) if empresas_seleccionadas else "none"
                        uid = f"usr-{int(datetime.now().timestamp())}"
                        
                        c.execute("""
                            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at, reset_requested)
                            VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, 0)
                            ON CONFLICT (username) DO UPDATE SET 
                                password_hash = EXCLUDED.password_hash,
                                name = EXCLUDED.name,
                                email = EXCLUDED.email,
                                role = EXCLUDED.role,
                                status = 'active',
                                assigned_empresas = EXCLUDED.assigned_empresas
                        """, (uid, u_user.strip().lower(), hash_password(u_pass.strip()), u_nombre.strip(), u_email.strip(), rol_final, assigned_txt, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Usuario '@{u_user.strip()}' creado exitosamente.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al registrar usuario: {err}")
                    finally:
                        conn.close()
                else:
                    st.error("Por favor completa el Usuario, Nombre y Contraseña.")

    with tab_ver_u:
        st.subheader("📋 Usuarios Registrados y Permisos")
        if not users_list:
            st.info("No hay usuarios operadores registrados aún.")
        else:
            for u in users_list:
                uid, uname, name, email, role, status, assigned = u
                with st.expander(f"👤 {name} (@{uname}) — Rol: {role.upper()}", expanded=False):
                    col1, col2 = st.columns([1.5, 2])
                    
                    with col1:
                        st.write(f"**Usuario:** `{uname}`")
                        st.write(f"**Nombre:** {name}")
                        st.write(f"**Correo:** {email or 'Sin correo registrado'}")
                        st.write(f"**Rol actual:** `{role.upper()}`")
                        st.write(f"**Estado de cuenta:** `{status}`")
                        
                        nueva_clave = st.text_input(f"Nueva clave para @{uname}:", type="password", key=f"pwd_{uid}")
                        if st.button(f"🔑 Cambiar Clave", key=f"btn_pwd_{uid}"):
                            if nueva_clave.strip():
                                conn = get_db_connection()
                                c = conn.cursor()
                                c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(nueva_clave.strip()), uid))
                                conn.commit()
                                conn.close()
                                st.success(f"Contraseña de @{uname} actualizada.")
                            else:
                                st.error("Ingresa una contraseña válida.")

                    with col2:
                        st.markdown("**🏢 Empresas Autorizadas:**")
                        ids_asig = [x.strip() for x in (assigned or "").split(",") if x.strip()]
                        if assigned == "all":
                            st.info("Acceso Total (Todas las empresas).")
                        elif not ids_asig or assigned == "none":
                            st.warning("⚠️ Sin empresas asignadas.")
                        else:
                            for eid in ids_asig:
                                st.markdown(f"- 🏢 **{dict_emps_lookup.get(eid, 'Empresa eliminada')}**")

                        st.markdown("---")
                        st.caption("Modificar empresas asignadas:")
                        nuevos_accesos = []
                        for emp in all_emps:
                            tiene = emp[0] in ids_asig or assigned == "all"
                            if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", value=tiene, key=f"chk_{uid}_{emp[0]}"):
                                nuevos_accesos.append(emp[0])

                        if st.button(f"💾 Guardar Permisos para @{uname}", key=f"btn_save_{uid}"):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("UPDATE users SET assigned_empresas = %s WHERE id = %s", (",".join(nuevos_accesos), uid))
                            conn.commit()
                            conn.close()
                            st.success("Permisos de empresa actualizados.")
                            st.rerun()

# ==========================================
# 14. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
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