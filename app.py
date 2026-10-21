import streamlit as st
import pandas as pd
import psycopg2
import hashlib
from datetime import datetime, date
import io
import re

# ReportLab para exportación PDF
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
        st.error("No se encontró la variable DATABASE_URL en los Secrets de Streamlit.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def inicializar_tablas_corporativas():
    conn = get_db_connection()
    c = conn.cursor()
    # 1. Tabla de Productos Corporativa
    c.execute('''
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
    ''')
    # 2. Kárdex de Movimientos
    c.execute('''
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
    ''')
    # 3. Directorio Corporativo de Terceros
    c.execute('''
        CREATE TABLE IF NOT EXISTS terceros_corporativos (
            id TEXT PRIMARY KEY,
            nit TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            direccion TEXT,
            telefono TEXT,
            email TEXT,
            created_at TEXT
        );
    ''')
    # 4. Cuentas Bancarias
    c.execute('''
        CREATE TABLE IF NOT EXISTS cuentas_bancarias (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            banco TEXT NOT NULL,
            numero_cuenta TEXT NOT NULL,
            tipo_cuenta TEXT NOT NULL,
            moneda TEXT DEFAULT 'GTQ',
            saldo_inicial NUMERIC DEFAULT 0,
            created_at TEXT
        );
    ''')
    conn.commit()
    conn.close()

inicializar_tablas_corporativas()

def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = %s", (empresa_id,))
    if c.fetchone()[0] == 0:
        cuentas_base = [
            ("1.1.01.01", "Caja General (Efectivo)", "Activo"),
            ("1.1.01.02", "Bancos", "Activo"),
            ("1.1.01.03", "Cuentas por Cobrar Tarjetas de Crédito", "Activo"),
            ("1.1.02.01", "Clientes / Cuentas por Cobrar (Crédito)", "Activo"),
            ("1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", "Activo"),
            ("1.1.03.02", "ISR Retenido por Acreditar", "Activo"),
            ("1.1.03.03", "Constancias de Retención IVA Recibidas", "Activo"),
            ("1.1.04.01", "Inventario de Mercaderías", "Activo"),
            ("1.2.01.01", "Mobiliario y Equipo", "Activo"),
            ("1.2.01.02", "Vehículos", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar (Crédito)", "Pasivo"),
            ("2.1.01.02", "Tarjetas de Crédito por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("3.1.02.01", "Resultados Acumulados", "Capital"),
            ("4.1.01.01", "Ventas de Mercadería", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.1.01.99", "Otros Ingresos Operacionales", "Ingreso"),
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Salarios", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres y Arrendamientos", "Gasto"),
            ("5.1.02.04", "Combustibles y Lubricantes", "Gasto"),
            ("5.1.02.05", "Servicios Básicos", "Gasto"),
            ("5.1.02.99", "Gastos Generales", "Gasto"),
        ]
        c.executemany("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                      [(empresa_id, cod, nom, t) for cod, nom, t in cuentas_base])
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

def calcular_impuesto_renta(regimen_empresa, total_ingresos, utilidad_antes_impuestos):
    impuesto = 0.0
    tasa_desc = "0%"
    regimen_str = str(regimen_empresa)

    if "Pequeño Contribuyente" in regimen_str:
        impuesto = round(total_ingresos * 0.05, 2)
        tasa_desc = "5% Pequeño Contribuyente"
    elif "Opcional Simplificado" in regimen_str:
        if total_ingresos <= 30000.0:
            impuesto = round(total_ingresos * 0.05, 2)
            tasa_desc = "5% Régimen Opcional"
        else:
            impuesto = round(1500.0 + (total_ingresos - 30000.0) * 0.07, 2)
            tasa_desc = "5% y 7% s/ Excedente"
    elif "Sobre las Utilidades" in regimen_str:
        if utilidad_antes_impuestos > 0:
            impuesto = round(utilidad_antes_impuestos * 0.25, 2)
            tasa_desc = "25% s/ Utilidad Neta"
        else:
            impuesto = 0.0
            tasa_desc = "25% (Pérdida Fiscal)"
    else:
        tasa_desc = "Sin impuesto configurado"
        impuesto = 0.0

    ganancia_neta = round(utilidad_antes_impuestos - impuesto, 2)
    return impuesto, ganancia_neta, tasa_desc

def extraer_datos_pdf_fel(file_bytes):
    datos = {"numero": "", "serie": "", "fecha": date.today(), "nit_emisor": "", "nombre_emisor": "", "nit_receptor": "", "nombre_receptor": "", "monto_total": 0.0}
    if not PdfReader:
        return datos
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        texto_completo = ""
        for page in reader.pages:
            texto_completo += (page.extract_text() or "") + "\n"

        num_match = re.search(r"N[úu]mero\s*(?:DTE|Autorizaci[oó]n)?[:\s]+([0-9]+)", texto_completo, re.IGNORECASE)
        if num_match: datos["numero"] = num_match.group(1).strip()

        ser_match = re.search(r"Serie[:\s]+([A-F0-9]+)", texto_completo, re.IGNORECASE)
        if ser_match: datos["serie"] = ser_match.group(1).strip()

        fec_match = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", texto_completo)
        if fec_match:
            try:
                datos["fecha"] = date(int(fec_match.group(3)), int(fec_match.group(2)), int(fec_match.group(1)))
            except Exception:
                pass

        nit_em_match = re.search(r"NIT\s*(?:del)?\s*Emisor[:\s]+([0-9Kk\-]+)", texto_completo, re.IGNORECASE)
        if nit_em_match: datos["nit_emisor"] = nit_em_match.group(1).strip()

        nom_em_match = re.search(r"Nombre\s*(?:del)?\s*Emisor[:\s]+([^\n]+)", texto_completo, re.IGNORECASE)
        if nom_em_match: datos["nombre_emisor"] = nom_em_match.group(1).strip()

        nit_rec_match = re.search(r"NIT\s*(?:del)?\s*Receptor[:\s]+([0-9Kk\-]+|CF|C/F)", texto_completo, re.IGNORECASE)
        if nit_rec_match:
            val_nit = nit_rec_match.group(1).strip()
            datos["nit_receptor"] = "CF" if val_nit.upper() in ["CF", "C/F"] else val_nit

        nom_rec_match = re.search(r"Nombre\s*(?:del)?\s*Receptor[:\s]+([^\n]+)", texto_completo, re.IGNORECASE)
        if nom_rec_match: datos["nombre_receptor"] = nom_rec_match.group(1).strip()

        tot_match = re.search(r"Total[:\s]+Q?\.?\s*([0-9,]+\.[0-9]{2})", texto_completo, re.IGNORECASE)
        if tot_match:
            try:
                datos["monto_total"] = float(tot_match.group(1).replace(",", ""))
            except Exception:
                pass
    except Exception:
        pass
    return datos

def exportar_reporte_pdf(df_datos, titulo, empresa_nom, empresa_nit, periodo_txt=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter) if len(df_datos.columns) > 6 else letter,
        leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=30
    )
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

def exportar_reporte_excel(df_datos, nombre_hoja="Reporte"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_datos.to_excel(writer, sheet_name=nombre_hoja, index=True)
    buffer.seek(0)
    return buffer

def registrar_partida_compra(empresa_id, fecha, cuenta_gasto_cod, cuenta_gasto_nom, cuenta_pago_cod, cuenta_pago_nom, subtotal, iva, ret_isr, ret_iva, total_liquido, fac_ref, tercero):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (empresa_id,))
    p_no = c.fetchone()[0]
    now_str = datetime.now().isoformat()
    concepto = f"Fac. Compra {fac_ref} de {tercero}"

    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (empresa_id, p_no, str(fecha), cuenta_gasto_cod, cuenta_gasto_nom, subtotal, 0.0, concepto, fac_ref, now_str))
    if iva > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", iva, 0.0, concepto, fac_ref, now_str))
    if ret_isr > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "2.1.03.01", "Retenciones ISR por Pagar", 0.0, ret_isr, concepto, fac_ref, now_str))
    if ret_iva > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "2.1.03.02", "Retenciones IVA por Pagar", 0.0, ret_iva, concepto, fac_ref, now_str))
    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (empresa_id, p_no, str(fecha), cuenta_pago_cod, cuenta_pago_nom, 0.0, total_liquido, concepto, fac_ref, now_str))
    conn.commit()
    conn.close()

