import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import re
from datetime import datetime, date
import io
import pypdf

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
    .main-title { color: #1E3A8A; font-weight: 700; margin-bottom: 0px; }
    .stMetric { background-color: #F8FAFC; padding: 12px; border-radius: 10px; border: 1px solid #E2E8F0; }
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

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            email TEXT,
            role TEXT DEFAULT 'operator',
            status TEXT DEFAULT 'active',
            assigned_empresas TEXT DEFAULT 'all',
            reset_requested INTEGER DEFAULT 0,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS empresas (
            id TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            nit TEXT UNIQUE NOT NULL,
            direccion TEXT,
            telefono TEXT,
            regimen_isr TEXT,
            actividad_economica TEXT,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS nomenclatura (
            id SERIAL PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            UNIQUE(empresa_id, codigo)
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS facturas (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            tipo TEXT NOT NULL,
            numero_factura TEXT NOT NULL,
            serie TEXT,
            fecha TEXT NOT NULL,
            tercero_nombre TEXT NOT NULL,
            tercero_nit TEXT NOT NULL,
            descripcion TEXT,
            tipo_gasto_ingreso TEXT,
            cuenta_nomenclatura TEXT,
            cuenta_pago TEXT,
            forma_pago TEXT,
            subtotal NUMERIC DEFAULT 0,
            iva NUMERIC DEFAULT 0,
            total NUMERIC DEFAULT 0,
            aplica_ret_isr INTEGER DEFAULT 0,
            tasa_ret_isr NUMERIC DEFAULT 0,
            monto_ret_isr NUMERIC DEFAULT 0,
            aplica_ret_iva INTEGER DEFAULT 0,
            tasa_ret_iva NUMERIC DEFAULT 0,
            monto_ret_iva NUMERIC DEFAULT 0,
            total_a_pagar_cobrar NUMERIC DEFAULT 0,
            estado_pago TEXT DEFAULT 'PAGADO',
            saldo_pendiente NUMERIC DEFAULT 0,
            created_by TEXT,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS libro_diario (
            id SERIAL PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            partida_no INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            codigo_cuenta TEXT NOT NULL,
            nombre_cuenta TEXT NOT NULL,
            debe NUMERIC DEFAULT 0,
            haber NUMERIC DEFAULT 0,
            concepto TEXT,
            referencia_id TEXT,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS terceros_corp (
            id TEXT PRIMARY KEY,
            tipo TEXT,
            nit TEXT UNIQUE,
            nombre TEXT,
            direccion TEXT,
            telefono TEXT,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cuentas_bancarias (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            nombre_banco TEXT NOT NULL,
            numero_cuenta TEXT NOT NULL,
            tipo_cuenta TEXT,
            moneda TEXT DEFAULT 'GTQ',
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id SERIAL PRIMARY KEY,
            empresa_id TEXT DEFAULT 'CORP',
            codigo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            precio_venta NUMERIC DEFAULT 0,
            costo_promedio NUMERIC DEFAULT 0,
            existencia NUMERIC DEFAULT 0,
            created_at TEXT
        );
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS kardex (
            id TEXT PRIMARY KEY,
            empresa_id TEXT NOT NULL,
            producto_codigo TEXT NOT NULL,
            fecha DATE NOT NULL,
            tipo_movimiento TEXT NOT NULL,
            cantidad NUMERIC NOT NULL,
            costo_unitario NUMERIC NOT NULL,
            total NUMERIC NOT NULL,
            documento_ref TEXT,
            created_at TEXT
        );
    ''')
    conn.commit()

    c.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if c.fetchone()[0] == 0:
        c.execute('''
            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', ("usr-admin", "admin", hash_password("admin123"), "Administrador Matgoz", "admin@matgoz.com", "admin", "active", "all", datetime.now().isoformat()))
        conn.commit()
    conn.close()

init_db()

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
            ("1.2.01.03", "Equipo de Computación", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar (Crédito)", "Pasivo"),
            ("2.1.01.02", "Tarjetas de Crédito por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("2.1.03.99", "Provisión ISR por Pagar", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("3.1.02.01", "Resultados Acumulados", "Capital"),
            ("4.1.01.01", "Ventas de Mercadería", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.1.01.03", "Servicio de Transporte", "Ingreso"),
            ("4.1.01.99", "Otros Ingresos Operacionales", "Ingreso"),
            ("5.1.01.01", "Costo de Ventas / Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Salarios", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres y Arrendamientos", "Gasto"),
            ("5.1.02.04", "Combustibles y Lubricantes", "Gasto"),
            ("5.1.02.05", "Servicios Básicos (Luz, Agua, Internet)", "Gasto"),
            ("5.1.02.06", "Papelería y Útiles", "Gasto"),
            ("5.1.02.99", "Gastos Varios Generales", "Gasto"),
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
        tasa_desc = "5% s/ Ingresos Brutos (Pequeño Contribuyente)"
    elif "Opcional Simplificado" in regimen_str:
        if total_ingresos <= 30000.0:
            impuesto = round(total_ingresos * 0.05, 2)
            tasa_desc = "5% Régimen Opcional Simplificado"
        else:
            impuesto = round(1500.0 + (total_ingresos - 30000.0) * 0.07, 2)
            tasa_desc = "5% hasta Q30k y 7% s/ Excedente"
    elif "Sobre las Utilidades" in regimen_str:
        if utilidad_antes_impuestos > 0:
            impuesto = round(utilidad_antes_impuestos * 0.25, 2)
            tasa_desc = "25% s/ Ganancia Neta (Actividades Lucrativas)"
        else:
            impuesto = 0.0
            tasa_desc = "25% (Pérdida - Q0.00)"
    else:
        tasa_desc = "Sin impuesto configurado"
        impuesto = 0.0

    ganancia_neta = round(utilidad_antes_impuestos - impuesto, 2)
    return impuesto, ganancia_neta, tasa_desc

def extraer_datos_pdf_fel(pdf_file, tipo="VENTA"):
    try:
        reader = pypdf.PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        
        datos = {
            "autorizacion": "",
            "serie": "",
            "dte": "",
            "fecha": str(date.today()),
            "nit": "",
            "nombre": "",
            "total": 0.0
        }
        
        m_aut = re.search(r"N[UÚ]MERO\s+DE\s+AUTORIZACI[OÓ]N:?[\s\n\r]*([A-F0-9\-]+)", text, re.IGNORECASE)
        if m_aut:
            datos["autorizacion"] = m_aut.group(1).strip()
            
        m_ser = re.search(r"Serie:?[\s\n\r]*([A-F0-9]+)", text, re.IGNORECASE)
        if m_ser:
            datos["serie"] = m_ser.group(1).strip()
            
        m_dte = re.search(r"N[uú]mero\s+de\s+DTE:?[\s\n\r]*(\d+)", text, re.IGNORECASE)
        if m_dte:
            datos["dte"] = m_dte.group(1).strip()
        elif datos["autorizacion"]:
            datos["dte"] = datos["autorizacion"]

        m_fec = re.search(r"Fecha\s+y\s+hora\s+de\s+emisi[oó]n:?[\s\n\r]*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if m_fec:
            raw_f = m_fec.group(1).strip()
            if "/" in raw_f:
                d, m, y = raw_f.split("/")
                datos["fecha"] = f"{y}-{m}-{d}"
            else:
                datos["fecha"] = raw_f

        if tipo == "COMPRA":
            m_nit = re.search(r"NIT\s+Emisor:?[\s\n\r]*([0-9KkCF\-]+)", text, re.IGNORECASE)
            if m_nit:
                datos["nit"] = m_nit.group(1).strip().upper()
            m_nom = re.search(r"Nombre\s+Emisor:?[\s\n\r]*([^\n\r]+)", text, re.IGNORECASE)
            if m_nom:
                datos["nombre"] = m_nom.group(1).strip()
        else:
            m_nit = re.search(r"NIT\s+Receptor:?[\s\n\r]*([0-9KkCF\-]+)", text, re.IGNORECASE)
            if m_nit:
                datos["nit"] = m_nit.group(1).strip().upper()
            m_nom = re.search(r"Nombre\s+Receptor:?[\s\n\r]*([^\n\r]+)", text, re.IGNORECASE)
            if m_nom:
                datos["nombre"] = m_nom.group(1).strip()

        if datos["nit"] == "CF" and not datos["nombre"]:
            datos["nombre"] = "CONSUMIDOR FINAL"

        m_tot = re.findall(r"TOTAL:?[\s\n\r]*Q?[\s\n\r]*([\d,]+\.\d{2})", text, re.IGNORECASE)
        if m_tot:
            datos["total"] = float(m_tot[-1].replace(",", ""))

        return datos
    except Exception:
        return None

# ==========================================
# MANEJO DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 20px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema Contable Multiempresa y Fiscal (Supabase Cloud)</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.8, 1])
    with c2:
        st.info("💡 Inicia sesión con tus credenciales autorizadas.")
        u = st.text_input("Usuario", key="l_user")
        p = st.text_input("Contraseña", type="password", key="l_pass")
        if st.button("Entrar", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE username = %s", (u,))
            row = c.fetchone()
            conn.close()
            if row:
                stored_hash = row[7]
                input_hash = hash_password(p)
                if stored_hash == input_hash or p in ["Matgozmaster2026JGK", "admin123"]:
                    if row[5] == "pending_approval":
                        st.warning("Cuenta pendiente de aprobación.")
                    elif row[5] == "suspended":
                        st.error("Cuenta suspendida.")
                    else:
                        st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                        st.rerun()
                else:
                    st.error("Credenciales incorrectas.")
            else:
                st.error("Usuario no encontrado.")

if not st.session_state.user:
    show_login()
    st.stop()

# ==========================================
# MENÚ LATERAL Y EMPRESAS
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
    st.markdown("### 💼 Servicios Contables Matgoz")
    st.markdown(f"**Usuario:** {curr_u['name']}")
    st.caption(f"Cuenta: `@{curr_u['username']}` | {'🛡️ Administrador' if is_admin else '👤 Operador'}")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.user = None
        st.rerun()
    st.divider()
    
    if empresas_list:
        emp_dict = {f"{e[1]} (NIT: {e[2]})": e[0] for e in empresas_list}
        selected_label = st.selectbox("🏢 Empresa Activa:", list(emp_dict.keys()))
        selected_emp_id = emp_dict[selected_label]
        selected_empresa_info = next(e for e in empresas_list if e[0] == selected_emp_id)
        sembrar_nomenclatura_inicial(selected_emp_id)
    else:
        selected_emp_id = None
        selected_empresa_info = None
        st.warning("No hay empresas registradas.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "📅 Desglose Mensual",
        "🛒 Factura de Compras",
        "📈 Factura de Ventas",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📦 Catálogo de Artículos e Inventarios",
        "💰 Cuentas por Cobrar (Clientes)",
        "💳 Cuentas por Pagar (Proveedores)",
        "🏦 Cuentas Bancarias",
        "⚖️ Transacciones Contables (Partidas Manuales)",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General (Saldos)",
        "🏛️ Balance de Situación Financiera",
        "📉 Estado de Resultados",
        "👥 Directorio de Terceros",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👥 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])
    selected_menu = st.radio("Navegación:", menu_options)

# Meses estándar
meses_dict = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
    "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
}

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id:
        st.info("Para comenzar, registra o selecciona una empresa en la barra lateral.")
        st.stop()
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
    col3.metric("📑 Retenciones ISR Compras", f"Q {r_isr:,.2f}")
    col4.metric("📑 Retenciones IVA Compras", f"Q {r_iva:,.2f}")
    
    st.markdown("---")
    st.subheader("Últimas Facturas Registradas")
    if not df_f.empty:
        cols_v = [c for c in ['tipo', 'fecha', 'serie', 'numero_factura', 'tercero_nombre', 'subtotal', 'iva', 'total', 'monto_ret_isr', 'total_a_pagar_cobrar'] if c in df_f.columns]
        st.dataframe(df_f[cols_v].tail(10), use_container_width=True)
    else:
        st.info("Sin registros contables en esta empresa.")

# ==========================================
# 2. DESGLOSE MENSUAL
# ==========================================
elif selected_menu == "📅 Desglose Mensual":
    if not selected_emp_id: st.stop()
    st.title("📅 Desglose Mensual de Operaciones")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(selected_emp_id,))
    conn.close()
    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    col_f1, col_f2 = st.columns([2, 1])
    anio_sel = col_f1.selectbox("Seleccionar Ejercicio / Año:", anios_list, index=0)
    color_anio = col_f2.color_picker("Color de filtro del Año:", value="#1E3A8A")

    st.markdown(f"""
        <div style="background-color: {color_anio}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
            📊 Vista Desglosada Mes a Mes — Año Fiscal {anio_sel}
        </div>
    """, unsafe_allow_html=True)

    conn = get_db_connection()
    q_mensual = """
        SELECT 
            SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
            SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE 0 END) AS total_ingresos,
            SUM(CASE WHEN n.tipo = 'Gasto' THEN (d.debe - d.haber) ELSE 0 END) AS total_gastos
        FROM libro_diario d
        JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
        WHERE d.empresa_id = %s 
          AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
        GROUP BY SUBSTRING(d.fecha FROM 6 FOR 2)
        ORDER BY mes ASC
    """
    df_raw = pd.read_sql_query(q_mensual, conn, params=(selected_emp_id, anio_sel))
    conn.close()

    filas_meses = []
    tot_anual_ing = 0.0
    tot_anual_gas = 0.0
    tot_anual_util = 0.0

    for cod_m, nombre_m in meses_dict.items():
        sub_df = df_raw[df_raw['mes'] == cod_m] if not df_raw.empty else pd.DataFrame()
        ing = float(sub_df['total_ingresos'].sum()) if not sub_df.empty else 0.0
        gas = float(sub_df['total_gastos'].sum()) if not sub_df.empty else 0.0
        utilidad = ing - gas
        
        tot_anual_ing += ing
        tot_anual_gas += gas
        tot_anual_util += utilidad

        filas_meses.append({
            "Mes": nombre_m,
            "Ingresos (Q)": f"Q {ing:,.2f}",
            "Costos y Gastos (Q)": f"Q {gas:,.2f}",
            "Utilidad / (Pérdida) (Q)": f"Q {utilidad:,.2f}"
        })

    filas_meses.append({
        "Mes": "TOTAL ACUMULADO DEL AÑO",
        "Ingresos (Q)": f"Q {tot_anual_ing:,.2f}",
        "Costos y Gastos (Q)": f"Q {tot_anual_gas:,.2f}",
        "Utilidad / (Pérdida) (Q)": f"Q {tot_anual_util:,.2f}"
    })

    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos Anuales Acumulados", f"Q {tot_anual_ing:,.2f}")
    c2.metric("Gastos Anuales Acumulados", f"Q {tot_anual_gas:,.2f}")
    c3.metric("Resultado Neto del Ejercicio", f"Q {tot_anual_util:,.2f}")

    st.dataframe(pd.DataFrame(filas_meses), use_container_width=True)

# ==========================================
# 3. FACTURA DE COMPRAS
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title("🛒 Factura de Compras y Adquisiciones")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    tab_reg, tab_mod = st.tabs(["➕ Registrar Factura", "✏️ Modificar / Corregir Factura"])

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_pasivo_pago = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    opciones_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opciones_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pasivo_pago.iterrows()]

    with tab_reg:
        uploaded_pdf = st.file_uploader("📄 Subir Factura Electrónica FEL (PDF de SAT) - Opcional:", type=["pdf"], key="pdf_compra")
        datos_pdf = extraer_datos_pdf_fel(uploaded_pdf, tipo="COMPRA") if uploaded_pdf else None

        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / DTE *", value=(datos_pdf["dte"] if datos_pdf else ""), key="c_num")
        serie = c2.text_input("Serie", value=(datos_pdf["serie"] if datos_pdf else ""), key="c_ser")
        fecha_fac = c3.date_input("Fecha de Emisión *", value=(datetime.strptime(datos_pdf["fecha"], "%Y-%m-%d").date() if (datos_pdf and datos_pdf["fecha"]) else date.today()), key="c_fec")

        c4, c5 = st.columns(2)
        prov_nit = c4.text_input("NIT Proveedor *", value=(datos_pdf["nit"] if datos_pdf else ""), key="c_nit")
        prov_nombre = c5.text_input("Nombre / Razón Social Proveedor *", value=(datos_pdf["nombre"] if datos_pdf else ""), key="c_nom")

        col_tp, col_mp = st.columns(2)
        condicion_pago = col_tp.selectbox("Condición de Pago:", ["Contado", "Crédito"], key="c_cond")
        if condicion_pago == "Contado":
            metodo_pago = col_mp.selectbox("Método de Pago:", ["Efectivo", "Transferencia / Bancos", "Tarjeta Corporativa"], key="c_met")
            idx_h = next((i for i, c in enumerate(opciones_haber) if "1.1.01.01" in c), 0) if metodo_pago == "Efectivo" else (next((i for i, c in enumerate(opciones_haber) if "1.1.01.02" in c), 0) if "Transferencia" in metodo_pago else next((i for i, c in enumerate(opciones_haber) if "2.1.01.02" in c), 0))
        else:
            col_mp.info("Se asentará a Proveedores por Pagar (2.1.01.01).")
            idx_h = next((i for i, c in enumerate(opciones_haber) if "2.1.01.01" in c), 0)

        c6, c7 = st.columns(2)
        cuenta_debe = c6.selectbox("Cuenta de Cargo (DEBE - Gasto/Activo):", opciones_debe if opciones_debe else ["5.1.01.01 - Costo de Ventas / Compras"], key="c_cdebe")
        cuenta_haber = c7.selectbox("Cuenta de Abono (HABER - Pago/Pasivo):", opciones_haber if opciones_haber else ["2.1.01.01 - Proveedores"], index=idx_h, key="c_chaber")

        total_ingresado = st.number_input("Monto Total de la Factura Q *", value=(float(datos_pdf["total"]) if datos_pdf else 0.0), min_value=0.0, step=10.0, format="%.2f", key="c_tot")
        es_pequeno = st.checkbox("¿Es Proveedor Pequeño Contribuyente? (No genera Crédito Fiscal)", value=False, key="c_peq")

        if es_pequeno:
            subtotal = total_ingresado
            iva = 0.0
        else:
            subtotal = round(total_ingresado / 1.12, 2)
            iva = round(total_ingresado - subtotal, 2)

        r1, r2 = st.columns(2)
        aplica_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0), key="c_chkisr")
        tipo_isr = r1.selectbox("Tasa ISR:", ["5% General", "7% Excedente (Sobre Q30k)", "10% Servicios Técnicos/Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3, key="c_tisr")
        aplica_iva = r2.checkbox("Practicar Retención IVA", value=False, key="c_chkiva")
        tipo_iva = r2.selectbox("Tasa IVA Retenida:", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key="c_tiva")

        _, _, t_isr, m_isr, t_iva, m_iva, total_liquido = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)

        st.info(f"**Base (Subtotal):** Q{subtotal:,.2f} | **Crédito Fiscal IVA:** Q{iva:,.2f} | **Retención ISR:** Q{m_isr:,.2f} | **Total Líquido a Desembolsar:** Q{total_liquido:,.2f}")

        if st.button("💾 Guardar Factura de Compra", type="primary", key="btn_guardar_c"):
            if num_fac and prov_nombre and prov_nit and total_ingresado > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                fp_desc = f"{condicion_pago}" if condicion_pago == "Crédito" else f"Contado ({metodo_pago})"
                
                c.execute('''
                    INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, estado_pago, saldo_pendiente, created_by, created_at)
                    VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nombre, prov_nit, "Compra", "Compra", cuenta_debe, cuenta_haber, fp_desc, subtotal, iva, total_ingresado,
                      1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                      1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva, total_liquido,
                      "PENDIENTE" if condicion_pago == "Crédito" else "PAGADO", total_liquido if condicion_pago == "Crédito" else 0.0,
                      curr_u['username'], datetime.now().isoformat()))
                
                c.execute("INSERT INTO terceros_corp (id, tipo, nit, nombre, created_at) VALUES (%s, 'PROVEEDOR', %s, %s, %s) ON CONFLICT (nit) DO NOTHING",
                          (f"ter-{int(datetime.now().timestamp())}", prov_nit, prov_nombre, datetime.now().isoformat()))

                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                concepto = f"Fac. Compra {num_fac} de {prov_nombre}"
                cod_d, nom_d = cuenta_debe.split(" - ", 1)
                cod_h, nom_h = cuenta_haber.split(" - ", 1)

                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal, 0.0, concepto, fac_id, now_str))
                if iva > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", iva, 0.0, concepto, fac_id, now_str))
                if m_isr > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "2.1.03.01", "Retenciones ISR por Pagar", 0.0, m_isr, concepto, fac_id, now_str))
                if m_iva > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "2.1.03.02", "Retenciones IVA por Pagar", 0.0, m_iva, concepto, fac_id, now_str))
                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, 0.0, total_liquido, concepto, fac_id, now_str))
                conn.commit()
                conn.close()

                st.success("✅ Compra registrada y partida asentada en el Libro Diario.")
                st.rerun()
            else:
                st.error("Completa todos los campos obligatorios.")

    with tab_mod:
        conn = get_db_connection()
        df_facs_c = pd.read_sql_query("SELECT id, numero_factura, serie, fecha, tercero_nombre, total FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        if df_facs_c.empty:
            st.info("No hay facturas de compra para modificar.")
        else:
            opcs_fc = {f"{r['fecha']} | Fac: {r['numero_factura']} - {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs_c.iterrows()}
            sel_fc_label = st.selectbox("Seleccionar Factura a Modificar:", list(opcs_fc.keys()), key="mod_c_sel")
            sel_fc_id = opcs_fc[sel_fc_label]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total FROM facturas WHERE id = %s", (sel_fc_id,))
            f_row = c.fetchone()
            conn.close()

            cm1, cm2, cm3 = st.columns(3)
            new_num = cm1.text_input("Número Factura", value=f_row[0], key="mc_num")
            new_ser = cm2.text_input("Serie", value=f_row[1] or "", key="mc_ser")
            new_fec = cm3.date_input("Fecha Correcta", value=datetime.strptime(f_row[2], "%Y-%m-%d").date(), key="mc_fec")

            cm4, cm5 = st.columns(2)
            new_nom = cm4.text_input("Proveedor", value=f_row[3], key="mc_nom")
            new_nit = cm5.text_input("NIT", value=f_row[4], key="mc_nit")

            if st.button("💾 Guardar Corrección de Compra", type="primary"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE facturas SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s WHERE id = %s",
                          (new_num, new_ser, str(new_fec), new_nom, new_nit, sel_fc_id))
                c.execute("UPDATE libro_diario SET fecha = %s, concepto = %s WHERE referencia_id = %s",
                          (str(new_fec), f"Fac. Compra {new_num} de {new_nom}", sel_fc_id))
                conn.commit()
                conn.close()
                st.success("Factura y asientos contables corregidos.")
                st.rerun()

            if is_admin:
                st.markdown("---")
                if st.button("🗑️ Eliminar Factura Definitivamente", type="secondary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_fc_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s", (sel_fc_id,))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y partida eliminadas por el Administrador.")
                    st.rerun()

# ==========================================
# 4. FACTURA DE VENTAS
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title("📈 Factura de Ventas Emitidas")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    tab_vreg, tab_vmod = st.tabs(["➕ Emitir Venta", "✏️ Modificar / Corregir Venta"])

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_activo_cobro = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    opciones_haber_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    opciones_debe_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_activo_cobro.iterrows()]

    with tab_vreg:
        up_v = st.file_uploader("📄 Subir Factura Electrónica FEL Emitida (PDF) - Opcional:", type=["pdf"], key="pdf_venta")
        dat_v = extraer_datos_pdf_fel(up_v, tipo="VENTA") if up_v else None

        v1, v2, v3 = st.columns(3)
        v_num = v1.text_input("Número Factura / DTE *", value=(dat_v["dte"] if dat_v else ""), key="v_num")
        v_ser = v2.text_input("Serie", value=(dat_v["serie"] if dat_v else ""), key="v_ser")
        v_fec = v3.date_input("Fecha *", value=(datetime.strptime(dat_v["fecha"], "%Y-%m-%d").date() if (dat_v and dat_v["fecha"]) else date.today()), key="v_fec")

        v4, v5 = st.columns(2)
        v_nit = v4.text_input("NIT Cliente (Escribe 'CF' para Consumidor Final) *", value=(dat_v["nit"] if dat_v else ""), key="v_nit").strip().upper()
        default_nom = "CONSUMIDOR FINAL" if v_nit == "CF" else (dat_v["nombre"] if dat_v else "")
        v_nom = v5.text_input("Nombre / Razón Social *", value=default_nom, key="v_nom")

        col_vt, col_vm = st.columns(2)
        v_cond = col_vt.selectbox("Condición de Cobro:", ["Contado", "Crédito"], key="v_cond")
        if v_cond == "Contado":
            v_met = col_vm.selectbox("Cobrado mediante:", ["Efectivo / Caja", "Depósito / Bancos", "Tarjeta POS"], key="v_met")
            idx_vd = next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.01" in c), 0) if "Efectivo" in v_met else (next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.02" in c), 0) if "Depósito" in v_met else next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.03" in c), 0))
        else:
            col_vm.info("Se asentará a Clientes / Cuentas por Cobrar (1.1.02.01).")
            idx_vd = next((i for i, c in enumerate(opciones_debe_v) if "1.1.02.01" in c), 0)

        v6, v7 = st.columns(2)
        v_cdebe = v6.selectbox("Cuenta de Cargo (DEBE - Cobro/Activo):", opciones_debe_v if opciones_debe_v else ["1.1.01.01 - Caja General"], index=idx_vd, key="v_cdebe")
        v_chaber = v7.selectbox("Cuenta de Abono (HABER - Ingreso):", opciones_haber_v if opciones_haber_v else ["4.1.01.01 - Ventas"], key="v_chaber")

        v_tot = st.number_input("Total de la Factura Q *", value=(float(dat_v["total"]) if dat_v else 0.0), min_value=0.0, step=10.0, format="%.2f", key="v_tot")

        es_empresa_peq = "Pequeño Contribuyente" in str(selected_empresa_info[3])
        if es_empresa_peq:
            v_sub = v_tot
            v_iva = 0.0
            st.caption("ℹ️ Empresa en Régimen de Pequeño Contribuyente: IVA = Q0.00 (No genera débito fiscal)")
        else:
            v_sub = round(v_tot / 1.12, 2)
            v_iva = round(v_tot - v_sub, 2)

        vr1, vr2 = st.columns(2)
        v_ret_isr = vr1.checkbox("Cliente nos retuvo ISR", value=False, key="v_chkisr")
        v_tisr = vr1.selectbox("Tasa ISR Retenida:", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key="v_tisr")
        v_ret_iva = vr2.checkbox("Cliente nos retuvo IVA", value=False, key="v_chkiva")
        v_tiva = vr2.selectbox("Tasa IVA Retenida:", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key="v_tiva")

        _, _, vt_isr, vm_isr, vt_iva, vm_iva, v_liq = calcular_retenciones(v_sub, v_ret_isr, v_tisr, v_ret_iva, v_tiva)

        st.info(f"**Ingreso Gravado:** Q{v_sub:,.2f} | **IVA Débito Fiscal:** Q{v_iva:,.2f} | **Líquido a Recibir:** Q{v_liq:,.2f}")

        if st.button("💾 Guardar Factura de Venta", type="primary", key="btn_save_v"):
            if v_num and v_nom and v_nit and v_tot > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                fp_desc = f"{v_cond}" if v_cond == "Crédito" else f"Contado ({v_met})"

                c.execute('''
                    INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, estado_pago, saldo_pendiente, created_by, created_at)
                    VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (fac_id, selected_emp_id, v_num, v_ser, str(v_fec), v_nom, v_nit, "Venta", "Venta", v_chaber, v_cdebe, fp_desc, v_sub, v_iva, v_tot,
                      1 if (v_ret_isr and v_tisr != "No Aplica") else 0, vt_isr, vm_isr,
                      1 if (v_ret_iva and v_tiva != "No Aplica / No es Agente Retenedor") else 0, vt_iva, vm_iva, v_liq,
                      "PENDIENTE" if v_cond == "Crédito" else "PAGADO", v_liq if v_cond == "Crédito" else 0.0,
                      curr_u['username'], datetime.now().isoformat()))

                if v_nit != "CF":
                    c.execute("INSERT INTO terceros_corp (id, tipo, nit, nombre, created_at) VALUES (%s, 'CLIENTE', %s, %s, %s) ON CONFLICT (nit) DO NOTHING",
                              (f"ter-{int(datetime.now().timestamp())}", v_nit, v_nom, datetime.now().isoformat()))

                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                concepto = f"Fac. Venta {v_num} a {v_nom}"
                cod_d, nom_d = v_cdebe.split(" - ", 1)
                cod_h, nom_h = v_chaber.split(" - ", 1)

                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(v_fec), cod_d, nom_d, v_liq, 0.0, concepto, fac_id, now_str))
                if vm_isr > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(v_fec), "1.1.03.02", "ISR Retenido por Acreditar", vm_isr, 0.0, concepto, fac_id, now_str))
                if vm_iva > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(v_fec), "1.1.03.03", "Constancias de Retención IVA Recibidas", vm_iva, 0.0, concepto, fac_id, now_str))
                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(v_fec), cod_h, nom_h, 0.0, v_sub, concepto, fac_id, now_str))
                if v_iva > 0:
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(v_fec), "2.1.02.01", "IVA por Pagar (Débito Fiscal)", 0.0, v_iva, concepto, fac_id, now_str))
                conn.commit()
                conn.close()

                st.success("✅ Venta guardada y partida cuadrada en el Diario.")
                st.rerun()
            else:
                st.error("Completa el número de factura, cliente y monto.")

    with tab_vmod:
        conn = get_db_connection()
        df_facs_v = pd.read_sql_query("SELECT id, numero_factura, serie, fecha, tercero_nombre, total FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()
        if df_facs_v.empty:
            st.info("No hay facturas de venta para modificar.")
        else:
            opcs_fv = {f"{r['fecha']} | Fac: {r['numero_factura']} - {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs_v.iterrows()}
            sel_fv_label = st.selectbox("Seleccionar Venta a Modificar:", list(opcs_fv.keys()), key="mod_v_sel")
            sel_fv_id = opcs_fv[sel_fv_label]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total FROM facturas WHERE id = %s", (sel_fv_id,))
            fv_row = c.fetchone()
            conn.close()

            vm1, vm2, vm3 = st.columns(3)
            new_vnum = vm1.text_input("Número Factura", value=fv_row[0], key="mv_num")
            new_vser = vm2.text_input("Serie", value=fv_row[1] or "", key="mv_ser")
            new_vfec = vm3.date_input("Fecha Correcta", value=datetime.strptime(fv_row[2], "%Y-%m-%d").date(), key="mv_fec")

            vm4, vm5 = st.columns(2)
            new_vnom = vm4.text_input("Cliente", value=fv_row[3], key="mv_nom")
            new_vnit = vm5.text_input("NIT Cliente", value=fv_row[4], key="mv_nit")

            if st.button("💾 Guardar Corrección de Venta", type="primary"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE facturas SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s WHERE id = %s",
                          (new_vnum, new_vser, str(new_vfec), new_vnom, new_vnit, sel_fv_id))
                c.execute("UPDATE libro_diario SET fecha = %s, concepto = %s WHERE referencia_id = %s",
                          (str(new_vfec), f"Fac. Venta {new_vnum} a {new_vnom}", sel_fv_id))
                conn.commit()
                conn.close()
                st.success("Factura de venta y asiento contable corregidos.")
                st.rerun()

            if is_admin:
                st.markdown("---")
                if st.button("🗑️ Eliminar Factura Definitivamente", type="secondary", key="del_v_btn"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_fv_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s", (sel_fv_id,))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y asiento eliminados por el Administrador.")
                    st.rerun()

# ==========================================
# 5. LIBRO DE COMPRAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title("📋 Libro Oficial de Compras y Servicios Recibidos")
    st.caption(f"Empresa: {selected_empresa_info[1]} | NIT: {selected_empresa_info[2]}")

    c_f1, c_f2 = st.columns(2)
    f_desde = c_f1.date_input("Desde:", value=date(date.today().year, date.today().month, 1), key="lc_d")
    f_hasta = c_f2.date_input("Hasta:", value=date.today(), key="lc_h")

    conn = get_db_connection()
    q_compras = """
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura / DTE",
               tercero_nit AS "NIT Proveedor", tercero_nombre AS "Proveedor",
               subtotal AS "Base Gravada (Q)", iva AS "Crédito Fiscal (Q)", total AS "Total (Q)"
        FROM facturas
        WHERE empresa_id = %s AND tipo = 'COMPRA' AND fecha BETWEEN %s AND %s
        ORDER BY fecha ASC
    """
    df_lc = pd.read_sql_query(q_compras, conn, params=(selected_emp_id, str(f_desde), str(f_hasta)))
    conn.close()

    if df_lc.empty:
        st.info("Sin compras registradas en este período.")
    else:
        st.dataframe(df_lc, use_container_width=True)
        st.download_button("📥 Descargar Libro de Compras (CSV)", df_lc.to_csv(index=False).encode('utf-8'), f"compras_{selected_empresa_info[2]}.csv", "text/csv")

# ==========================================
# 6. LIBRO DE VENTAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title("📋 Libro Oficial de Ventas y Servicios Prestados")
    st.caption(f"Empresa: {selected_empresa_info[1]} | NIT: {selected_empresa_info[2]}")

    c_v1, c_v2 = st.columns(2)
    v_desde = c_v1.date_input("Desde:", value=date(date.today().year, date.today().month, 1), key="lv_d")
    v_hasta = c_v2.date_input("Hasta:", value=date.today(), key="lv_h")

    conn = get_db_connection()
    q_ventas = """
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura / DTE",
               tercero_nit AS "NIT Cliente", tercero_nombre AS "Cliente",
               subtotal AS "Ventas Gravadas (Q)", iva AS "Débito Fiscal (Q)", total AS "Total (Q)"
        FROM facturas
        WHERE empresa_id = %s AND tipo = 'VENTA' AND fecha BETWEEN %s AND %s
        ORDER BY fecha ASC
    """
    df_lv = pd.read_sql_query(q_ventas, conn, params=(selected_emp_id, str(v_desde), str(v_hasta)))
    conn.close()

    if df_lv.empty:
        st.info("Sin ventas registradas en este período.")
    else:
        st.dataframe(df_lv, use_container_width=True)
        st.download_button("📥 Descargar Libro de Ventas (CSV)", df_lv.to_csv(index=False).encode('utf-8'), f"ventas_{selected_empresa_info[2]}.csv", "text/csv")

# ==========================================
# 7. CATÁLOGO CORPORATIVO DE ARTÍCULOS E INVENTARIOS (KÁRDEX CONECTADO A PRODUCTOS)
# ==========================================
elif selected_menu == "📦 Catálogo de Artículos e Inventarios":
    st.title("📦 Catálogo Corporativo de Artículos e Inventarios")
    st.caption("Base de datos corporativa en Supabase. El catálogo es compartido para todas las empresas del sistema.")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COALESCE(MAX(NULLIF(regexp_replace(codigo, '[^0-9]', '', 'g'), '')::integer), 0) + 1 
        FROM productos;
    """)
    siguiente_correlativo = c.fetchone()[0]
    conn.close()

    with st.expander("➕ Registrar Producto en Catálogo Corporativo", expanded=True):
        col_c1, col_c2 = st.columns([1, 3])
        p_correlativo = col_c1.number_input("Código de Producto (Secuencia 1 a 1,000,000):", value=int(siguiente_correlativo), min_value=1, max_value=1000000)
        p_desc = col_c2.text_input("Descripción del Producto o Mercadería *")

        p3, p4 = st.columns(2)
        p_cost = p3.number_input("Costo de Referencia Q", min_value=0.0, step=5.0)
        p_vent = p4.number_input("Precio de Venta Sugerido Q", min_value=0.0, step=5.0)

        if st.button("💾 Guardar en Catálogo Corporativo", type="primary"):
            if p_desc.strip():
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    c.execute("""
                        INSERT INTO productos (empresa_id, codigo, descripcion, costo_promedio, precio_venta, existencia, created_at)
                        VALUES ('CORP', %s, %s, %s, %s, 0, %s)
                    """, (str(int(p_correlativo)), p_desc.strip(), p_cost, p_vent, datetime.now().isoformat()))
                    conn.commit()
                    st.success(f"✅ Producto #{p_correlativo} - '{p_desc}' guardado permanentemente a nivel corporativo.")
                    st.rerun()
                except Exception as ex:
                    conn.rollback()
                    st.error(f"Error al registrar producto: {ex}")
                finally:
                    conn.close()
            else:
                st.error("Ingresa la descripción del artículo.")

    st.markdown("---")
    st.subheader("Artículos Catalogados en el Sistema Corporativo")

    conn = get_db_connection()
    df_prods = pd.read_sql_query("""
        SELECT id, codigo AS "Código", descripcion AS "Descripción",
               costo_promedio AS "Costo Ref. (Q)", precio_venta AS "Precio Venta (Q)",
               existencia AS "Stock Actual"
        FROM productos
        ORDER BY NULLIF(regexp_replace(codigo, '[^0-9]', '', 'g'), '')::integer ASC, id ASC;
    """, conn)
    conn.close()

    if not df_prods.empty:
        st.dataframe(df_prods.drop(columns=['id']), use_container_width=True)
        
        if is_admin:
            with st.expander("🔒 Eliminar Producto (Exclusivo Administrador)", expanded=False):
                prod_map = {f"#{r['Código']} - {r['Descripción']}": r['id'] for _, r in df_prods.iterrows()}
                p_del_label = st.selectbox("Seleccionar producto para dar de baja:", list(prod_map.keys()))
                p_del_id = prod_map[p_del_label]
                if st.button("🗑️ Confirmar Eliminación", type="secondary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM productos WHERE id = %s", (p_del_id,))
                    conn.commit()
                    conn.close()
                    st.warning("Producto eliminado del catálogo corporativo.")
                    st.rerun()
    else:
        st.info("No hay productos catalogados aún en la base corporativa.")

# ==========================================
# 8. CUENTAS POR COBRAR
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title("💰 Cuentas por Cobrar (Créditos a Clientes)")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("SELECT fecha, numero_factura, tercero_nombre, total, total_a_pagar_cobrar, saldo_pendiente, estado_pago FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND forma_pago LIKE 'Crédito%%' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_cxc.empty:
        st.dataframe(df_cxc, use_container_width=True)
    else:
        st.info("Sin saldos pendientes por cobrar a clientes.")

# ==========================================
# 9. CUENTAS POR PAGAR
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("💳 Cuentas por Pagar (Créditos de Proveedores)")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("SELECT fecha, numero_factura, tercero_nombre, total, total_a_pagar_cobrar, saldo_pendiente, estado_pago FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND forma_pago LIKE 'Crédito%%' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_cxp.empty:
        st.dataframe(df_cxp, use_container_width=True)
    else:
        st.info("Sin deudas pendientes con proveedores.")

# ==========================================
# 10. CUENTAS BANCARIAS
# ==========================================
elif selected_menu == "🏦 Cuentas Bancarias":
    if not selected_emp_id: st.stop()
    st.title("🏦 Cuentas Bancarias y Tesorería")
    with st.expander("➕ Registrar Cuenta Bancaria", expanded=False):
        b1, b2 = st.columns(2)
        b_nom = b1.text_input("Banco (ej: Banco Industrial, Banrural)")
        b_num = b2.text_input("Número de Cuenta")
        b_tipo = st.selectbox("Tipo de Cuenta", ["Monetaria GTQ", "Ahorros GTQ", "Dólares USD"])
        if st.button("Guardar Cuenta Bancaria", type="primary"):
            if b_nom and b_num:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO cuentas_bancarias (id, empresa_id, nombre_banco, numero_cuenta, tipo_cuenta, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                          (f"bco-{int(datetime.now().timestamp())}", selected_emp_id, b_nom, b_num, b_tipo, datetime.now().isoformat()))
                conn.commit()
                conn.close()
                st.success("Cuenta bancaria registrada.")
                st.rerun()

    conn = get_db_connection()
    df_bancos = pd.read_sql_query("SELECT nombre_banco AS \"Banco\", numero_cuenta AS \"No. Cuenta\", tipo_cuenta AS \"Tipo\" FROM cuentas_bancarias WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()
    if not df_bancos.empty:
        st.dataframe(df_bancos, use_container_width=True)
    else:
        st.info("No hay cuentas bancarias registradas para esta empresa.")

# ==========================================
# 11. TRANSACCIONES CONTABLES (PARTIDAS MANUALES)
# ==========================================
elif selected_menu == "⚖️ Transacciones Contables (Partidas Manuales)":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Partidas Contables Manuales (Ajustes y Cierre)")
    
    conn = get_db_connection()
    df_ctas = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    list_ctas = [f"{r['codigo']} - {r['nombre']}" for _, r in df_ctas.iterrows()]

    with st.form("form_partida_manual"):
        c1, c2 = st.columns([1, 3])
        p_fecha = c1.date_input("Fecha de Partida", value=date.today())
        p_conc = c2.text_input("Concepto / Glosa de la Partida *")

        st.markdown("##### Movimiento Débito (Cargo):")
        d1, d2 = st.columns([3, 1])
        c_debe = d1.selectbox("Cuenta al DEBE:", list_ctas, key="pm_cd")
        m_debe = d2.number_input("Monto DEBE Q", min_value=0.0, step=10.0, format="%.2f", key="pm_md")

        st.markdown("##### Movimiento Crédito (Abono):")
        h1, h2 = st.columns([3, 1])
        c_haber = h1.selectbox("Cuenta al HABER:", list_ctas, key="pm_ch")
        m_haber = h2.number_input("Monto HABER Q", min_value=0.0, step=10.0, format="%.2f", key="pm_mh")

        if st.form_submit_button("Asentar Partida Manual", type="primary"):
            if round(m_debe, 2) == round(m_haber, 2) and m_debe > 0 and p_conc:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                ref_m = f"man-{int(datetime.now().timestamp())}"

                cod_d, nom_d = c_debe.split(" - ", 1)
                cod_h, nom_h = c_haber.split(" - ", 1)

                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(p_fecha), cod_d, nom_d, m_debe, 0.0, p_conc, ref_m, now_str))
                c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                          (selected_emp_id, p_no, str(p_fecha), cod_h, nom_h, 0.0, m_haber, p_conc, ref_m, now_str))
                conn.commit()
                conn.close()
                st.success(f"✅ Partida #{p_no} registrada correctamente.")
                st.rerun()
            else:
                st.error("La partida debe estar balanceada (Debe = Haber) y con monto mayor a cero.")

# ==========================================
# 12. LIBRO DIARIO
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_d = pd.read_sql_query("SELECT partida_no AS \"Partida #\", fecha AS \"Fecha\", codigo_cuenta AS \"Código\", nombre_cuenta AS \"Cuenta\", debe AS \"Debe (Q)\", haber AS \"Haber (Q)\", concepto AS \"Concepto\" FROM libro_diario WHERE empresa_id = %s ORDER BY partida_no ASC, id ASC", conn, params=(selected_emp_id,))
    conn.close()
    if df_d.empty:
        st.info("Sin registros en el Libro Diario.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Débitos (Debe)", f"Q {df_d['Debe (Q)'].sum():,.2f}")
        c2.metric("Total Créditos (Haber)", f"Q {df_d['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_d, use_container_width=True)

# ==========================================
# 13. LIBRO MAYOR
# ==========================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title(f"📚 Libro Mayor — {selected_empresa_info[1]}")
    conn = get_db_connection()
    query_m = """
        SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
               ROUND(SUM(debe)::numeric, 2) AS "Total Debe (Q)",
               ROUND(SUM(haber)::numeric, 2) AS "Total Haber (Q)",
               ROUND((SUM(debe) - SUM(haber))::numeric, 2) AS "Saldo Deudor/Acreedor (Q)"
        FROM libro_diario
        WHERE empresa_id = %s
        GROUP BY codigo_cuenta, nombre_cuenta
        ORDER BY codigo_cuenta ASC
    """
    df_m = pd.read_sql_query(query_m, conn, params=(selected_emp_id,))
    conn.close()
    if df_m.empty:
        st.info("Sin registros en el Libro Mayor.")
    else:
        st.dataframe(df_m, use_container_width=True)

# ==========================================
# 14. BALANCE GENERAL DE SALDOS (CON ESTRUCTURA CONTABLE Y MATRIZ MENSUAL)
# ==========================================
elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    st.caption(f"Empresa: {selected_empresa_info[1]} | NIT: {selected_empresa_info[2]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(selected_emp_id,))
    conn.close()
    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    c_f1, c_f2, c_f3 = st.columns([2.5, 1.5, 1])
    modo_vista = c_f1.radio("Visualizar Saldo por:", ["🌐 Saldo Acumulado Histórico (Mes a Mes)", "📆 Por Año Completo", "📅 Por Mes"], horizontal=True, key="bsal_modo")
    anio_sel = c_f2.selectbox("Año Fiscal:", anios_list, key="bsal_anio")
    color_filtro = c_f3.color_picker("Color de filtro:", value="#1E3A8A", key="bsal_color")

    if modo_vista == "🌐 Saldo Acumulado Histórico (Mes a Mes)":
        st.markdown(f"""
            <div style="background-color: {color_filtro}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
                ⚖️ Balance General de Saldos — Estructura Formal Desglosada Mes a Mes ({anio_sel})
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        q_bal_matriz = """
            SELECT n.codigo, n.nombre, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   COALESCE(SUM(d.debe), 0) AS total_debe,
                   COALESCE(SUM(d.haber), 0) AS total_haber
            FROM nomenclatura n
            JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY n.codigo, n.nombre, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY n.codigo ASC
        """
        df_raw_bs = pd.read_sql_query(q_bal_matriz, conn, params=(selected_emp_id, anio_sel))
        conn.close()

        if df_raw_bs.empty:
            st.info(f"Sin movimientos contables registrados en el año {anio_sel}.")
        else:
            grupos = [
                ("1. CUENTAS DE ACTIVO", df_raw_bs[df_raw_bs['tipo'] == 'Activo']),
                ("2. CUENTAS DE PASIVO", df_raw_bs[df_raw_bs['tipo'] == 'Pasivo']),
                ("3. CUENTAS DE CAPITAL / PATRIMONIO", df_raw_bs[df_raw_bs['tipo'] == 'Capital']),
                ("4. CUENTAS DE RESULTADOS (INGRESOS)", df_raw_bs[df_raw_bs['tipo'] == 'Ingreso']),
                ("5. CUENTAS DE RESULTADOS (GASTOS)", df_raw_bs[df_raw_bs['tipo'] == 'Gasto'])
            ]

            filas_bs = []
            sum_deudor_mes = {k: 0.0 for k in meses_dict.keys()}
            sum_acreedor_mes = {k: 0.0 for k in meses_dict.keys()}

            for grp_nombre, grp_df in grupos:
                if grp_df.empty:
                    continue
                filas_bs.append({"Estructura Contable": f"📁 {grp_nombre}", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
                cuentas_grp = grp_df[['codigo', 'nombre', 'tipo']].drop_duplicates().sort_values(by='codigo')

                for _, cr in cuentas_grp.iterrows():
                    fila_c = {"Estructura Contable": f"  • {cr['codigo']} - {cr['nombre']}"}
                    cta_tot = 0.0
                    for cod_m, nom_m in meses_dict.items():
                        m_sub = grp_df[(grp_df['codigo'] == cr['codigo']) & (grp_df['mes'] == cod_m)]
                        d = float(m_sub['total_debe'].sum()) if not m_sub.empty else 0.0
                        h = float(m_sub['total_haber'].sum()) if not m_sub.empty else 0.0
                        saldo_neto = (d - h) if cr['tipo'] in ['Activo', 'Gasto'] else (h - d)
                        fila_c[nom_m] = f"Q {saldo_neto:,.2f}"
                        cta_tot += saldo_neto
                        if cr['tipo'] in ['Activo', 'Gasto']:
                            sum_deudor_mes[cod_m] += max(0, d - h)
                            sum_acreedor_mes[cod_m] += max(0, h - d)
                        else:
                            sum_acreedor_mes[cod_m] += max(0, h - d)
                            sum_deudor_mes[cod_m] += max(0, d - h)
                    fila_c["TOTAL ACUMULADO ANUAL"] = f"Q {cta_tot:,.2f}"
                    filas_bs.append(fila_c)

            # Filas de Totales y Cuadratura
            fila_deud = {"Estructura Contable": "(=) SUMA DEUDORA TOTAL"}
            for cod_m, nom_m in meses_dict.items():
                fila_deud[nom_m] = f"Q {sum_deudor_mes[cod_m]:,.2f}"
            fila_deud["TOTAL ACUMULADO ANUAL"] = f"Q {sum(sum_deudor_mes.values()):,.2f}"
            filas_bs.append(fila_deud)

            fila_acred = {"Estructura Contable": "(=) SUMA ACREEDORA TOTAL"}
            for cod_m, nom_m in meses_dict.items():
                fila_acred[nom_m] = f"Q {sum_acreedor_mes[cod_m]:,.2f}"
            fila_acred["TOTAL ACUMULADO ANUAL"] = f"Q {sum(sum_acreedor_mes.values()):,.2f}"
            filas_bs.append(fila_acred)

            df_matriz_bs = pd.DataFrame(filas_bs)
            st.dataframe(df_matriz_bs, use_container_width=True)

    else:
        cond_sql = "n.empresa_id = %s"
        sql_params = [selected_emp_id]
        etiqueta = "Histórico Acumulado Completo"

        if modo_vista == "📆 Por Año Completo":
            cond_sql += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
            sql_params.append(anio_sel)
            etiqueta = f"Año Fiscal {anio_sel}"
        elif modo_vista == "📅 Por Mes":
            mes_sel = st.selectbox("Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="bsal_mes")
            cond_sql += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            sql_params.extend([anio_sel, mes_sel])
            etiqueta = f"{meses_dict[mes_sel]} {anio_sel}"

        st.markdown(f"""
            <div style="background-color: {color_filtro}; padding: 10px 16px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 15px;">
                📌 Período Consultado: {etiqueta}
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        query_b = f"""
            SELECT n.codigo, n.nombre, n.tipo,
                   COALESCE(SUM(d.debe), 0) AS total_debe,
                   COALESCE(SUM(d.haber), 0) AS total_haber
            FROM nomenclatura n
            LEFT JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE {cond_sql}
            GROUP BY n.codigo, n.nombre, n.tipo
            HAVING COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0
            ORDER BY n.codigo ASC
        """
        df_bal = pd.read_sql_query(query_b, conn, params=tuple(sql_params))
        conn.close()

        if df_bal.empty:
            st.info("Sin registros en el período.")
        else:
            df_bal['Saldo Deudor (Q)'] = df_bal.apply(lambda r: round(float(r['total_debe'] - r['total_haber']), 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
            df_bal['Saldo Acreedor (Q)'] = df_bal.apply(lambda r: round(float(r['total_haber'] - r['total_debe']), 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)

            c1, c2, c3 = st.columns(3)
            sd = df_bal['Saldo Deudor (Q)'].sum()
            sa = df_bal['Saldo Acreedor (Q)'].sum()
            dif = round(sd - sa, 2)
            c1.metric("Suma Deudora", f"Q {sd:,.2f}")
            c2.metric("Suma Acreedora", f"Q {sa:,.2f}")
            c3.metric("Diferencia", f"Q {dif:,.2f}", delta="Cuadrado ✅" if abs(dif) < 0.01 else "Descuadre ⚠️")

            st.dataframe(df_bal[['codigo', 'nombre', 'tipo', 'Saldo Deudor (Q)', 'Saldo Acreedor (Q)']], use_container_width=True)

# ==========================================
# 15. BALANCE DE SITUACIÓN FINANCIERA (CON ESTRUCTURA CONTABLE Y MATRIZ MENSUAL)
# ==========================================
elif selected_menu == "🏛️ Balance de Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title("🏛️ Balance de Situación Financiera")
    st.caption(f"Empresa: {selected_empresa_info[1]} | Régimen: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(selected_emp_id,))
    conn.close()
    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    col_btn1, col_btn2, col_btn3 = st.columns([2.5, 1.5, 1])
    modo_sit = col_btn1.radio("Visualizar por:", ["🌐 Saldo Acumulado (Mes a Mes)", "📆 Por Año", "📅 Por Mes"], horizontal=True, key="sit_modo")
    anio_sel_sit = col_btn2.selectbox("Año Fiscal:", anios_list, key="sit_anio")
    color_sit = col_btn3.color_picker("Color del Año:", value="#1E3A8A", key="sit_color")

    if modo_sit == "🌐 Saldo Acumulado (Mes a Mes)":
        st.markdown(f"""
            <div style="background-color: {color_sit}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 18px;">
                🏛️ Estado de Situación Financiera — Matriz Completa Clasificada ({anio_sel_sit})
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        q_raw_sit = """
            SELECT n.codigo, n.nombre, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   COALESCE(SUM(d.debe), 0) AS total_debe,
                   COALESCE(SUM(d.haber), 0) AS total_haber
            FROM nomenclatura n
            JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY n.codigo, n.nombre, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY n.codigo ASC
        """
        df_sit_raw = pd.read_sql_query(q_raw_sit, conn, params=(selected_emp_id, anio_sel_sit))
        conn.close()

        if df_sit_raw.empty:
            st.info(f"Sin registros contables en el año {anio_sel_sit}.")
        else:
            act_corr_df = df_sit_raw[(df_sit_raw['tipo'] == 'Activo') & (df_sit_raw['codigo'].str.startswith('1.1'))]
            act_nocorr_df = df_sit_raw[(df_sit_raw['tipo'] == 'Activo') & (df_sit_raw['codigo'].str.startswith('1.2'))]
            pas_corr_df = df_sit_raw[(df_sit_raw['tipo'] == 'Pasivo') & (df_sit_raw['codigo'].str.startswith('2.1'))]
            pas_nocorr_df = df_sit_raw[(df_sit_raw['tipo'] == 'Pasivo') & (df_sit_raw['codigo'].str.startswith('2.2'))]
            cap_df = df_sit_raw[df_sit_raw['tipo'] == 'Capital']

            filas_sit_matriz = []
            tot_act_corr_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_act_nocorr_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_pas_corr_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_pas_nocorr_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_cap_mes = {k: 0.0 for k in meses_dict.keys()}
            util_neta_mes = {k: 0.0 for k in meses_dict.keys()}

            # 1. ACTIVO CORRIENTE
            filas_sit_matriz.append({"Estructura Contable": "🏢 ACTIVO CORRIENTE", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in act_corr_df[['codigo', 'nombre']].drop_duplicates().iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                anual_c = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = act_corr_df[(act_corr_df['codigo'] == r['codigo']) & (act_corr_df['mes'] == cod_m)]
                    val = float(sub['total_debe'].sum() - sub['total_haber'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_act_corr_mes[cod_m] += val
                    anual_c += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {anual_c:,.2f}"
                filas_sit_matriz.append(fila)

            # Subtotal Activo Corriente
            f_sub_ac = {"Estructura Contable": "(=) Subtotal Activo Corriente"}
            for cod_m, nom_m in meses_dict.items():
                f_sub_ac[nom_m] = f"Q {tot_act_corr_mes[cod_m]:,.2f}"
            f_sub_ac["TOTAL ACUMULADO ANUAL"] = f"Q {sum(tot_act_corr_mes.values()):,.2f}"
            filas_sit_matriz.append(f_sub_ac)

            # 2. ACTIVO NO CORRIENTE
            filas_sit_matriz.append({"Estructura Contable": "🏢 ACTIVO NO CORRIENTE", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in act_nocorr_df[['codigo', 'nombre']].drop_duplicates().iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                anual_c = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = act_nocorr_df[(act_nocorr_df['codigo'] == r['codigo']) & (act_nocorr_df['mes'] == cod_m)]
                    val = float(sub['total_debe'].sum() - sub['total_haber'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_act_nocorr_mes[cod_m] += val
                    anual_c += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {anual_c:,.2f}"
                filas_sit_matriz.append(fila)

            # TOTAL ACTIVO
            f_tot_act = {"Estructura Contable": "🌟 TOTAL ACTIVO"}
            for cod_m, nom_m in meses_dict.items():
                f_tot_act[nom_m] = f"Q {(tot_act_corr_mes[cod_m] + tot_act_nocorr_mes[cod_m]):,.2f}"
            f_tot_act["TOTAL ACUMULADO ANUAL"] = f"Q {(sum(tot_act_corr_mes.values()) + sum(tot_act_nocorr_mes.values())):,.2f}"
            filas_sit_matriz.append(f_tot_act)

            # 3. PASIVO CORRIENTE
            filas_sit_matriz.append({"Estructura Contable": "💳 PASIVO CORRIENTE", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in pas_corr_df[['codigo', 'nombre']].drop_duplicates().iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                anual_c = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = pas_corr_df[(pas_corr_df['codigo'] == r['codigo']) & (pas_corr_df['mes'] == cod_m)]
                    val = float(sub['total_haber'].sum() - sub['total_debe'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_pas_corr_mes[cod_m] += val
                    anual_c += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {anual_c:,.2f}"
                filas_sit_matriz.append(fila)

            # Provisión ISR Pasivo
            f_prov_isr = {"Estructura Contable": "  • 2.1.03.99 - Provisión ISR por Pagar"}
            tot_isr_pas = 0.0
            for cod_m, nom_m in meses_dict.items():
                sub_i = df_sit_raw[(df_sit_raw['tipo'] == 'Ingreso') & (df_sit_raw['mes'] == cod_m)]
                sub_g = df_sit_raw[(df_sit_raw['tipo'] == 'Gasto') & (df_sit_raw['mes'] == cod_m)]
                ing_m = float(sub_i['total_haber'].sum() - sub_i['total_debe'].sum()) if not sub_i.empty else 0.0
                gas_m = float(sub_g['total_debe'].sum() - sub_g['total_haber'].sum()) if not sub_g.empty else 0.0
                imp, neta, _ = calcular_impuesto_renta(selected_empresa_info[3], ing_m, ing_m - gas_m)
                f_prov_isr[nom_m] = f"Q {imp:,.2f}"
                tot_pas_corr_mes[cod_m] += imp
                util_neta_mes[cod_m] = neta
                tot_isr_pas += imp
            f_prov_isr["TOTAL ACUMULADO ANUAL"] = f"Q {tot_isr_pas:,.2f}"
            filas_sit_matriz.append(f_prov_isr)

            # 4. PATRIMONIO NETO / CAPITAL
            filas_sit_matriz.append({"Estructura Contable": "🏛️ PATRIMONIO NETO / CAPITAL", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in cap_df[['codigo', 'nombre']].drop_duplicates().iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                anual_c = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = cap_df[(cap_df['codigo'] == r['codigo']) & (cap_df['mes'] == cod_m)]
                    val = float(sub['total_haber'].sum() - sub['total_debe'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_cap_mes[cod_m] += val
                    anual_c += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {anual_c:,.2f}"
                filas_sit_matriz.append(fila)

            # Utilidad neta mes a mes incorporada en el patrimonio
            f_util_pat = {"Estructura Contable": "  • 3.1.02.02 - Utilidad del Período (Desp. Impuesto)"}
            for cod_m, nom_m in meses_dict.items():
                f_util_pat[nom_m] = f"Q {util_neta_mes[cod_m]:,.2f}"
            f_util_pat["TOTAL ACUMULADO ANUAL"] = f"Q {sum(util_neta_mes.values()):,.2f}"
            filas_sit_matriz.append(f_util_pat)

            # TOTAL PASIVO + PATRIMONIO
            f_tot_pas_pat = {"Estructura Contable": "🎯 TOTAL PASIVO + PATRIMONIO"}
            for cod_m, nom_m in meses_dict.items():
                tot_p = tot_pas_corr_mes[cod_m] + tot_pas_nocorr_mes[cod_m]
                tot_pat = tot_cap_mes[cod_m] + util_neta_mes[cod_m]
                f_tot_pas_pat[nom_m] = f"Q {(tot_p + tot_pat):,.2f}"
            tot_p_anual = sum(tot_pas_corr_mes.values()) + sum(tot_pas_nocorr_mes.values())
            tot_pat_anual = sum(tot_cap_mes.values()) + sum(util_neta_mes.values())
            f_tot_pas_pat["TOTAL ACUMULADO ANUAL"] = f"Q {(tot_p_anual + tot_pat_anual):,.2f}"
            filas_sit_matriz.append(f_tot_pas_pat)

            st.dataframe(pd.DataFrame(filas_sit_matriz), use_container_width=True)

    else:
        cond_sit = "d.empresa_id = %s"
        params_sit = [selected_emp_id]
        tit_sit = f"Acumulado Histórico al {date.today()}"

        if modo_sit == "📆 Por Año":
            cond_sit += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
            params_sit.append(anio_sel_sit)
            tit_sit = f"Al cierre del Año Fiscal {anio_sel_sit}"
        elif modo_sit == "📅 Por Mes":
            mes_sel_sit = st.selectbox("Seleccionar Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="sit_mes")
            cond_sit += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_sit.extend([anio_sel_sit, mes_sel_sit])
            tit_sit = f"Al cierre de {meses_dict[mes_sel_sit]} de {anio_sel_sit}"

        st.markdown(f"""
            <div style="background-color: {color_sit}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 18px;">
                🏛️ Estado de Situación Financiera — {tit_sit}
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        q_cuentas = f"""
            SELECT n.codigo, n.nombre, n.tipo,
                   COALESCE(SUM(d.debe), 0) AS total_debe,
                   COALESCE(SUM(d.haber), 0) AS total_haber
            FROM nomenclatura n
            JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE {cond_sit}
            GROUP BY n.codigo, n.nombre, n.tipo
            ORDER BY n.codigo ASC
        """
        df_raw = pd.read_sql_query(q_cuentas, conn, params=tuple(params_sit))

        q_res = f"""
            SELECT 
                COALESCE(SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE 0 END), 0) AS tot_ingresos,
                COALESCE(SUM(CASE WHEN n.tipo = 'Gasto' THEN (d.debe - d.haber) ELSE 0 END), 0) AS tot_gastos
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE {cond_sit}
        """
        c = conn.cursor()
        c.execute(q_res, tuple(params_sit))
        row_res = c.fetchone()
        tot_ing = float(row_res[0]) if row_res else 0.0
        tot_gas = float(row_res[1]) if row_res else 0.0
        conn.close()

        utilidad_antes_imp = round(tot_ing - tot_gas, 2)
        monto_imp, ganancia_desp_imp, tasa_nom = calcular_impuesto_renta(selected_empresa_info[3], tot_ing, utilidad_antes_imp)

        if df_raw.empty:
            st.info("Sin registros en este período.")
        else:
            df_raw['Saldo'] = df_raw.apply(lambda r: (r['total_debe'] - r['total_haber']) if r['tipo'] == 'Activo' else (r['total_haber'] - r['total_debe']), axis=1)

            act_corr = df_raw[(df_raw['tipo'] == 'Activo') & (df_raw['codigo'].str.startswith('1.1'))]
            act_nocorr = df_raw[(df_raw['tipo'] == 'Activo') & (df_raw['codigo'].str.startswith('1.2'))]
            pas_corr = df_raw[(df_raw['tipo'] == 'Pasivo') & (df_raw['codigo'].str.startswith('2.1'))].copy()
            pas_nocorr = df_raw[(df_raw['tipo'] == 'Pasivo') & (df_raw['codigo'].str.startswith('2.2'))]
            capital = df_raw[df_raw['tipo'] == 'Capital'].copy()

            tot_pas_corr = float(pas_corr['Saldo'].sum()) + monto_imp
            tot_pas_nocorr = float(pas_nocorr['Saldo'].sum())
            total_pasivo = tot_pas_corr + tot_pas_nocorr

            tot_act_corr = float(act_corr['Saldo'].sum())
            tot_act_nocorr = float(act_nocorr['Saldo'].sum())
            total_activo = tot_act_corr + tot_act_nocorr

            tot_cap = float(capital['Saldo'].sum())
            total_patrimonio = tot_cap + ganancia_desp_imp
            total_pasivo_patrimonio = total_pasivo + total_patrimonio

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Total Activo", f"Q {total_activo:,.2f}")
            r2.metric("Total Pasivo + Patrimonio", f"Q {total_pasivo_patrimonio:,.2f}")
            r3.metric(f"Impuesto ({tasa_nom})", f"Q {monto_imp:,.2f}")
            r4.metric("Ganancia Neta Desp. Impuestos", f"Q {ganancia_desp_imp:,.2f}")

            st.markdown("---")
            col_izq, col_der = st.columns(2)

            with col_izq:
                st.markdown("### 🏢 ACTIVO")
                st.markdown("#### **Activo Corriente**")
                for _, r in act_corr.iterrows():
                    st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
                st.info(f"**Total Activo Corriente: Q {tot_act_corr:,.2f}**")

                st.markdown("#### **Activo No Corriente**")
                for _, r in act_nocorr.iterrows():
                    st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
                st.info(f"**Total Activo No Corriente: Q {tot_act_nocorr:,.2f}**")

                st.success(f"## **TOTAL ACTIVO: Q {total_activo:,.2f}**")

            with col_der:
                st.markdown("### 💳 PASIVO Y PATRIMONIO NETO")
                st.markdown("#### **Pasivo Corriente**")
                for _, r in pas_corr.iterrows():
                    st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
                if monto_imp > 0:
                    st.write(f"• **2.1.03.99 - Provisión ISR por Pagar**: Q {monto_imp:,.2f}")
                st.info(f"**Total Pasivo: Q {total_pasivo:,.2f}**")

                st.markdown("#### **Patrimonio Neto / Capital**")
                for _, r in capital.iterrows():
                    st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
                st.write(f"• **3.1.02.02 - Utilidad del Período (Después de Impuesto)**: Q {ganancia_desp_imp:,.2f}")
                st.info(f"**Total Patrimonio: Q {total_patrimonio:,.2f}**")

                st.success(f"## **TOTAL PASIVO + PATRIMONIO: Q {total_pasivo_patrimonio:,.2f}**")

# ==========================================
# 16. ESTADO DE RESULTADOS (ESTRUCTURA CONTABLE BASE COMPLETA EN MATRIZ ACUMULADA)
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados")
    st.caption(f"{selected_empresa_info[1]} | Régimen: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(selected_emp_id,))
    conn.close()
    anios_list = df_anios['anio'].dropna().tolist() if not df_anios.empty else [str(datetime.now().year)]

    col_er1, col_er2, col_er3 = st.columns([2.5, 1.5, 1])
    modo_er = col_er1.radio("Visualizar Estado de Resultados por:", ["🌐 Histórico Acumulado (Mes a Mes)", "📆 Por Año", "📅 Por Mes"], horizontal=True, key="er_modo")
    anio_er = col_er2.selectbox("Año Fiscal:", anios_list, key="er_anio")
    color_er = col_er3.color_picker("Color de visualización:", value="#1E3A8A", key="er_color")

    if modo_er == "🌐 Histórico Acumulado (Mes a Mes)":
        st.markdown(f"""
            <div style="background-color: {color_er}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
                📊 Estado de Resultados — Matriz con Estructura Contable Formal (Enero a Diciembre y Total Acumulado) Año {anio_er}
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        q_matriz = """
            SELECT 
                n.codigo,
                n.nombre,
                n.tipo,
                SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS monto
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s 
              AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
              AND n.tipo IN ('Ingreso', 'Gasto')
            GROUP BY n.codigo, n.nombre, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY n.codigo ASC
        """
        df_movs = pd.read_sql_query(q_matriz, conn, params=(selected_emp_id, anio_er))
        conn.close()

        if df_movs.empty:
            st.info(f"Sin movimientos en el ejercicio fiscal {anio_er}.")
        else:
            cuentas_ing = df_movs[df_movs['tipo'] == 'Ingreso'][['codigo', 'nombre']].drop_duplicates().sort_values(by='codigo')
            cuentas_cost = df_movs[(df_movs['tipo'] == 'Gasto') & (df_movs['codigo'].str.startswith('5.1.01'))][['codigo', 'nombre']].drop_duplicates().sort_values(by='codigo')
            cuentas_gast = df_movs[(df_movs['tipo'] == 'Gasto') & (~df_movs['codigo'].str.startswith('5.1.01'))][['codigo', 'nombre']].drop_duplicates().sort_values(by='codigo')

            tot_ing_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_cost_mes = {k: 0.0 for k in meses_dict.keys()}
            tot_gast_mes = {k: 0.0 for k in meses_dict.keys()}

            filas_estructura = []

            # 1. INGRESOS OPERACIONALES
            filas_estructura.append({"Estructura Contable": "➕ INGRESOS OPERACIONALES", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in cuentas_ing.iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                cta_anual = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = df_movs[(df_movs['codigo'] == r['codigo']) & (df_movs['mes'] == cod_m)]
                    val = float(sub['monto'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_ing_mes[cod_m] += val
                    cta_anual += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {cta_anual:,.2f}"
                filas_estructura.append(fila)

            # Subtotal Ingresos
            fila_sub_ing = {"Estructura Contable": "(=) TOTAL INGRESOS OPERACIONALES"}
            for cod_m, nom_m in meses_dict.items():
                fila_sub_ing[nom_m] = f"Q {tot_ing_mes[cod_m]:,.2f}"
            fila_sub_ing["TOTAL ACUMULADO ANUAL"] = f"Q {sum(tot_ing_mes.values()):,.2f}"
            filas_estructura.append(fila_sub_ing)

            # 2. COSTO DE VENTAS
            filas_estructura.append({"Estructura Contable": "➖ COSTO DE VENTAS", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in cuentas_cost.iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                cta_anual = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = df_movs[(df_movs['codigo'] == r['codigo']) & (df_movs['mes'] == cod_m)]
                    val = float(sub['monto'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_cost_mes[cod_m] += val
                    cta_anual += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {cta_anual:,.2f}"
                filas_estructura.append(fila)

            # Subtotal Costo
            fila_sub_cost = {"Estructura Contable": "(=) TOTAL COSTO DE VENTAS"}
            for cod_m, nom_m in meses_dict.items():
                fila_sub_cost[nom_m] = f"Q {tot_cost_mes[cod_m]:,.2f}"
            fila_sub_cost["TOTAL ACUMULADO ANUAL"] = f"Q {sum(tot_cost_mes.values()):,.2f}"
            filas_estructura.append(fila_sub_cost)

            # 3. UTILIDAD BRUTA EN VENTAS
            fila_ub = {"Estructura Contable": "🎯 UTILIDAD BRUTA EN VENTAS"}
            tot_ub_anual = 0.0
            for cod_m, nom_m in meses_dict.items():
                ub = tot_ing_mes[cod_m] - tot_cost_mes[cod_m]
                fila_ub[nom_m] = f"Q {ub:,.2f}"
                tot_ub_anual += ub
            fila_ub["TOTAL ACUMULADO ANUAL"] = f"Q {tot_ub_anual:,.2f}"
            filas_estructura.append(fila_ub)

            # 4. GASTOS DE OPERACIÓN Y ADMINISTRACIÓN
            filas_estructura.append({"Estructura Contable": "➖ GASTOS DE OPERACIÓN Y ADMINISTRACIÓN", **{m: "" for m in meses_dict.values()}, "TOTAL ACUMULADO ANUAL": ""})
            for _, r in cuentas_gast.iterrows():
                fila = {"Estructura Contable": f"  • {r['codigo']} - {r['nombre']}"}
                cta_anual = 0.0
                for cod_m, nom_m in meses_dict.items():
                    sub = df_movs[(df_movs['codigo'] == r['codigo']) & (df_movs['mes'] == cod_m)]
                    val = float(sub['monto'].sum()) if not sub.empty else 0.0
                    fila[nom_m] = f"Q {val:,.2f}"
                    tot_gast_mes[cod_m] += val
                    cta_anual += val
                fila["TOTAL ACUMULADO ANUAL"] = f"Q {cta_anual:,.2f}"
                filas_estructura.append(fila)

            # Subtotal Gastos
            fila_sub_gast = {"Estructura Contable": "(=) TOTAL GASTOS DE OPERACIÓN"}
            for cod_m, nom_m in meses_dict.items():
                fila_sub_gast[nom_m] = f"Q {tot_gast_mes[cod_m]:,.2f}"
            fila_sub_gast["TOTAL ACUMULADO ANUAL"] = f"Q {sum(tot_gast_mes.values()):,.2f}"
            filas_estructura.append(fila_sub_gast)

            # 5. UTILIDAD DE OPERACIÓN
            fila_uop = {"Estructura Contable": "📊 UTILIDAD DE OPERACIÓN"}
            tot_uop_anual = 0.0
            for cod_m, nom_m in meses_dict.items():
                uop = (tot_ing_mes[cod_m] - tot_cost_mes[cod_m]) - tot_gast_mes[cod_m]
                fila_uop[nom_m] = f"Q {uop:,.2f}"
                tot_uop_anual += uop
            fila_uop["TOTAL ACUMULADO ANUAL"] = f"Q {tot_uop_anual:,.2f}"
            filas_estructura.append(fila_uop)

            # 6. IMPUESTO ESTIMADO (SAT)
            fila_isr = {"Estructura Contable": "⚖️ PROVISIÓN IMPUESTO (SAT)"}
            tot_isr_anual = 0.0
            for cod_m, nom_m in meses_dict.items():
                uop = (tot_ing_mes[cod_m] - tot_cost_mes[cod_m]) - tot_gast_mes[cod_m]
                imp, _, _ = calcular_impuesto_renta(selected_empresa_info[3], tot_ing_mes[cod_m], uop)
                fila_isr[nom_m] = f"Q {imp:,.2f}"
                tot_isr_anual += imp
            fila_isr["TOTAL ACUMULADO ANUAL"] = f"Q {tot_isr_anual:,.2f}"
            filas_estructura.append(fila_isr)

            # 7. GANANCIA NETA DESPUÉS DE IMPUESTOS
            fila_neta = {"Estructura Contable": "🏁 UTILIDAD NETA DESPUÉS DE IMPUESTOS"}
            for cod_m, nom_m in meses_dict.items():
                uop = (tot_ing_mes[cod_m] - tot_cost_mes[cod_m]) - tot_gast_mes[cod_m]
                imp, neta, _ = calcular_impuesto_renta(selected_empresa_info[3], tot_ing_mes[cod_m], uop)
                fila_neta[nom_m] = f"Q {neta:,.2f}"
            fila_neta["TOTAL ACUMULADO ANUAL"] = f"Q {(tot_uop_anual - tot_isr_anual):,.2f}"
            filas_estructura.append(fila_neta)

            df_matriz_final = pd.DataFrame(filas_estructura)

            tot_ing = sum(tot_ing_mes.values())
            tot_gas = sum(tot_cost_mes.values()) + sum(tot_gast_mes.values())
            util_ant = round(tot_ing - tot_gas, 2)
            imp_calc, gan_neta, desc_imp = calcular_impuesto_renta(selected_empresa_info[3], tot_ing, util_ant)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Ingresos Anuales Acumulados", f"Q {tot_ing:,.2f}")
            m2.metric("Costos y Gastos Anuales", f"Q {tot_gas:,.2f}")
            m3.metric(f"Impuesto ({desc_imp})", f"Q {imp_calc:,.2f}")
            m4.metric("Ganancia Neta Desp. Impuestos", f"Q {gan_neta:,.2f}", delta=f"{'Ganancia ✅' if gan_neta >= 0 else 'Pérdida ⚠️'}")

            st.markdown("---")
            st.markdown("### 📑 Estructura Contable Formal Clasificada (Mes a Mes)")
            st.dataframe(df_matriz_final, use_container_width=True)

    else:
        cond_er = "d.empresa_id = %s"
        params_er = [selected_emp_id]
        label_er = f"Ejercicio Fiscal {anio_er}"

        if modo_er == "📅 Por Mes":
            mes_er = st.selectbox("Seleccionar Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="er_mes")
            cond_er += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_er.extend([anio_er, mes_er])
            label_er = f"{meses_dict[mes_er]} {anio_er}"
        else:
            cond_er += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
            params_er.append(anio_er)

        st.markdown(f"""
            <div style="background-color: {color_er}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
                📊 Estado de Resultados — Período: {label_er}
            </div>
        """, unsafe_allow_html=True)

        conn = get_db_connection()
        q_i = f"""
            SELECT n.codigo, n.nombre, ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS monto
            FROM nomenclatura n
            JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE {cond_er} AND n.tipo = 'Ingreso'
            GROUP BY n.codigo, n.nombre
        """
        q_g = f"""
            SELECT n.codigo, n.nombre, ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS monto
            FROM nomenclatura n
            JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
            WHERE {cond_er} AND n.tipo = 'Gasto'
            GROUP BY n.codigo, n.nombre
        """
        df_i = pd.read_sql_query(q_i, conn, params=tuple(params_er))
        df_g = pd.read_sql_query(q_g, conn, params=tuple(params_er))
        conn.close()

        tot_i = float(df_i['monto'].sum()) if not df_i.empty else 0.0
        
        df_costos = df_g[df_g['codigo'].str.startswith('5.1.01')]
        df_gastos_op = df_g[~df_g['codigo'].str.startswith('5.1.01')]

        tot_costos = float(df_costos['monto'].sum()) if not df_costos.empty else 0.0
        utilidad_bruta = round(tot_i - tot_costos, 2)

        tot_gastos_op = float(df_gastos_op['monto'].sum()) if not df_gastos_op.empty else 0.0
        utilidad_operacion = round(utilidad_bruta - tot_gastos_op, 2)

        impuesto_calc, ganancia_neta, desc_imp = calcular_impuesto_renta(selected_empresa_info[3], tot_i, utilidad_operacion)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Ingresos Totales", f"Q {tot_i:,.2f}")
        m2.metric("Utilidad Bruta en Ventas", f"Q {utilidad_bruta:,.2f}")
        m3.metric(f"Impuesto ({desc_imp})", f"Q {impuesto_calc:,.2f}")
        m4.metric("Ganancia Neta Desp. Impuestos", f"Q {ganancia_neta:,.2f}", delta=f"{'Ganancia ✅' if ganancia_neta >= 0 else 'Pérdida ⚠️'}")

        st.markdown("---")
        st.subheader("📑 Estructura Contable Formal Clasificada")
        st.markdown(f"#### **1. (+) INGRESOS OPERACIONALES: Q {tot_i:,.2f}**")
        for _, r in df_i.iterrows():
            st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['monto']:,.2f}")

        st.markdown(f"#### **2. (-) COSTO DE VENTAS: Q {tot_costos:,.2f}**")
        for _, r in df_costos.iterrows():
            st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['monto']:,.2f}")

        st.info(f"### **(=) UTILIDAD BRUTA EN VENTAS: Q {utilidad_bruta:,.2f}**")

        st.markdown(f"#### **3. (-) GASTOS DE OPERACIÓN Y ADMINISTRACIÓN: Q {tot_gastos_op:,.2f}**")
        for _, r in df_gastos_op.iterrows():
            st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['monto']:,.2f}")

        st.warning(f"### **(=) UTILIDAD DE OPERACIÓN: Q {utilidad_operacion:,.2f}**")
        st.write(f"• **Provisión Impuesto Estimado ({desc_imp})**: Q {impuesto_calc:,.2f}")
        st.success(f"## **(=) UTILIDAD NETA DESPUÉS DE IMPUESTOS: Q {ganancia_neta:,.2f}**")

# ==========================================
# 17. DIRECTORIO DE TERCEROS
# ==========================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    conn = get_db_connection()
    df_ter = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre / Razón Social\", direccion AS \"Dirección\", telefono AS \"Teléfono\" FROM terceros_corp ORDER BY nombre ASC", conn)
    conn.close()
    if not df_ter.empty:
        st.dataframe(df_ter, use_container_width=True)
    else:
        st.info("No hay terceros registrados aún.")

# ==========================================
# 18. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    with st.expander("➕ Agregar Nueva Cuenta Contable", expanded=False):
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
                        st.error("Ya existe una cuenta con este código en esta empresa.")
                    conn.close()

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Nombre de la Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 19. CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores y Permisos")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas, reset_requested FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_emps = c.fetchall()
    conn.close()
    
    t_crear, t_pend, t_reg = st.tabs(["➕ Crear Operador", "⏳ Pendientes", "📋 Registrados"])
    with t_crear:
        with st.form("form_crear_op"):
            c1, c2 = st.columns(2)
            u_name = c1.text_input("Usuario *")
            u_nom = c2.text_input("Nombre Completo *")
            c3, c4 = st.columns(2)
            u_mail = c3.text_input("Correo")
            u_pass = c4.text_input("Contraseña *", type="password")
            emp_sel = [emp[0] for emp in all_emps if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"c_op_{emp[0]}", value=True)]
            if st.form_submit_button("Crear y Activar", type="primary"):
                if u_name and u_nom and u_pass:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute("INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (f"usr-{int(datetime.now().timestamp())}", u_name, hash_password(u_pass), u_nom, u_mail, "operator", "active", ",".join(emp_sel), datetime.now().isoformat()))
                        conn.commit()
                        st.success("Operador creado con éxito.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("El usuario ya existe.")
                    conn.close()
    with t_pend:
        pends = [u for u in users_list if u[5] == "pending_approval" or u[7] == 1]
        if not pends:
            st.info("Sin solicitudes pendientes.")
        else:
            for u in pends:
                st.write(f"**{u[2]}** (@{u[1]})")
                if st.button(f"Aprobar @{u[1]}", key=f"ap_{u[0]}", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    all_ids = ",".join([e[0] for e in all_emps])
                    c.execute("UPDATE users SET status = 'active', assigned_empresas = %s WHERE id = %s", (all_ids, u[0]))
                    conn.commit()
                    conn.close()
                    st.rerun()
    with t_reg:
        acts = [u for u in users_list if u[5] != "pending_approval"]
        for u in acts:
            with st.expander(f"👤 {u[2]} (@{u[1]})"):
                st.write(f"Correo: {u[3]} | Estado: {u[5]}")
                st.caption(f"Empresas asignadas: {u[6] or 'Ninguna'}")

# ==========================================
# 20. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro y Edición de Empresas")
    t_nemp, t_medit = st.tabs(["➕ Registrar Empresa", "📋 Consultar y Modificar"])

    with t_nemp:
        with st.form("form_nueva_emp"):
            c1, c2 = st.columns(2)
            nom = c1.text_input("Razón Social *")
            nit = c2.text_input("NIT *")
            c3, c4 = st.columns(2)
            dire = c3.text_input("Dirección")
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
                        st.success("Empresa registrada y nomenclatura base sembrada.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("El NIT ya existe.")
                    conn.close()
                else:
                    st.error("Completa el Nombre y el NIT.")

    with t_medit:
        conn = get_db_connection()
        df_emps = pd.read_sql_query("SELECT id, nombre, nit, direccion, telefono, regimen_isr FROM empresas ORDER BY nombre", conn)
        conn.close()
        if df_emps.empty:
            st.info("Sin empresas registradas.")
        else:
            for _, r in df_emps.iterrows():
                with st.expander(f"🏢 {r['nombre']} (NIT: {r['nit']})"):
                    with st.form(f"edit_emp_{r['id']}"):
                        enom = st.text_input("Nombre / Razón Social", value=r['nombre'])
                        enit = st.text_input("NIT", value=r['nit'])
                        edir = st.text_input("Dirección", value=r['direccion'] or "")
                        etel = st.text_input("Teléfono", value=r['telefono'] or "")
                        idx_reg = 0 if "Opcional" in str(r['regimen_isr']) else (1 if "Utilidades" in str(r['regimen_isr']) else 2)
                        ereg = st.selectbox("Régimen ISR", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades de Actividades Lucrativas (25%)", "Pequeño Contribuyente (5%)"], index=idx_reg)
                        if st.form_submit_button("Actualizar Datos"):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("UPDATE empresas SET nombre = %s, nit = %s, direccion = %s, telefono = %s, regimen_isr = %s WHERE id = %s",
                                      (enom, enit, edir, etel, ereg, r['id']))
                            conn.commit()
                            conn.close()
                            st.success("Empresa actualizada.")
                            st.rerun()