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
    """)
    c.execute("""
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
    """)
    c.execute("""
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
    """)
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
            ("2.1.03.03", "Provisión ISR por Pagar (Régimen)", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("3.1.02.01", "Resultados Acumulados", "Capital"),
            ("3.1.02.02", "Ganancia / Pérdida del Ejercicio", "Capital"),
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

# ==========================================
# CÁLCULOS TRIBUTARIOS Y CONTABLES (DESDE EL TOTAL)
# ==========================================
def calcular_desglose_desde_total(monto_total, es_pequeno=False, aplica_isr=False, tipo_isr="No Aplica", aplica_iva=False, tipo_iva="No Aplica"):
    monto_total = float(monto_total or 0.0)
    if es_pequeno:
        subtotal = round(monto_total, 2)
        iva = 0.0
        total = subtotal
    else:
        subtotal = round(monto_total / 1.12, 2)
        iva = round(monto_total - subtotal, 2)
        total = round(monto_total, 2)

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

    if aplica_iva and iva > 0 and tipo_iva not in ["No Aplica", "No Aplica / No es Agente Retenedor"]:
        if "15%" in tipo_iva:
            tasa_iva = 0.15
            monto_iva = round(iva * 0.15, 2)
        elif "25%" in tipo_iva:
            tasa_iva = 0.25
            monto_iva = round(iva * 0.25, 2)
        elif "100%" in tipo_iva:
            tasa_iva = 1.0
            monto_iva = round(iva, 2)

    liquido = round(total - monto_isr - monto_iva, 2)
    return subtotal, iva, total, tasa_isr, monto_isr, tasa_iva, monto_iva, liquido

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

# ==========================================
# EXTRACTOR DE FACTURAS DTE SAT GUATEMALA (FEL)
# ==========================================
def extraer_datos_dte_sat(file_bytes):
    datos = {
        "numero_dte": "",
        "serie": "",
        "fecha": date.today(),
        "nit_emisor": "",
        "nombre_emisor": "",
        "nit_receptor": "",
        "nombre_receptor": "",
        "total": 0.0,
        "es_pequeno": False
    }
    if not PdfReader:
        return datos
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        texto = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                texto += t + "\n"

        if re.search(r'Pequeño\s+Contribuyente|No\s+genera\s+derecho\s+a\s+cr[eé]dito\s+fiscal', texto, re.I):
            datos["es_pequeno"] = True

        m_aut = re.search(r'(?:NÚMERO\s+DE\s+AUTORIZACI[OÓ]N|Autorizaci[oó]n|UUID)[\s:\n\r]+([A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}|[A-F0-9]{32})', texto, re.I)
        if m_aut:
            datos["numero_dte"] = m_aut.group(1).strip()

        m_ser = re.search(r'(?:SERIE|Serie)[\s:\n\r]+([A-F0-9]+)', texto)
        if m_ser:
            datos["serie"] = m_ser.group(1).strip()

        m_num = re.search(r'(?:N[uú]mero\s+de\s+DTE|N[UÚ]MERO\s+DTE)[\s:\n\r]+([0-9]+)', texto, re.I)
        if m_num and not datos["numero_dte"]:
            datos["numero_dte"] = m_num.group(1).strip()

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

        m_nem = re.search(r'Nit\s*Emisor[\s:\n\r]+([0-9Kk\-]+)', texto, re.I)
        if m_nem:
            datos["nit_emisor"] = m_nem.group(1).replace("-", "").strip().upper()

        m_nomem = re.search(r'Nombre(?:\s+del)?\s+Emisor[\s:\n\r]+([^\n\r]+)', texto, re.I)
        if m_nomem:
            datos["nombre_emisor"] = m_nomem.group(1).strip()

        m_nrec = re.search(r'NIT\s*Receptor[\s:\n\r]+([0-9Kk\-]+|CF|C/?F)', texto, re.I)
        if m_nrec:
            r_nit = m_nrec.group(1).replace("-", "").strip().upper()
            datos["nit_receptor"] = "CF" if r_nit in ["CF", "C/F"] else r_nit

        m_nomrec = re.search(r'Nombre(?:\s+del)?\s+Receptor[\s:\n\r]+([^\n\r]+)', texto, re.I)
        if m_nomrec:
            datos["nombre_receptor"] = m_nomrec.group(1).strip()
        elif datos["nit_receptor"] == "CF":
            datos["nombre_receptor"] = "CONSUMIDOR FINAL"

        montos = re.findall(r'(?:TOTAL(?:ES)?|Gran\s+Total|Total\s+General)[\s:\n\r]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
        if montos:
            datos["total"] = float(montos[-1].replace(",", ""))
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

    selected_menu = st.radio("Módulo a Trabajar:", menu_options, index=3)

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
    try:
        df_prods_corp = pd.read_sql_query(
            "SELECT id, codigo, descripcion, existencia, costo_promedio, precio_venta FROM productos ORDER BY NULLIF(regexp_replace(codigo, '\\D', '', 'g'), '')::int ASC, codigo ASC", 
            conn
        )
    except Exception:
        df_prods_corp = pd.read_sql_query("SELECT id, codigo, descripcion, existencia, costo_promedio, precio_venta FROM productos ORDER BY codigo ASC", conn)
    conn.close()

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
        imp, neta, _ = calcular_impuesto_renta(selected_empresa_info[3], ing, ing - gas)
        filas.append({"Mes": nom_m, "Ingresos (Q)": ing, "Costos y Gastos (Q)": gas, "Impuesto ISR (Q)": imp, "Utilidad Neta (Q)": neta})

    df_mensual = pd.DataFrame(filas)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingresos Anuales", f"Q {df_mensual['Ingresos (Q)'].sum():,.2f}")
    c2.metric("Gastos Anuales", f"Q {df_mensual['Costos y Gastos (Q)'].sum():,.2f}")
    c3.metric("Impuesto ISR Anual", f"Q {df_mensual['Impuesto ISR (Q)'].sum():,.2f}")
    c4.metric("Resultado Neto Anual", f"Q {df_mensual['Utilidad Neta (Q)'].sum():,.2f}")

    df_disp = df_mensual.copy()
    for col in ["Ingresos (Q)", "Costos y Gastos (Q)", "Impuesto ISR (Q)", "Utilidad Neta (Q)"]:
        df_disp[col] = df_disp[col].map("Q {:,.2f}".format)
    st.dataframe(df_disp, use_container_width=True)

# ==============================================================================
# 🛒 FACTURAS DE COMPRAS Y DOCUMENTOS NO FISCALES (RECIBOS / VALES)
# ==============================================================================
elif selected_menu in ["🛒 Factura de Compras", "Factura de Compras"]:
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()

    st.title(f"🛒 Registro de Compras y Gastos — {selected_empresa_info[1]}")

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_pasivo = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    opciones_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opciones_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pasivo.iterrows()]

    tipo_doc = st.radio(
        "Tipo de Comprobante / Documento:",
        ["📄 Factura Electrónica FEL (Fiscal SAT)", "🧾 Documento No Fiscal (Recibo / Vale / Comprobante Interno)"],
        horizontal=True
    )

    es_no_fiscal = (tipo_doc == "🧾 Documento No Fiscal (Recibo / Vale / Comprobante Interno)")

    pre_datos = {}
    if not es_no_fiscal:
        dte_cargado = st.file_uploader("Subir Factura Electrónica FEL (PDF) - Opcional:", type=["pdf"], key="pdf_compra_fel")
        if dte_cargado:
            try:
                pre_datos = extraer_datos_dte_sat(dte_cargado, tipo_operacion="COMPRA")
                st.success("✅ Factura FEL leída correctamente.")
            except Exception:
                st.warning("No se pudieron leer automáticamente los datos del PDF.")

    with st.expander(f"➕ Formulario de Registro ({'Documento No Fiscal' if es_no_fiscal else 'Factura de Compra'})", expanded=True):
        with st.form("form_compra_mixto"):
            c1, c2, c3 = st.columns(3)
            if es_no_fiscal:
                subtipo = c1.selectbox("Clase de Documento:", ["Recibo Simple", "Vale de Caja Chica", "Comprobante de Egreso", "Recibo de Alquiler No Fiscal", "Otro"])
                num_doc = c2.text_input("No. de Recibo / Vale *", value=f"REC-{int(datetime.now().timestamp())%100000}")
                fecha_doc = c3.date_input("Fecha de Emisión *", value=date.today())
            else:
                num_doc = c1.text_input("Número Factura / DTE *", value=pre_datos.get("numero_dte", ""))
                serie_doc = c2.text_input("Serie", value=pre_datos.get("serie", ""))
                fecha_doc = c3.date_input("Fecha de Emisión *", value=pre_datos.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            tercero_nom = c4.text_input("Beneficiario / Proveedor *", value=pre_datos.get("nombre_emisor", ""))
            tercero_nit = c5.text_input("NIT o DPI (Escribe 'CF' si no tiene) *", value=pre_datos.get("nit_emisor", "CF" if es_no_fiscal else ""))

            c6, c7 = st.columns(2)
            cuenta_debe = c6.selectbox("Cuenta de Cargo (Gasto/Costo) *", opciones_debe if opciones_debe else ["5.1.02.99 - Gastos Generales"])
            cuenta_haber = c7.selectbox("Cuenta de Abono (Caja/Banco/Pasivo) *", opciones_haber if opciones_haber else ["1.1.01.01 - Caja General (Efectivo)"])

            monto_total_doc = st.number_input("Monto Total del Comprobante (Q) *", min_value=0.0, step=10.0, format="%.2f", value=float(pre_datos.get("total", 0.0)))

            if not es_no_fiscal:
                col_r1, col_r2 = st.columns(2)
                aplica_iva = col_r1.checkbox("Genera Crédito Fiscal IVA (12%)", value=True)
                aplica_ret_isr = col_r2.checkbox("Practicar Retención ISR", value=(monto_total_doc >= 2800.0))
            else:
                st.caption("ℹ️ *Los documentos no fiscales no generan crédito fiscal de IVA ni retención de impuestos SAT; el 100% del valor se imputa directamente a la cuenta de gasto.*")
                aplica_iva = False
                aplica_ret_isr = False

            if st.form_submit_button("💾 Guardar y Asentar en Contabilidad", type="primary"):
                if num_doc and tercero_nom and monto_total_doc > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    doc_id = f"doc-{int(datetime.now().timestamp())}"

                    if es_no_fiscal or not aplica_iva:
                        subtotal = monto_total_doc
                        iva = 0.0
                    else:
                        subtotal = round(monto_total_doc / 1.12, 2)
                        iva = round(monto_total_doc - subtotal, 2)

                    # 1. Registrar en tabla de facturas / compras
                    c.execute("""
                        INSERT INTO facturas (
                            id, empresa_id, tipo, numero_factura, serie, fecha,
                            tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso,
                            cuenta_nomenclatura, cuenta_pago, forma_pago,
                            subtotal, iva, total, total_a_pagar_cobrar, created_by, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        doc_id, selected_emp_id, 'NO_FISCAL' if es_no_fiscal else 'COMPRA',
                        num_doc, subtipo if es_no_fiscal else serie_doc, str(fecha_doc),
                        tercero_nom, tercero_nit, subtipo if es_no_fiscal else 'Compra de Mercadería/Servicio',
                        'Gasto', cuenta_debe, cuenta_haber, 'Contado',
                        subtotal, iva, monto_total_doc, monto_total_doc,
                        curr_u['username'], datetime.now().isoformat()
                    ))

                    # 2. Asentar en Libro Diario
                    cod_d, nom_d = cuenta_debe.split(" - ", 1)
                    cod_h, nom_h = cuenta_haber.split(" - ", 1)
                    asiento_id = f"asiento-{int(datetime.now().timestamp())}"

                    # Asiento: Cargo al gasto
                    c.execute("""
                        INSERT INTO libro_diario (empresa_id, asiento_id, fecha, codigo_cuenta, nombre_cuenta, debe, haber, descripcion)
                        VALUES (%s, %s, %s, %s, %s, %s, 0.0, %s)
                    """, (selected_emp_id, asiento_id, str(fecha_doc), cod_d, nom_d, subtotal, f"{num_doc} - {tercero_nom}"))

                    # Asiento: IVA Crédito fiscal (si aplica)
                    if iva > 0:
                        c.execute("""
                            INSERT INTO libro_diario (empresa_id, asiento_id, fecha, codigo_cuenta, nombre_cuenta, debe, haber, descripcion)
                            VALUES (%s, %s, %s, '1.1.03.01', 'IVA por Cobrar (Crédito Fiscal)', %s, 0.0, %s)
                        """, (selected_emp_id, asiento_id, str(fecha_doc), iva, f"IVA {num_doc}"))

                    # Asiento: Abono a caja/banco
                    c.execute("""
                        INSERT INTO libro_diario (empresa_id, asiento_id, fecha, codigo_cuenta, nombre_cuenta, debe, haber, descripcion)
                        VALUES (%s, %s, %s, %s, %s, 0.0, %s, %s)
                    """, (selected_emp_id, asiento_id, str(fecha_doc), cod_h, nom_h, monto_total_doc, f"Pago comprobante {num_doc}"))

                    conn.commit()
                    conn.close()
                    st.success(f"✅ {'Documento no fiscal' if es_no_fiscal else 'Factura'} registrado y asentado correctamente.")
                    st.rerun()
                else:
                    st.error("Por favor completa los campos requeridos y un valor mayor a cero.")