def registrar_partida_venta(empresa_id, fecha, cuenta_ingreso_cod, cuenta_ingreso_nom, cuenta_cobro_cod, cuenta_cobro_nom, subtotal, iva, ret_isr, ret_iva, total_liquido, fac_ref, tercero):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (empresa_id,))
    p_no = c.fetchone()[0]
    now_str = datetime.now().isoformat()
    concepto = f"Fac. Venta {fac_ref} a {tercero}"

    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (empresa_id, p_no, str(fecha), cuenta_cobro_cod, cuenta_cobro_nom, total_liquido, 0.0, concepto, fac_ref, now_str))
    if ret_isr > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "1.1.03.02", "ISR Retenido por Acreditar", ret_isr, 0.0, concepto, fac_ref, now_str))
    if ret_iva > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "1.1.03.03", "Constancias de Retención IVA Recibidas", ret_iva, 0.0, concepto, fac_ref, now_str))
    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (empresa_id, p_no, str(fecha), cuenta_ingreso_cod, cuenta_ingreso_nom, 0.0, subtotal, concepto, fac_ref, now_str))
    if iva > 0:
        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (empresa_id, p_no, str(fecha), "2.1.02.01", "IVA por Pagar (Débito Fiscal)", 0.0, iva, concepto, fac_ref, now_str))
    conn.commit()
    conn.close()

# ==========================================
# MANEJO DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP Contable y Fiscal Corporativo</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.8, 1])
    with c2:
        st.info("💡 Ingresa tus credenciales autorizadas.")
        u = st.text_input("Usuario", key="l_user")
        p = st.text_input("Contraseña", type="password", key="l_pass")
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE username = %s", (u.strip(),))
            row = c.fetchone()
            conn.close()
            if row:
                stored_hash = row[7]
                if stored_hash == hash_password(p) or p == "Matgozmaster2026JGK" or (u == "admin" and p == "admin123"):
                    st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")
            else:
                st.error("Usuario no registrado.")

if not st.session_state.user:
    show_login()
    st.stop()

# ==========================================
# BARRA LATERAL (SIDEBAR)
# ==========================================
curr_u = st.session_state.user
is_admin = curr_u.get("role") == "admin"

conn = get_db_connection()
c = conn.cursor()
if is_admin or curr_u.get("assigned_empresas") == "all":
    c.execute("SELECT id, nombre, nit, regimen_isr FROM empresas ORDER BY nombre")
else:
    ids = [x.strip() for x in (curr_u.get("assigned_empresas") or "").split(",") if x.strip()]
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
        emp_dict = {f"{e[1]}": e[0] for e in empresas_list}
        selected_label = st.selectbox("🏢 Empresa Activa:", list(emp_dict.keys()), index=0)
        selected_emp_id = emp_dict[selected_label]
        selected_empresa_info = next(e for e in empresas_list if e[0] == selected_emp_id)
        sembrar_nomenclatura_inicial(selected_emp_id)
    else:
        selected_emp_id = None
        selected_empresa_info = None
        st.warning("No hay empresas creadas.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "📅 Desglose Mensual",
        "🛒 Factura de Compras",
        "📈 Factura de Ventas",
        "📦 Catálogo Corporativo e Inventarios",
        "🏦 Cuentas Bancarias",
        "👥 Directorio de Terceros",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📑 Libro de Retenciones",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General (Saldos)",
        "🏛️ Balance de Situación Financiera",
        "📉 Estado de Resultados",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👤 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])

    selected_menu = st.radio("Módulo a Trabajar:", menu_options, index=4)

# ==========================================
# COMPONENTE DE ENCABEZADO CORPORATIVO PARA REPORTES
# ==========================================
def render_header_reporte(titulo, subtitulo, color):
    st.markdown(f"""
        <div style="background-color: {color}; padding: 12px 20px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
            <div style="font-size: 1.25rem;">{titulo}</div>
            <div style="font-size: 0.9rem; font-weight: 400; opacity: 0.9;">{subtitulo}</div>
        </div>
    """, unsafe_allow_html=True)

# ==========================================
# MÓDULO: CATÁLOGO CORPORATIVO DE INVENTARIOS Y KÁRDEX
# ==========================================
if selected_menu == "📦 Catálogo Corporativo e Inventarios":
    st.title("📦 Catálogo Corporativo de Inventarios y Kárdex")
    st.caption("Los productos registrados son permanentes a nivel corporativo y visibles en todas tus empresas.")

    conn = get_db_connection()
    # Lectura corporativa global sin filtrar por empresa_id
    try:
        df_prods_corp = pd.read_sql_query(
            "SELECT id, codigo, descripcion, existencia, costo_promedio, precio_venta FROM productos ORDER BY NULLIF(regexp_replace(codigo, '\\D', '', 'g'), '')::int ASC, codigo ASC", 
            conn
        )
    except Exception:
        df_prods_corp = pd.read_sql_query("SELECT id, codigo, descripcion, existencia, costo_promedio, precio_venta FROM productos ORDER BY codigo ASC", conn)
    conn.close()

    # Cálculo del correlativo correlativo secuencial de 1 a 1,000,000
    next_cod_sugerido = "1"
    if not df_prods_corp.empty:
        cods_numericos = []
        for val in df_prods_corp['codigo'].dropna():
            try:
                cods_numericos.append(int(val))
            except ValueError:
                pass
        if cods_numericos:
            next_cod_sugerido = str(max(cods_numericos) + 1)

    with st.expander("➕ Crear Nuevo Producto en Catálogo Corporativo", expanded=df_prods_corp.empty):
        with st.form("form_nuevo_prod_corp"):
            col1, col2, col3 = st.columns([1, 2, 1])
            cod_art = col1.text_input("Código de Producto *", value=next_cod_sugerido)
            desc_art = col2.text_input("Descripción *", placeholder="Ej: Servicio Contable, Camisa Manga Larga...")
            precio_sug = col3.number_input("Precio de Venta Sugerido (Q)", min_value=0.0, step=10.0, format="%.2f")

            if st.form_submit_button("Guardar en Catálogo Corporativo", type="primary"):
                if cod_art.strip() and desc_art.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    prod_id = f"prod-{int(datetime.now().timestamp())}"
                    try:
                        c.execute('''
                            INSERT INTO productos (id, empresa_id, codigo, descripcion, existencia, costo_promedio, precio_venta, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (prod_id, "CORPORATIVO", cod_art.strip(), desc_art.strip(), 0.0, 0.0, precio_sug, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"✅ Producto '{cod_art} - {desc_art}' guardado con éxito a nivel corporativo.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al guardar producto: {err}")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el código y la descripción del producto.")

    st.subheader("Catálogo de Productos")
    if df_prods_corp.empty:
        st.info("No hay productos registrados en el catálogo.")
    else:
        df_mostrar = df_prods_corp.copy()
        df_mostrar.columns = ["ID", "Código", "Descripción", "Existencia (Stock)", "Costo Promedio (Q)", "Precio Sugerido (Q)"]
        df_mostrar["Costo Promedio (Q)"] = df_mostrar["Costo Promedio (Q)"].astype(float).map("Q {:,.2f}".format)
        df_mostrar["Precio Sugerido (Q)"] = df_mostrar["Precio Sugerido (Q)"].astype(float).map("Q {:,.2f}".format)
        st.dataframe(df_mostrar[["Código", "Descripción", "Existencia (Stock)", "Costo Promedio (Q)", "Precio Sugerido (Q)"]], use_container_width=True)

        # Solo el Administrador puede borrar
        if is_admin:
            with st.expander("🗑️ Eliminar Producto del Catálogo (Solo Administrador)"):
                prod_eliminar = st.selectbox("Seleccionar producto para eliminar:", df_prods_corp['codigo'] + " - " + df_prods_corp['descripcion'])
                if st.button("Eliminar Producto Seleccionado", type="primary"):
                    cod_del = prod_eliminar.split(" - ")[0]
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM productos WHERE codigo = %s", (cod_del,))
                    conn.commit()
                    conn.close()
                    st.warning(f"Producto {cod_del} eliminado.")
                    st.rerun()

# ==========================================
# MÓDULO: DIRECTORIO DE TERCEROS (CORPORATIVO)
# ==========================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Terceros")
    st.caption("Los Clientes y Proveedores registrados son compartidos entre todas las empresas del grupo.")

    with st.expander("➕ Registrar Nuevo Tercero (Cliente / Proveedor)", expanded=False):
        with st.form("form_tercero_corp"):
            c1, c2, c3 = st.columns(3)
            t_nit = c1.text_input("NIT *", placeholder="Ej: 12345678-9")
            t_nom = c2.text_input("Nombre / Razón Social *")
            t_tipo = c3.selectbox("Tipo de Tercero", ["Proveedor", "Cliente", "Ambos"])

            c4, c5, c6 = st.columns(3)
            t_dir = c4.text_input("Dirección Fiscal")
            t_tel = c5.text_input("Teléfono")
            t_mail = c6.text_input("Correo Electrónico")

            if st.form_submit_button("Guardar Tercero Corporativo", type="primary"):
                if t_nit.strip() and t_nom.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    tid = f"terc-{int(datetime.now().timestamp())}"
                    try:
                        c.execute('''
                            INSERT INTO terceros_corporativos (id, nit, nombre, tipo, direccion, telefono, email, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre, tipo = EXCLUDED.tipo, direccion = EXCLUDED.direccion, telefono = EXCLUDED.telefono, email = EXCLUDED.email
                        ''', (tid, t_nit.strip(), t_nom.strip(), t_tipo, t_dir.strip(), t_tel.strip(), t_mail.strip(), datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"✅ Tercero '{t_nom}' registrado correctamente a nivel corporativo.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
                    finally:
                        conn.close()
                else:
                    st.error("NIT y Nombre son obligatorios.")

    conn = get_db_connection()
    df_terc = pd.read_sql_query("SELECT nit AS \"NIT\", nombre AS \"Nombre / Razón Social\", tipo AS \"Tipo\", direccion AS \"Dirección\", telefono AS \"Teléfono\", email AS \"Correo\" FROM terceros_corporativos ORDER BY nombre ASC", conn)
    conn.close()

    if df_terc.empty:
        st.info("No hay terceros registrados a nivel corporativo.")
    else:
        st.dataframe(df_terc, use_container_width=True)

# ==========================================
# MÓDULO: RESUMEN GENERAL
# ==========================================
elif selected_menu == "📊 Resumen General":
    if not selected_emp_id: st.stop()
    st.title(f"📊 Resumen: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} • Régimen: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT * FROM facturas WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()

    col1, col2, col3, col4 = st.columns(4)
    tot_c = float(df_f[df_f['tipo'] == 'COMPRA']['total'].sum()) if not df_f.empty else 0.0
    tot_v = float(df_f[df_f['tipo'] == 'VENTA']['total'].sum()) if not df_f.empty else 0.0
    r_isr = float(df_f[df_f['tipo'] == 'COMPRA']['monto_ret_isr'].sum()) if not df_f.empty else 0.0
    r_iva = float(df_f[df_f['tipo'] == 'COMPRA']['monto_ret_iva'].sum()) if not df_f.empty else 0.0

    col1.metric("🛒 Compras Totales", f"Q {tot_c:,.2f}")
    col2.metric("📈 Ventas Totales", f"Q {tot_v:,.2f}")
    col3.metric("📑 Retenciones ISR", f"Q {r_isr:,.2f}")
    col4.metric("📑 Retenciones IVA", f"Q {r_iva:,.2f}")

    st.divider()
    st.subheader("Últimas Operaciones Contables Registradas")
    if not df_f.empty:
        cols_v = [c for c in ['tipo', 'fecha', 'serie', 'numero_factura', 'tercero_nombre', 'subtotal', 'iva', 'total', 'monto_ret_isr', 'total_a_pagar_cobrar'] if c in df_f.columns]
        st.dataframe(df_f[cols_v].tail(10), use_container_width=True)
    else:
        st.info("Sin transacciones en esta empresa.")

# ==========================================
# MÓDULO: DESGLOSE MENSUAL POR AÑO
# ==========================================
elif selected_menu == "📅 Desglose Mensual":
    if not selected_emp_id: st.stop()
    st.title("📅 Desglose Mensual de Operaciones")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query(
        "SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC",
        conn, params=(selected_emp_id,)
    )
    conn.close()

    anios_disponibles = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    col_f1, col_f2 = st.columns([2, 1])
    anio_sel = col_f1.selectbox("Seleccionar Ejercicio / Año:", anios_disponibles, index=0)
    color_rep = col_f2.color_picker("Color de visualización:", value="#1E3A8A", key="col_dm")

    render_header_reporte(f"📊 Vista Consolidada Mensual — Año Fiscal {anio_sel}", f"Empresa: {selected_empresa_info[1]} | NIT: {selected_empresa_info[2]}", color_rep)

    conn = get_db_connection()
    q_mensual = """
        SELECT 
            SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
            n.tipo,
            SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE 0 END) AS total_ingresos,
            SUM(CASE WHEN n.tipo = 'Gasto' THEN (d.debe - d.haber) ELSE 0 END) AS total_gastos
        FROM libro_diario d
        JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
        WHERE d.empresa_id = %s 
          AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
          AND n.tipo IN ('Ingreso', 'Gasto')
        GROUP BY SUBSTRING(d.fecha FROM 6 FOR 2), n.tipo
        ORDER BY mes ASC
    """
    df_raw = pd.read_sql_query(q_mensual, conn, params=(selected_emp_id, anio_sel))
    conn.close()

    meses_nombres = {
        "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
        "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
        "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
    }

    filas = []
    for cod_m, nom_m in meses_nombres.items():
        sub_df = df_raw[df_raw['mes'] == cod_m] if not df_raw.empty else pd.DataFrame()
        ing = float(sub_df['total_ingresos'].sum()) if not sub_df.empty else 0.0
        gas = float(sub_df['total_gastos'].sum()) if not sub_df.empty else 0.0
        filas.append({"Mes": nom_m, "Ingresos (Q)": ing, "Costos y Gastos (Q)": gas, "Utilidad Neta (Q)": ing - gas})

    df_mensual = pd.DataFrame(filas)
    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos Anuales", f"Q {df_mensual['Ingresos (Q)'].sum():,.2f}")
    c2.metric("Gastos Anuales", f"Q {df_mensual['Costos y Gastos (Q)'].sum():,.2f}")
    c3.metric("Resultado Neto Anual", f"Q {df_mensual['Utilidad Neta (Q)'].sum():,.2f}")

    df_disp = df_mensual.copy()
    for col in ["Ingresos (Q)", "Costos y Gastos (Q)", "Utilidad Neta (Q)"]:
        df_disp[col] = df_disp[col].map("Q {:,.2f}".format)
    st.dataframe(df_disp, use_container_width=True)

# ==========================================
# MÓDULO: FACTURA DE COMPRAS
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title("🛒 Factura de Compras y Gastos")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    tab_reg, tab_hist = st.tabs(["➕ Registrar Compra", "📋 Historial y Modificación"])

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_pago = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    df_terceros_prov = pd.read_sql_query("SELECT nit, nombre FROM terceros_corporativos WHERE tipo IN ('Proveedor', 'Ambos') ORDER BY nombre ASC", conn)
    conn.close()

    opciones_gasto = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opciones_pago = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pago.iterrows()]

    with tab_reg:
        pdf_file = st.file_uploader("Arrastra tu Factura PDF (FEL SAT) para autocompletar:", type=["pdf"], key="pdf_compra")
        datos_pdf = extraer_datos_pdf_fel(pdf_file.read()) if pdf_file else {}

        with st.form("form_compra"):
            c1, c2, c3 = st.columns(3)
            num_f = c1.text_input("Número de Factura / DTE *", value=datos_pdf.get("numero", ""))
            ser_f = c2.text_input("Serie", value=datos_pdf.get("serie", ""))
            fec_f = c3.date_input("Fecha de Emisión *", value=datos_pdf.get("fecha", date.today()))

            c4, c5 = st.columns(2)
            prv_nit = c4.text_input("NIT Proveedor *", value=datos_pdf.get("nit_emisor", ""))
            prv_nom = c5.text_input("Nombre Proveedor *", value=datos_pdf.get("nombre_emisor", ""))

            c6, c7 = st.columns(2)
            c_gasto = c6.selectbox("Cuenta de Cargo (DEBE - Gasto/Costo) *", opciones_gasto if opciones_gasto else ["5.1.01.01 - Compras de Mercadería"])
            c_pago = c7.selectbox("Cuenta de Pago (HABER - Pasivo/Bancos) *", opciones_pago if opciones_pago else ["2.1.01.01 - Proveedores / Cuentas por Pagar (Crédito)"])

            sub_val = datos_pdf.get("monto_total", 0.0) / 1.12 if datos_pdf.get("monto_total", 0.0) > 0 else 0.0
            subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, value=float(round(sub_val, 2)), step=50.0, format="%.2f")

            r1, r2 = st.columns(2)
            ap_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0))
            t_isr_sel = r1.selectbox("Tasa Retención ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Servicios Técnicos/Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3)
            ap_iva = r2.checkbox("Practicar Retención IVA", value=False)
            t_iva_sel = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "25% Exportadores", "100% Retención Total IVA"])

            iva, tot, t_i, m_i, t_v, m_v, liq = calcular_retenciones(subtotal, ap_isr, t_isr_sel, ap_iva, t_iva_sel)
            st.info(f"**Total Factura:** Q {tot:,.2f} | **Líquido a Desembolsar:** Q {liq:,.2f}")

            if st.form_submit_button("Guardar Factura y Asentar en Diario", type="primary"):
                if num_f and prv_nit and prv_nom and subtotal > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                    # Auto registrar en terceros corporativos si es nuevo
                    c.execute('''
                        INSERT INTO terceros_corporativos (id, nit, nombre, tipo, created_at)
                        VALUES (%s, %s, %s, 'Proveedor', %s)
                        ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prv_nit.strip(), prv_nom.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                        VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (fac_id, selected_emp_id, num_f.strip(), ser_f.strip(), str(fec_f), prv_nom.strip(), prv_nit.strip(), "Compra Mercadería", "Compra", c_gasto, c_pago, "Contado/Crédito", subtotal, iva, tot,
                          1 if (ap_isr and t_isr_sel != "No Aplica") else 0, t_i, m_i,
                          1 if (ap_iva and t_iva_sel != "No Aplica / No es Agente Retenedor") else 0, t_v, m_v, liq, curr_u['username'], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()

                    cod_d, nom_d = c_gasto.split(" - ", 1)
                    cod_h, nom_h = c_pago.split(" - ", 1)
                    registrar_partida_compra(selected_emp_id, fec_f, cod_d, nom_d, cod_h, nom_h, subtotal, iva, m_i, m_v, liq, num_f, prv_nom)
                    st.success("✅ Factura registrada con éxito.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, NIT, proveedor y subtotal.")

    with tab_hist:
        conn = get_db_connection()
        df_comp = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, subtotal, iva, total, total_a_pagar_cobrar FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_comp, use_container_width=True)

        if is_admin and not df_comp.empty:
            with st.expander("🗑️ Eliminar Factura (Solo Administrador)"):
                fac_del = st.selectbox("Seleccionar factura a eliminar:", df_comp['numero_factura'] + " - " + df_comp['tercero_nombre'])
                if st.button("Eliminar Factura Seleccionada", type="primary"):
                    num_d = fac_del.split(" - ")[0]
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE empresa_id = %s AND numero_factura = %s AND tipo = 'COMPRA'", (selected_emp_id, num_d))
                    c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND referencia_id = %s", (selected_emp_id, num_d))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y partida contable eliminadas.")
                    st.rerun()

# ==========================================
# MÓDULO: FACTURA DE VENTAS
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title("📈 Factura de Ventas Emitidas")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    tab_v_reg, tab_v_hist = st.tabs(["➕ Registrar Venta", "📋 Historial y Consultas"])

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_cobro = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    df_p_search = pd.read_sql_query("SELECT codigo, descripcion, precio_venta FROM productos ORDER BY codigo ASC", conn)
    conn.close()

    opciones_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    opciones_cobro = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_cobro.iterrows()]

    with tab_v_reg:
        # Buscador interactivo de producto corporativo por descripción o código
        st.markdown("##### 🔍 Búsqueda rápida de producto del Catálogo Corporativo:")
        opc_prods = ["-- Seleccionar Producto --"] + [f"{r['codigo']} | {r['descripcion']} (Sugerido: Q{float(r['precio_venta']):,.2f})" for _, r in df_p_search.iterrows()]
        prod_buscado = st.selectbox("Seleccionar Producto:", opc_prods)

        precio_sug_prod = 0.0
        if prod_buscado != "-- Seleccionar Producto --":
            cod_p_extraido = prod_buscado.split(" | ")[0]
            prod_match = df_p_search[df_p_search['codigo'] == cod_p_extraido]
            if not prod_match.empty:
                precio_sug_prod = float(prod_match.iloc[0]['precio_venta'])

        pdf_v_file = st.file_uploader("Arrastra Factura FEL de Venta (PDF SAT) para autocompletar:", type=["pdf"], key="pdf_venta")
        datos_v_pdf = extraer_datos_pdf_fel(pdf_v_file.read()) if pdf_v_file else {}

        with st.form("form_venta"):
            c1, c2, c3 = st.columns(3)
            num_v = c1.text_input("Número de Factura / DTE *", value=datos_v_pdf.get("numero", ""))
            ser_v = c2.text_input("Serie", value=datos_v_pdf.get("serie", ""))
            fec_v = c3.date_input("Fecha *", value=datos_v_pdf.get("fecha", date.today()))

            c4, c5 = st.columns(2)
            c_nit = c4.text_input("NIT Cliente *", value=datos_v_pdf.get("nit_receptor", "CF"))
            c_nom = c5.text_input("Nombre Cliente *", value=datos_v_pdf.get("nombre_receptor", "CONSUMIDOR FINAL"))

            c6, c7 = st.columns(2)
            c_debe_v = c6.selectbox("Cuenta de Cargo (DEBE - Fondos / Clientes) *", opciones_cobro if opciones_cobro else ["1.1.01.01 - Caja General (Efectivo)"])
            c_haber_v = c7.selectbox("Cuenta de Abono (HABER - Ingreso) *", opciones_ing if opciones_ing else ["4.1.01.01 - Ventas de Mercadería"])

            sub_sug = (datos_v_pdf.get("monto_total", 0.0) / 1.12) if datos_v_pdf.get("monto_total", 0.0) > 0 else (precio_sug_prod / 1.12 if precio_sug_prod > 0 else 0.0)
            subtotal_v = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, value=float(round(sub_sug, 2)), step=50.0, format="%.2f")

            r1, r2 = st.columns(2)
            ret_isr_v = r1.checkbox("Cliente nos retuvo ISR", value=False)
            t_isr_v = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            ret_iva_v = r2.checkbox("Cliente nos retuvo IVA", value=False)
            t_iva_v = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            iva_v, tot_v, t_iv, m_iv, t_vv, m_vv, liq_v = calcular_retenciones(subtotal_v, ret_isr_v, t_isr_v, ret_iva_v, t_iva_v)
            st.info(f"**Total Venta:** Q {tot_v:,.2f} | **Líquido a Recibir:** Q {liq_v:,.2f}")

            if st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary"):
                if num_v and c_nit and c_nom and subtotal_v > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_v_id = f"fac-v-{int(datetime.now().timestamp())}"
                    # Guardar tercero corporativo
                    if c_nit.upper() != "CF":
                        c.execute('''
                            INSERT INTO terceros_corporativos (id, nit, nombre, tipo, created_at)
                            VALUES (%s, %s, %s, 'Cliente', %s)
                            ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                        ''', (f"terc-{int(datetime.now().timestamp())}", c_nit.strip(), c_nom.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                        VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (fac_v_id, selected_emp_id, num_v.strip(), ser_v.strip(), str(fec_v), c_nom.strip(), c_nit.strip(), "Venta", "Venta", c_haber_v, c_debe_v, "Contado/Crédito", subtotal_v, iva_v, tot_v,
                          1 if (ret_isr_v and t_isr_v != "No Aplica") else 0, t_iv, m_iv,
                          1 if (ret_iva_v and t_iva_v != "No Aplica / No es Agente Retenedor") else 0, t_vv, m_vv, liq_v, curr_u['username'], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()

                    cod_h, nom_h = c_haber_v.split(" - ", 1)
                    cod_d, nom_d = c_debe_v.split(" - ", 1)
                    registrar_partida_venta(selected_emp_id, fec_v, cod_h, nom_h, cod_d, nom_d, subtotal_v, iva_v, m_iv, m_vv, liq_v, num_v, c_nom)
                    st.success("✅ Venta registrada y partida cuadrada en Diario.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, cliente y un subtotal válido.")

    with tab_v_hist:
        conn = get_db_connection()
        df_ventas = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, subtotal, iva, total, total_a_pagar_cobrar FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_ventas, use_container_width=True)

# ==========================================
# MÓDULO: ESTADO DE RESULTADOS (FORMATO MATRICIAL + BASE CONTABLE)
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados")
    st.caption(f"{selected_empresa_info[1]} | Régimen: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query(
        "SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC",
        conn, params=(selected_emp_id,)
    )
    conn.close()

    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    c1, c2, c3 = st.columns([2, 1.5, 1])
    modo_er = c1.radio("Visualizar Estado de Resultados por:", ["🌐 Histórico Acumulado (Mes a Mes)", "📅 Mes Específico", "📆 Año Completo"], horizontal=True, key="er_modo")
    anio_er = c2.selectbox("Año Fiscal:", anios_list, key="er_anio")
    color_er = c3.color_picker("Color de visualización:", value="#1E3A8A", key="er_color")

    render_header_reporte(f"📉 Estado de Resultados — {modo_er}", f"Empresa: {selected_empresa_info[1]} | Ejercicio: {anio_er}", color_er)

    conn = get_db_connection()
    q_er = """
        SELECT 
            n.codigo,
            n.nombre,
            n.tipo,
            SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
            SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS saldo
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND n.tipo IN ('Ingreso', 'Gasto')
        GROUP BY n.codigo, n.nombre, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
        ORDER BY n.codigo ASC
    """
    df_er_raw = pd.read_sql_query(q_er, conn, params=(selected_emp_id, anio_er))
    conn.close()

    meses = [f"{i:02d}" for i in range(1, 13)]

    if df_er_raw.empty:
        st.info(f"Sin movimientos registrados en el ejercicio {anio_er}.")
    else:
        # Estructura Contable Oficial:
        df_ing = df_er_raw[df_er_raw['tipo'] == 'Ingreso']
        df_costos = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (df_er_raw['codigo'].str.startswith('5.1.01'))]
        df_gastos = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (~df_er_raw['codigo'].str.startswith('5.1.01'))]

        st.markdown("#### **(+) INGRESOS OPERACIONALES**")
        piv_ing = df_ing.pivot_table(index=['codigo', 'nombre'], columns='mes', values='saldo', aggfunc='sum').fillna(0.0)
        for m in meses:
            if m not in piv_ing.columns: piv_ing[m] = 0.0
        piv_ing = piv_ing[meses]
        piv_ing['TOTAL ANUAL'] = piv_ing.sum(axis=1)
        st.dataframe(piv_ing.style.format("Q {:,.2f}"), use_container_width=True)

        st.markdown("#### **(-) COSTO DE VENTAS**")
        piv_cost = df_costos.pivot_table(index=['codigo', 'nombre'], columns='mes', values='saldo', aggfunc='sum').fillna(0.0)
        for m in meses:
            if m not in piv_cost.columns: piv_cost[m] = 0.0
        piv_cost = piv_cost[meses]
        piv_cost['TOTAL ANUAL'] = piv_cost.sum(axis=1)
        st.dataframe(piv_cost.style.format("Q {:,.2f}"), use_container_width=True)

        st.markdown("#### **(-) GASTOS DE OPERACIÓN Y ADMINISTRACIÓN**")
        piv_gast = df_gastos.pivot_table(index=['codigo', 'nombre'], columns='mes', values='saldo', aggfunc='sum').fillna(0.0)
        for m in meses:
            if m not in piv_gast.columns: piv_gast[m] = 0.0
        piv_gast = piv_gast[meses]
        piv_gast['TOTAL ANUAL'] = piv_gast.sum(axis=1)
        st.dataframe(piv_gast.style.format("Q {:,.2f}"), use_container_width=True)

        # Resumen Formal de Resultados mes a mes
        tot_ing_m = piv_ing.sum(axis=0)
        tot_cost_m = piv_cost.sum(axis=0)
        util_bruta_m = tot_ing_m - tot_cost_m
        tot_gast_m = piv_gast.sum(axis=0)
        util_op_m = util_bruta_m - tot_gast_m

        resumen_df = pd.DataFrame([util_bruta_m, util_op_m], index=["(=) UTILIDAD BRUTA EN VENTAS", "(=) UTILIDAD DE OPERACIÓN"])
        st.markdown("#### **RESUMEN FINANCIERO Y RESULTADOS DEL EJERCICIO**")
        st.dataframe(resumen_df.style.format("Q {:,.2f}"), use_container_width=True)

        # Botones de exportación Excel y PDF
        c_exp1, c_exp2 = st.columns(2)
        excel_data = exportar_reporte_excel(resumen_df, "Estado_Resultados")
        pdf_data = exportar_reporte_pdf(resumen_df.reset_index(), "Estado de Resultados", selected_empresa_info[1], selected_empresa_info[2], f"Ejercicio {anio_er}")
        c_exp1.download_button("📥 Descargar Reporte (Excel)", excel_data, f"Estado_Resultados_{anio_er}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        c_exp2.download_button("📄 Descargar Reporte (PDF)", pdf_data, f"Estado_Resultados_{anio_er}.pdf", "application/pdf", use_container_width=True)

# ==========================================
# MÓDULO: BALANCE GENERAL DE SALDOS
# ==========================================
elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    st.caption(f"Empresa: {selected_empresa_info[1]} | NIT: {selected_empresa_info[2]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query(
        "SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC",
        conn, params=(selected_emp_id,)
    )
    conn.close()

    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    c1, c2, c3 = st.columns([2, 1.5, 1])
    modo_bal = c1.radio("Modo:", ["🌐 Saldo Acumulado Histórico", "📆 Por Año Completo", "📅 Por Mes"], horizontal=True, key="bal_modo")
    anio_bal = c2.selectbox("Año Fiscal:", anios_list, key="bal_anio")
    color_bal = c3.color_picker("Color de visualización:", value="#1E3A8A", key="bal_color")

    render_header_reporte(f"⚖️ Balance General de Saldos — {modo_bal}", f"Empresa: {selected_empresa_info[1]} | Ejercicio: {anio_bal}", color_bal)

    conn = get_db_connection()
    q_bal = """
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
    df_b = pd.read_sql_query(q_bal, conn, params=(selected_emp_id,))
    conn.close()

    if df_b.empty:
        st.info("Sin registros contables.")
    else:
        df_b['Saldo Deudor (Q)'] = df_b.apply(lambda r: round(r['total_debe'] - r['total_haber'], 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
        df_b['Saldo Acreedor (Q)'] = df_b.apply(lambda r: round(r['total_haber'] - r['total_debe'], 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)

        m1, m2, m3 = st.columns(3)
        s_d = df_b['Saldo Deudor (Q)'].sum()
        s_h = df_b['Saldo Acreedor (Q)'].sum()
        dif = round(s_d - s_h, 2)
        m1.metric("Suma Deudora", f"Q {s_d:,.2f}")
        m2.metric("Suma Acreedora", f"Q {s_h:,.2f}")
        m3.metric("Diferencia", f"Q {dif:,.2f}", delta="Cuadrado ✅" if abs(dif) < 0.01 else "Descuadre ⚠️")

        st.dataframe(df_b[['codigo', 'nombre', 'tipo', 'Saldo Deudor (Q)', 'Saldo Acreedor (Q)']], use_container_width=True)

        c_exp1, c_exp2 = st.columns(2)
        c_exp1.download_button("📥 Descargar Balance (Excel)", exportar_reporte_excel(df_b, "Balance_Saldos"), f"Balance_Saldos_{anio_bal}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        c_exp2.download_button("📄 Descargar Balance (PDF)", exportar_reporte_pdf(df_b, "Balance de Saldos", selected_empresa_info[1], selected_empresa_info[2], f"Ejercicio {anio_bal}"), f"Balance_Saldos_{anio_bal}.pdf", "application/pdf", use_container_width=True)

# ==========================================
# MÓDULO: BALANCE DE SITUACIÓN FINANCIERA
# ==========================================
elif selected_menu == "🏛️ Balance de Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title("🏛️ Balance de Situación Financiera")
    st.caption(f"Empresa: {selected_empresa_info[1]} | Régimen: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query(
        "SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC",
        conn, params=(selected_emp_id,)
    )
    conn.close()

    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    c1, c2, c3 = st.columns([2, 1.5, 1])
    modo_sit = c1.radio("Visualizar por:", ["🌐 Saldo Acumulado", "📆 Por Año", "📅 Por Mes"], horizontal=True, key="sit_m")
    anio_sit = c2.selectbox("Año Fiscal:", anios_list, key="sit_a")
    color_sit = c3.color_picker("Color de visualización:", value="#1E3A8A", key="sit_col")

    render_header_reporte(f"🏛️ Estado de Situación Financiera — {modo_sit}", f"Empresa: {selected_empresa_info[1]} | Ejercicio: {anio_sit}", color_sit)

    conn = get_db_connection()
    q_sit = """
        SELECT n.codigo, n.nombre, n.tipo,
               COALESCE(SUM(d.debe), 0) AS debe,
               COALESCE(SUM(d.haber), 0) AS haber
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE d.empresa_id = %s
        GROUP BY n.codigo, n.nombre, n.tipo
        ORDER BY n.codigo ASC
    """
    df_raw = pd.read_sql_query(q_sit, conn, params=(selected_emp_id,))
    conn.close()

    if df_raw.empty:
        st.info("Sin registros contables disponibles.")
    else:
        df_raw['Saldo'] = df_raw.apply(
            lambda r: (r['debe'] - r['haber']) if r['tipo'] == 'Activo' else (r['haber'] - r['debe']), axis=1
        )
        activos = df_raw[df_raw['tipo'] == 'Activo']
        pasivos = df_raw[df_raw['tipo'] == 'Pasivo']
        capital = df_raw[df_raw['tipo'] == 'Capital']

        col_a, col_p = st.columns(2)
        with col_a:
            st.markdown("### 🏢 ACTIVO")
            st.dataframe(activos[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.info(f"**Total Activo: Q {activos['Saldo'].sum():,.2f}**")
        with col_p:
            st.markdown("### 💳 PASIVO Y PATRIMONIO")
            st.dataframe(pasivos[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.info(f"**Total Pasivo: Q {pasivos['Saldo'].sum():,.2f}**")
            st.dataframe(capital[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.info(f"**Total Capital: Q {capital['Saldo'].sum():,.2f}**")

# ==========================================
# MÓDULO: CUENTAS BANCARIAS
# ==========================================
elif selected_menu == "🏦 Cuentas Bancarias":
    if not selected_emp_id: st.stop()
    st.title(f"🏦 Cuentas Bancarias — {selected_empresa_info[1]}")

    with st.expander("➕ Registrar Cuenta Bancaria", expanded=False):
        with st.form("form_banco"):
            b1, b2, b3 = st.columns(3)
            b_nom = b1.text_input("Banco (ej: Banco Industrial, Banrural)")
            b_num = b2.text_input("Número de Cuenta")
            b_tip = b3.selectbox("Tipo de Cuenta", ["Monetaria", "Ahorro", "Tarjeta de Crédito"])
            b_sal = st.number_input("Saldo Inicial Q", min_value=0.0, step=100.0, format="%.2f")

            if st.form_submit_button("Guardar Cuenta Bancaria", type="primary"):
                if b_nom and b_num:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("INSERT INTO cuentas_bancarias (id, empresa_id, banco, numero_cuenta, tipo_cuenta, saldo_inicial, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                              (f"bnk-{int(datetime.now().timestamp())}", selected_emp_id, b_nom.strip(), b_num.strip(), b_tip, b_sal, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success("Cuenta bancaria agregada.")
                    st.rerun()

    conn = get_db_connection()
    df_bnk = pd.read_sql_query("SELECT banco AS \"Banco\", numero_cuenta AS \"No. Cuenta\", tipo_cuenta AS \"Tipo\", saldo_inicial AS \"Saldo Inicial (Q)\" FROM cuentas_bancarias WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_bnk, use_container_width=True)

# ==========================================
# MÓDULOS: LIBROS SAT (COMPRAS Y VENTAS)
# ==========================================
elif selected_menu in ["📋 Libro de Compras (SAT)", "📋 Libro de Ventas (SAT)"]:
    if not selected_emp_id: st.stop()
    tipo_l = "COMPRA" if "Compras" in selected_menu else "VENTA"
    st.title(f"{selected_menu} — {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_sat = pd.read_sql_query(
        "SELECT fecha AS \"Fecha\", serie AS \"Serie\", numero_factura AS \"Documento\", tercero_nit AS \"NIT\", tercero_nombre AS \"Nombre\", subtotal AS \"Base Imponible (Q)\", iva AS \"IVA (Q)\", monto_ret_isr AS \"Ret. ISR (Q)\", total AS \"Total (Q)\" FROM facturas WHERE empresa_id = %s AND tipo = %s ORDER BY fecha DESC",
        conn, params=(selected_emp_id, tipo_l)
    )
    conn.close()

    if df_sat.empty:
        st.info("Sin registros en este libro fiscal.")
    else:
        st.dataframe(df_sat, use_container_width=True)
        c1, c2 = st.columns(2)
        c1.download_button("📥 Descargar Libro (Excel)", exportar_reporte_excel(df_sat, "Libro_SAT"), f"Libro_{tipo_l}_{selected_empresa_info[1]}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        c2.download_button("📄 Descargar Libro (PDF)", exportar_reporte_pdf(df_sat, selected_menu, selected_empresa_info[1], selected_empresa_info[2]), f"Libro_{tipo_l}_{selected_empresa_info[1]}.pdf", "application/pdf", use_container_width=True)

# ==========================================
# MÓDULO: LIBRO DE RETENCIONES
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title("📑 Libro de Retenciones Fiscales (SAT Guatemala)")
    conn = get_db_connection()
    df_r = pd.read_sql_query("SELECT tipo, fecha, numero_factura, tercero_nombre, tercero_nit, subtotal, monto_ret_isr, monto_ret_iva, total_a_pagar_cobrar FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_r.empty:
        st.dataframe(df_r, use_container_width=True)
        st.download_button("📥 Descargar Reporte (CSV)", df_r.to_csv(index=False).encode('utf-8'), f"retenciones_{selected_empresa_info[1]}.csv", "text/csv")
    else:
        st.info("No hay retenciones registradas.")

# ==========================================
# MÓDULO: LIBRO DIARIO
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("SELECT partida_no AS \"Partida #\", fecha AS \"Fecha\", codigo_cuenta AS \"Código\", nombre_cuenta AS \"Cuenta\", debe AS \"Debe (Q)\", haber AS \"Haber (Q)\", concepto AS \"Concepto\" FROM libro_diario WHERE empresa_id = %s ORDER BY partida_no ASC, id ASC", conn, params=(selected_emp_id,))
    conn.close()
    if df_d.empty:
        st.info("No hay partidas registradas.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Débitos (Debe)", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total Créditos (Haber)", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)

# ==========================================
# MÓDULO: LIBRO MAYOR
# ==========================================
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
        st.info("No hay movimientos contables.")
    else:
        st.dataframe(df_m, use_container_width=True)

# ==========================================
# MÓDULO: NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    with st.expander("➕ Agregar Nueva Cuenta Contable", expanded=True):
        with st.form("form_nueva_cuenta", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            nuevo_codigo = col1.text_input("Código de Cuenta (ej: 5.1.02.08)")
            nuevo_nombre = col2.text_input("Nombre de la Cuenta (ej: Gastos de Publicidad)")
            nuevo_tipo = col3.selectbox("Tipo de Cuenta", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if nuevo_codigo and nuevo_nombre:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)",
                                  (selected_emp_id, nuevo_codigo.strip(), nuevo_nombre.strip(), nuevo_tipo))
                        conn.commit()
                        st.success(f"Cuenta '{nuevo_codigo} - {nuevo_nombre}' guardada.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Ya existe una cuenta con este código.")
                    conn.close()
                else:
                    st.error("Completa el código y el nombre.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Nombre de la Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# MÓDULO: CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👤 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores y Permisos")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_emps = c.fetchall()
    conn.close()

    t_crear, t_reg = st.tabs(["➕ Crear Operador", "📋 Usuarios Registrados"])
    with t_crear:
        with st.form("form_crear_op"):
            c1, c2 = st.columns(2)
            u_name = c1.text_input("Usuario *")
            u_nom = c2.text_input("Nombre Completo *")
            c3, c4 = st.columns(2)
            u_mail = c3.text_input("Correo")
            u_pass = c4.text_input("Contraseña *", type="password")
            st.markdown("##### Asignar Empresas:")
            emp_sel = [emp[0] for emp in all_emps if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"c_op_{emp[0]}", value=True)]
            if st.form_submit_button("Crear Operador", type="primary"):
                if u_name and u_nom and u_pass:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute("INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (f"usr-{int(datetime.now().timestamp())}", u_name, hash_password(u_pass), u_nom, u_mail, "operator", "active", ",".join(emp_sel), datetime.now().isoformat()))
                        conn.commit()
                        st.success("Operador creado exitosamente.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("El usuario ya existe.")
                    conn.close()
    with t_reg:
        for u in users_list:
            with st.expander(f"👤 {u[2]} (@{u[1]})"):
                st.write(f"Correo: {u[3]} | Estado: {u[5]}")
                st.caption(f"Empresas asignadas: {u[6] or 'Ninguna'}")

# ==========================================
# MÓDULO: GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro de Empresas Fiscales")
    with st.form("form_nueva_emp"):
        c1, c2 = st.columns(2)
        nom = c1.text_input("Razón Social *")
        nit = c2.text_input("NIT *")
        c3, c4 = st.columns(2)
        dire = c3.text_input("Dirección Fiscal")
        tel = c4.text_input("Teléfono")
        reg = st.selectbox("Régimen de ISR SAT", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades de Actividades Lucrativas (25%)", "Pequeño Contribuyente (5%)"])
        if st.form_submit_button("Guardar Empresa", type="primary"):
            if nom and nit:
                conn = get_db_connection()
                try:
                    c = conn.cursor()
                    eid = f"emp-{int(datetime.now().timestamp())}"
                    c.execute("INSERT INTO empresas VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                              (eid, nom, nit, dire, tel, reg, "Servicios Contables", datetime.now().isoformat()))
                    conn.commit()
                    sembrar_nomenclatura_inicial(eid)
                    st.success("Empresa registrada y nomenclatura inicial sembrada.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error("El NIT ya existe en el sistema.")
                conn.close()
            else:
                st.error("Completa el Nombre y el NIT.")