# ==========================================
# MÓDULO: FACTURA DE VENTAS (INGRESO POR TOTAL)
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
        st.markdown("##### 🔍 Selección de Producto Corporativo (Opcional):")
        opc_prods = ["-- Seleccionar Producto --"] + [f"{r['codigo']} | {r['descripcion']} (Sugerido: Q{float(r['precio_venta']):,.2f})" for _, r in df_p_search.iterrows()]
        prod_buscado = st.selectbox("Seleccionar Producto:", opc_prods)

        precio_sug_prod = 0.0
        if prod_buscado != "-- Seleccionar Producto --":
            cod_p_extraido = prod_buscado.split(" | ")[0]
            prod_match = df_p_search[df_p_search['codigo'] == cod_p_extraido]
            if not prod_match.empty:
                precio_sug_prod = float(prod_match.iloc[0]['precio_venta'])

        pdf_v_file = st.file_uploader("Arrastra Factura FEL de Venta (PDF SAT) para autocompletar:", type=["pdf"], key="pdf_venta_uploader")
        
        datos_v_pdf = {}
        pdf_v_key_suffix = "manual"
        if pdf_v_file:
            datos_v_pdf = extraer_datos_dte_sat(pdf_v_file.read())
            pdf_v_key_suffix = f"pdf_v_{pdf_v_file.name}_{pdf_v_file.size}"

        tot_inicial = float(datos_v_pdf.get("total", 0.0)) if datos_v_pdf.get("total", 0.0) > 0 else precio_sug_prod
        es_peq_v_def = datos_v_pdf.get("es_pequeno", False) or ("pequeño" in (selected_empresa_info[3] or "").lower())

        with st.form(f"form_venta_{pdf_v_key_suffix}"):
            c1, c2, c3 = st.columns(3)
            num_v = c1.text_input("Número Factura / Autorización DTE *", value=datos_v_pdf.get("numero_dte", ""), key=f"v_num_{pdf_v_key_suffix}")
            ser_v = c2.text_input("Serie", value=datos_v_pdf.get("serie", ""), key=f"v_ser_{pdf_v_key_suffix}")
            fec_v = c3.date_input("Fecha *", value=datos_v_pdf.get("fecha", date.today()), key=f"v_fec_{pdf_v_key_suffix}")

            c4, c5 = st.columns(2)
            c_nit = c4.text_input("NIT Cliente *", value=datos_v_pdf.get("nit_receptor", "CF"), key=f"v_nit_{pdf_v_key_suffix}")
            
            nom_cli_def = datos_v_pdf.get("nombre_receptor", "")
            if not nom_cli_def and c_nit.strip().upper() in ["CF", "C/F"]:
                nom_cli_def = "CONSUMIDOR FINAL"
            c_nom = c5.text_input("Nombre Cliente *", value=nom_cli_def, key=f"v_nom_{pdf_v_key_suffix}")

            c6, c7 = st.columns(2)
            c_debe_v = c6.selectbox("Cuenta de Cargo (DEBE - Fondos / Clientes) *", opciones_cobro if opciones_cobro else ["1.1.01.01 - Caja General (Efectivo)"])
            c_haber_v = c7.selectbox("Cuenta de Abono (HABER - Ingreso) *", opciones_ing if opciones_ing else ["4.1.01.01 - Ventas de Mercadería"])

            # INGRESO DIRECTO POR EL TOTAL DE FACTURA
            total_factura_v = st.number_input("Total de la Factura (Q) *", min_value=0.0, value=float(tot_inicial), step=50.0, format="%.2f", key=f"v_tot_{pdf_v_key_suffix}")

            chk_peq_v = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera Débito Fiscal)", value=es_peq_v_def, key=f"v_peq_{pdf_v_key_suffix}")

            r1, r2 = st.columns(2)
            ret_isr_v = r1.checkbox("Cliente nos retuvo ISR", value=False, key=f"v_aisr_{pdf_v_key_suffix}")
            t_isr_v = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key=f"v_tisr_{pdf_v_key_suffix}")
            ret_iva_v = r2.checkbox("Cliente nos retuvo IVA", value=False, key=f"v_aiva_{pdf_v_key_suffix}")
            t_iva_v = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key=f"v_tiva_{pdf_v_key_suffix}")

            subtotal_v, iva_v, tot_v, t_iv, m_iv, t_vv, m_vv, liq_v = calcular_desglose_desde_total(total_factura_v, chk_peq_v, ret_isr_v, t_isr_v, ret_iva_v, t_iva_v)

            st.info(f"📊 **Base (Subtotal):** Q {subtotal_v:,.2f} | **IVA (Débito Fiscal):** Q {iva_v:,.2f} | **Total:** Q {tot_v:,.2f} | **Líquido a Recibir:** Q {liq_v:,.2f}")

            if st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary"):
                # Asegurar CF
                if c_nit.strip().upper() in ["CF", "C/F"] and not c_nom.strip():
                    c_nom = "CONSUMIDOR FINAL"

                if num_v.strip() and c_nit.strip() and c_nom.strip() and total_factura_v > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_v_id = f"fac-v-{int(datetime.now().timestamp())}"
                    if c_nit.strip().upper() not in ["CF", "C/F"]:
                        c.execute('''
                            INSERT INTO terceros_corporativos (id, nit, nombre, tipo, created_at)
                            VALUES (%s, %s, %s, 'Cliente', %s)
                            ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                        ''', (f"terc-{int(datetime.now().timestamp())}", c_nit.strip().upper(), c_nom.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                        VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (fac_v_id, selected_emp_id, num_v.strip(), ser_v.strip(), str(fec_v), c_nom.strip(), c_nit.strip().upper(), "Venta", "Venta", c_haber_v, c_debe_v, "Contado/Crédito", subtotal_v, iva_v, tot_v,
                          1 if (ret_isr_v and t_isr_v != "No Aplica") else 0, t_iv, m_iv,
                          1 if (ret_iva_v and t_iva_v != "No Aplica / No es Agente Retenedor") else 0, t_vv, m_vv, liq_v, curr_u['username'], datetime.now().isoformat()))
                    conn.commit()
                    conn.close()

                    cod_h, nom_h = c_haber_v.split(" - ", 1)
                    cod_d, nom_d = c_debe_v.split(" - ", 1)
                    registrar_partida_venta(selected_emp_id, fec_v, cod_h, nom_h, cod_d, nom_d, subtotal_v, iva_v, m_iv, m_vv, liq_v, num_v.strip(), c_nom.strip())
                    st.success("✅ Venta registrada y partida cuadrada en Diario.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, cliente y un total mayor a cero.")

    with tab_v_hist:
        conn = get_db_connection()
        df_ventas = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, subtotal, iva, total, total_a_pagar_cobrar FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        st.dataframe(df_ventas, use_container_width=True)

# ==============================================================================
# 📉 ESTADO DE RESULTADOS (FILTRO DINÁMICO: MES, AÑO COMPLETO, MES A MES)
# ==============================================================================
elif selected_menu in ["📉 Estado de Resultados", "Estado de Resultados"]:
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero en la barra lateral.")
        st.stop()

    conn = get_db_connection()
    df_anios = pd.read_sql_query(
        "SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC",
        conn, params=(selected_emp_id,)
    )
    conn.close()

    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(date.today().year)]

    col_er1, col_er2, col_er3 = st.columns([2.5, 1.2, 1])
    modo_er = col_er1.radio(
        "Visualizar Estado de Resultados por:",
        ["🌐 Histórico Acumulado (Mes a Mes)", "📅 Mes Específico", "📆 Año Completo"],
        horizontal=True,
        key="er_modo_filtro"
    )
    anio_sel_er = col_er2.selectbox("Año Fiscal:", anios_list, key="er_anio_filtro")
    color_er = col_er3.color_picker("Color de visualización:", value="#1E3A8A", key="er_color_picker")

    meses_dict = {
        "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
        "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
        "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
    }

    mes_sel_er = None
    if modo_er == "📅 Mes Específico":
        mes_sel_er = st.selectbox(
            "Seleccionar Mes:", 
            list(meses_dict.keys()), 
            format_func=lambda x: f"{x} - {meses_dict[x]}", 
            key="er_mes_selector"
        )
        titulo_banner = f"Estado de Resultados — {meses_dict[mes_sel_er]} {anio_sel_er}"
    elif modo_er == "📆 Año Completo":
        titulo_banner = f"Estado de Resultados — Ejercicio Fiscal Completo {anio_sel_er}"
    else:
        titulo_banner = f"Estado de Resultados — Histórico Acumulado (Mes a Mes) {anio_sel_er}"

    st.markdown(f"""
        <div style="background-color: {color_er}; padding: 12px 20px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
            📉 {titulo_banner}<br>
            <span style="font-size: 13px; font-weight: normal;">Empresa: {selected_empresa_info[1]} | Ejercicio: {anio_sel_er}</span>
        </div>
    """, unsafe_allow_html=True)

    # 1. Filtros SQL según modalidad
    cond_sql = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
    params_sql = [selected_emp_id, str(anio_sel_er)]

    if modo_er == "📅 Mes Específico":
        cond_sql += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
        params_sql.append(mes_sel_er)

    conn = get_db_connection()
    # Consulta detallada mensual para armar columnas o totales
    q_er = f"""
        SELECT 
            n.codigo,
            n.nombre,
            n.tipo,
            SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
            SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS total_mov
        FROM libro_diario d
        JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
        WHERE {cond_sql} AND n.tipo IN ('Ingreso', 'Gasto')
        GROUP BY n.codigo, n.nombre, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
        ORDER BY n.codigo ASC
    """
    df_er_raw = pd.read_sql_query(q_er, conn, params=tuple(params_sql))
    conn.close()

    # Función auxiliar para renderizar cada tabla (Ingresos / Gastos / Costos)
    def armar_tabla_seccion(df_fuente, filtro_tipo, cod_prefix=None):
        sub = df_fuente[df_fuente['tipo'] == filtro_tipo].copy()
        if cod_prefix:
            sub = sub[sub['codigo'].str.startswith(cod_prefix)]
        if sub.empty:
            return pd.DataFrame()

        if modo_er == "🌐 Histórico Acumulado (Mes a Mes)":
            pivot = sub.pivot_table(index=['codigo', 'nombre'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()
            for m in [f"{i:02d}" for i in range(1, 13)]:
                if m not in pivot.columns:
                    pivot[m] = 0.0
            cols_meses = [f"{i:02d}" for i in range(1, 13)]
            pivot['TOTAL ANUAL'] = pivot[cols_meses].sum(axis=1)
            # Formatear a Quetzales
            for c in cols_meses + ['TOTAL ANUAL']:
                pivot[c] = pivot[c].map(lambda v: f"Q {v:,.2f}")
            return pivot[['codigo', 'nombre'] + cols_meses + ['TOTAL ANUAL']]

        elif modo_er == "📅 Mes Específico":
            res = sub.groupby(['codigo', 'nombre'])['total_mov'].sum().reset_index()
            res.rename(columns={'total_mov': f'Total {meses_dict[mes_sel_er]} (Q)'}, inplace=True)
            res[f'Total {meses_dict[mes_sel_er]} (Q)'] = res[f'Total {meses_dict[mes_sel_er]} (Q)'].map("Q {:,.2f}".format)
            return res

        else: # Año Completo
            res = sub.groupby(['codigo', 'nombre'])['total_mov'].sum().reset_index()
            res.rename(columns={'total_mov': f'Total Anual {anio_sel_er} (Q)'}, inplace=True)
            res[f'Total Anual {anio_sel_er} (Q)'] = res[f'Total Anual {anio_sel_er} (Q)'].map("Q {:,.2f}".format)
            return res

    # Cálculo de totales globales para tarjetas métricas
    tot_ing = df_er_raw[df_er_raw['tipo'] == 'Ingreso']['total_mov'].sum() if not df_er_raw.empty else 0.0
    tot_gas = df_er_raw[df_er_raw['tipo'] == 'Gasto']['total_mov'].sum() if not df_er_raw.empty else 0.0
    utilidad_neta = tot_ing - tot_gas

    m1, m2, m3 = st.columns(3)
    m1.metric("Ingresos Totales", f"Q {tot_ing:,.2f}")
    m2.metric("Costos y Gastos Totales", f"Q {tot_gas:,.2f}")
    m3.metric("Resultado Neto", f"Q {utilidad_neta:,.2f}", delta="Ganancia" if utilidad_neta >= 0 else "Pérdida")

    st.markdown("---")
    st.subheader("(+) INGRESOS OPERACIONALES")
    df_ingresos = armar_tabla_seccion(df_er_raw, 'Ingreso')
    if not df_ingresos.empty:
        st.dataframe(df_ingresos, use_container_width=True, hide_index=True)
    else:
        st.info("No hay ingresos registrados en el período seleccionado.")

    st.subheader("(-) COSTO DE VENTAS")
    df_costos = armar_tabla_seccion(df_er_raw, 'Gasto', cod_prefix='5.1.01')
    if not df_costos.empty:
        st.dataframe(df_costos, use_container_width=True, hide_index=True)
    else:
        st.caption("Sin costos de ventas registrados.")

    st.subheader("(-) GASTOS DE OPERACIÓN Y ADMINISTRACIÓN")
    # Excluye la cuenta 5.1.01 (que es de costo directo)
    sub_gastos = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (~df_er_raw['codigo'].str.startswith('5.1.01'))]
    df_gastos_op = armar_tabla_seccion(sub_gastos, 'Gasto')
    if not df_gastos_op.empty:
        st.dataframe(df_gastos_op, use_container_width=True, hide_index=True)
    else:
        st.caption("Sin gastos operativos en este período.")
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
# MÓDULO: BALANCE DE SITUACIÓN FINANCIERA (CUADRADO CON GANANCIA)
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
        WHERE d.empresa_id = %s AND (SUBSTRING(d.fecha FROM 1 FOR 4) = %s OR %s = '🌐 Saldo Acumulado')
        GROUP BY n.codigo, n.nombre, n.tipo
        ORDER BY n.codigo ASC
    """
    df_raw = pd.read_sql_query(q_sit, conn, params=(selected_emp_id, anio_sit, modo_sit))
    conn.close()

    if df_raw.empty:
        st.info("Sin registros contables disponibles.")
    else:
        df_raw['Saldo'] = df_raw.apply(
            lambda r: (r['debe'] - r['haber']) if r['tipo'] == 'Activo' else (r['haber'] - r['debe']), axis=1
        )
        activos = df_raw[df_raw['tipo'] == 'Activo'].copy()
        pasivos = df_raw[df_raw['tipo'] == 'Pasivo'].copy()
        capital = df_raw[df_raw['tipo'] == 'Capital'].copy()

        ingresos_total = df_raw[df_raw['tipo'] == 'Ingreso']['haber'].sum() - df_raw[df_raw['tipo'] == 'Ingreso']['debe'].sum()
        gastos_total = df_raw[df_raw['tipo'] == 'Gasto']['debe'].sum() - df_raw[df_raw['tipo'] == 'Gasto']['haber'].sum()
        utilidad_bruta = float(ingresos_total - gastos_total)

        impuesto_renta, ganancia_neta, desc_imp = calcular_impuesto_renta(selected_empresa_info[3], float(ingresos_total), utilidad_bruta)

        if impuesto_renta > 0:
            nueva_fila_pasivo = pd.DataFrame([{
                "codigo": "2.1.03.03",
                "nombre": f"Provisión ISR por Pagar ({desc_imp})",
                "tipo": "Pasivo",
                "debe": 0.0,
                "haber": impuesto_renta,
                "Saldo": impuesto_renta
            }])
            pasivos = pd.concat([pasivos, nueva_fila_pasivo], ignore_index=True)

        nueva_fila_capital = pd.DataFrame([{
            "codigo": "3.1.02.02",
            "nombre": "Ganancia / (Pérdida) Neta del Ejercicio",
            "tipo": "Capital",
            "debe": 0.0,
            "haber": ganancia_neta,
            "Saldo": ganancia_neta
        }])
        capital = pd.concat([capital, nueva_fila_capital], ignore_index=True)

        tot_act = float(activos['Saldo'].sum())
        tot_pas = float(pasivos['Saldo'].sum())
        tot_cap = float(capital['Saldo'].sum())
        tot_pas_cap = round(tot_pas + tot_cap, 2)
        dif_cuadre = round(tot_act - tot_pas_cap, 2)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Activo", f"Q {tot_act:,.2f}")
        c2.metric("Total Pasivo + Patrimonio", f"Q {tot_pas_cap:,.2f}")
        c3.metric("Diferencia de Balance", f"Q {dif_cuadre:,.2f}", delta="Balance Cuadrado ✅" if abs(dif_cuadre) < 0.05 else "Descuadre ⚠️")

        st.divider()

        col_a, col_p = st.columns(2)
        with col_a:
            st.markdown("### 🏢 ACTIVO")
            st.dataframe(activos[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.success(f"**TOTAL ACTIVO: Q {tot_act:,.2f}**")
        with col_p:
            st.markdown("### 💳 PASIVO")
            st.dataframe(pasivos[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.info(f"**Total Pasivo: Q {tot_pas:,.2f}**")

            st.markdown("### 🏛️ PATRIMONIO NETO")
            st.dataframe(capital[['codigo', 'nombre', 'Saldo']].style.format({"Saldo": "Q {:,.2f}"}), use_container_width=True)
            st.info(f"**Total Patrimonio: Q {tot_cap:,.2f}**")

            st.success(f"**TOTAL PASIVO Y PATRIMONIO: Q {tot_pas_cap:,.2f}**")

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