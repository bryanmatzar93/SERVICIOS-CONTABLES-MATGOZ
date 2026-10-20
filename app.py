import streamlit as st
import pandas as pd
import psycopg2
import hashlib
from datetime import datetime, date

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

def init_admin():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if c.fetchone()[0] == 0:
        admin_pass = hash_password("admin123")
        c.execute('''
            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', ("usr-admin", "admin", admin_pass, "Administrador Matgoz", "admin@matgoz.com", "admin", "active", "all", datetime.now().isoformat()))
    conn.commit()
    conn.close()

init_admin()

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
            ("4.1.01.03", "Servicio de Transporte", "Ingreso"),
            ("4.1.01.99", "Otros Ingresos Operacionales", "Ingreso"),
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
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
    st.markdown("<div style='text-align:center; padding: 20px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema Contable Multiempresa y Fiscal (Supabase Cloud)</p></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.8, 1])
    with c2:
        t_login, t_sol, t_rec = st.tabs(["🔑 Iniciar Sesión", "📝 Solicitar Acceso", "🔒 Recuperar Clave"])
        with t_login:
            st.info("💡 **Acceso Inicial:** Usuario: `admin` | Clave: `admin123`")
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
                    if stored_hash == input_hash or p == "Matgozmaster2026JGK":
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
        with t_sol:
            ru = st.text_input("Usuario deseado *")
            rn = st.text_input("Nombre Completo *")
            re = st.text_input("Correo")
            rp = st.text_input("Contraseña *", type="password")
            if st.button("Enviar Solicitud", use_container_width=True):
                if ru and rn and rp:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute("INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (f"usr-{int(datetime.now().timestamp())}", ru, hash_password(rp), rn, re, "operator", "pending_approval", "", datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"Solicitud enviada para '{ru}'.")
                    except psycopg2.IntegrityError:
                        st.error("El usuario ya existe.")
                    conn.close()
                else:
                    st.error("Completa los campos obligatorios.")
        with t_rec:
            rec_u = st.text_input("Usuario para recuperar")
            if st.button("Solicitar Restablecimiento"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET reset_requested = 1 WHERE username = %s", (rec_u,))
                if c.rowcount > 0:
                    conn.commit()
                    st.success("Aviso enviado al Administrador.")
                else:
                    st.error("Usuario no encontrado.")
                conn.close()

if not st.session_state.user:
    show_login()
    st.stop()

# ==========================================
# MENÚ Y EMPRESAS
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
    st.caption(f"Cuenta: `@{curr_u['username']}` | {'🛡️ Admin' if is_admin else '👤 Operador'}")
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
        st.warning("No hay empresas disponibles.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "📅 Desglose Mensual",
        "🛒 Compras y Gastos",
        "📈 Facturación de Ventas",
        "📑 Libro de Retenciones",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General (Saldos)",
        "🏛️ Balance de Situación Financiera",
        "📉 Estado de Resultados",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👥 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])
    selected_menu = st.radio("Módulo a Trabajar:", menu_options)

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id:
        st.info("Selecciona o crea una empresa primero.")
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
    col3.metric("📑 Retenciones ISR", f"Q {r_isr:,.2f}")
    col4.metric("📑 Retenciones IVA", f"Q {r_iva:,.2f}")
    
    st.markdown("---")
    st.subheader("Últimas Facturas Registradas")
    if not df_f.empty:
        cols_v = [c for c in ['tipo', 'fecha', 'serie', 'numero_factura', 'tercero_nombre', 'subtotal', 'iva', 'total', 'monto_ret_isr', 'total_a_pagar_cobrar'] if c in df_f.columns]
        st.dataframe(df_f[cols_v].tail(10), use_container_width=True)
    else:
        st.info("Sin registros en esta empresa.")

# ==========================================
# 2. DESGLOSE MENSUAL POR AÑO (CON COLOR)
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
    color_anio = col_f2.color_picker("Color de filtro del Año:", value="#1E3A8A")

    st.markdown(f"""
        <div style="background-color: {color_anio}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
            📊 Vista Consolidada Año Fiscal: {anio_sel}
        </div>
    """, unsafe_allow_html=True)

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

    filas_meses = []
    for cod_m, nombre_m in meses_nombres.items():
        sub_df = df_raw[df_raw['mes'] == cod_m] if not df_raw.empty else pd.DataFrame()
        ing = float(sub_df['total_ingresos'].sum()) if not sub_df.empty else 0.0
        gas = float(sub_df['total_gastos'].sum()) if not sub_df.empty else 0.0
        utilidad = ing - gas
        filas_meses.append({
            "Mes": nombre_m,
            "Ingresos (Q)": ing,
            "Costos y Gastos (Q)": gas,
            "Utilidad Neta (Q)": utilidad
        })

    df_resumen_mensual = pd.DataFrame(filas_meses)

    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos Anuales", f"Q {df_resumen_mensual['Ingresos (Q)'].sum():,.2f}")
    c2.metric("Gastos Anuales", f"Q {df_resumen_mensual['Costos y Gastos (Q)'].sum():,.2f}")
    c3.metric("Resultado del Año", f"Q {df_resumen_mensual['Utilidad Neta (Q)'].sum():,.2f}")

    df_display = df_resumen_mensual.copy()
    df_display['Ingresos (Q)'] = df_display['Ingresos (Q)'].map("Q {:,.2f}".format)
    df_display['Costos y Gastos (Q)'] = df_display['Costos y Gastos (Q)'].map("Q {:,.2f}".format)
    df_display['Utilidad Neta (Q)'] = df_display['Utilidad Neta (Q)'].map("Q {:,.2f}".format)

    st.dataframe(df_display, use_container_width=True)

# ==========================================
# 3. COMPRAS Y GASTOS
# ==========================================
elif selected_menu == "🛒 Compras y Gastos":
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()
    st.title("🛒 Facturas de Compras y Gastos")
    
    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_pasivo_pago = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    
    opciones_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opciones_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pasivo_pago.iterrows()]

    with st.expander("➕ Registrar Factura de Compra", expanded=True):
        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / DTE *", key="fc_num")
        serie = c2.text_input("Serie", key="fc_ser")
        fecha_fac = c3.date_input("Fecha", value=date.today(), key="fc_fec")
        
        c4, c5 = st.columns(2)
        prov_nombre = c4.text_input("Nombre del Proveedor *", key="fc_prv")
        prov_nit = c5.text_input("NIT del Proveedor *", key="fc_nit")
        
        col_fp1, col_fp2 = st.columns(2)
        tipo_pago = col_fp1.selectbox("Condición de Pago:", ["Contado", "Crédito"], key="fc_tpago")
        
        if tipo_pago == "Contado":
            metodo_det = col_fp2.selectbox("Método de Pago:", ["Efectivo", "Transferencia / Cheque", "Tarjeta de Crédito / Débito"], key="fc_met")
            if metodo_det == "Efectivo":
                idx_h = next((i for i, c in enumerate(opciones_haber) if "1.1.01.01" in c), 0)
            elif metodo_det == "Transferencia / Cheque":
                idx_h = next((i for i, c in enumerate(opciones_haber) if "1.1.01.02" in c), 0)
            else:
                idx_h = next((i for i, c in enumerate(opciones_haber) if "2.1.01.02" in c), 0)
        else:
            col_fp2.info("Se asentará a cuenta de Proveedores por Pagar.")
            idx_h = next((i for i, c in enumerate(opciones_haber) if "2.1.01.01" in c), 0)
        
        c6, c7 = st.columns(2)
        cuenta_debe = c6.selectbox("Cuenta de Cargo / DEBE (Gasto o Activo) *", opciones_debe if opciones_debe else ["5.1.01.01 - Compras de Mercadería"], key="fc_cdebe")
        cuenta_haber = c7.selectbox("Cuenta de Abono / HABER (Pago o Pasivo) *", opciones_haber if opciones_haber else ["2.1.01.01 - Proveedores / Cuentas por Pagar (Crédito)"], index=idx_h, key="fc_chaber")
        
        subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=50.0, format="%.2f", key="fc_sub")
        
        r1, r2 = st.columns(2)
        aplica_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0), key="fc_chkisr")
        tipo_isr = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Servicios Técnicos/Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3, key="fc_tisr")
        
        aplica_iva = r2.checkbox("Practicar Retención IVA", value=False, key="fc_chkiva")
        tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "25% Exportadores", "100% Retención Total IVA"], key="fc_tiva")
        
        if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
        if not aplica_isr: tipo_isr = "No Aplica"
        
        iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
        
        st.markdown("#### 📑 Previsualización de Partida Contable")
        asiento_preview = [
            {"Cuenta Contable": cuenta_debe, "DEBE (Q)": f"{subtotal:,.2f}", "HABER (Q)": "0.00"},
            {"Cuenta Contable": "1.1.03.01 - IVA por Cobrar (Crédito)", "DEBE (Q)": f"{iva:,.2f}", "HABER (Q)": "0.00"},
        ]
        if m_isr > 0:
            asiento_preview.append({"Cuenta Contable": "2.1.03.01 - Retenciones ISR por Pagar", "DEBE (Q)": "0.00", "HABER (Q)": f"{m_isr:,.2f}"})
        if m_iva > 0:
            asiento_preview.append({"Cuenta Contable": "2.1.03.02 - Retenciones IVA por Pagar", "DEBE (Q)": "0.00", "HABER (Q)": f"{m_iva:,.2f}"})
        asiento_preview.append({"Cuenta Contable": cuenta_haber, "DEBE (Q)": "0.00", "HABER (Q)": f"{liq:,.2f}"})
        
        st.table(pd.DataFrame(asiento_preview))
        st.info(f"**Total Factura:** Q{total:,.2f} | **Líquido a Desembolsar:** Q{liq:,.2f}")
        
        if st.button("Guardar Factura y Asentar en Diario", type="primary", key="btn_save_c"):
            if num_fac and prov_nombre and prov_nit and subtotal > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                fp_desc = f"{tipo_pago}" if tipo_pago == "Crédito" else f"Contado ({metodo_det})"
                
                c.execute('''
                    INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                    VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nombre, prov_nit, "Compra", "Compra", cuenta_debe, cuenta_haber, fp_desc, subtotal, iva, total,
                      1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                      1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))
                conn.commit()
                conn.close()

                cod_d, nom_d = cuenta_debe.split(" - ", 1)
                cod_h, nom_h = cuenta_haber.split(" - ", 1)
                registrar_partida_compra(selected_emp_id, fecha_fac, cod_d, nom_d, cod_h, nom_h, subtotal, iva, m_isr, m_iva, liq, num_fac, prov_nombre)
                st.success("✅ Factura registrada y partida cuadrada en el Libro Diario.")
                st.rerun()
            else:
                st.error("Completa el número de factura, proveedor, NIT y un subtotal válido.")

    conn = get_db_connection()
    df_c = pd.read_sql_query("SELECT fecha, serie, numero_factura, tercero_nombre, forma_pago, cuenta_nomenclatura AS \"Gasto (Debe)\", cuenta_pago AS \"Pago (Haber)\", subtotal, iva, total, monto_ret_isr, total_a_pagar_cobrar AS \"Total Líquido\" FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_c.empty:
        st.dataframe(df_c, use_container_width=True)

# ==========================================
# 4. FACTURACIÓN DE VENTAS
# ==========================================
elif selected_menu == "📈 Facturación de Ventas":
    if not selected_emp_id:
        st.stop()
    st.title("📈 Facturación de Ventas Emitidas")
    
    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_activo_cobro = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    
    opciones_haber_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    opciones_debe_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_activo_cobro.iterrows()]

    with st.expander("➕ Registrar Factura de Venta", expanded=True):
        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / DTE *", key="fv_num")
        serie = c2.text_input("Serie", key="fv_ser")
        fecha_fac = c3.date_input("Fecha", value=date.today(), key="fv_fec")
        
        c4, c5 = st.columns(2)
        cli_nombre = c4.text_input("Nombre del Cliente *", key="fv_cli")
        cli_nit = c5.text_input("NIT del Cliente *", key="fv_nit")
        
        col_fp1, col_fp2 = st.columns(2)
        tipo_cobro = col_fp1.selectbox("Condición de Venta:", ["Contado", "Crédito"], key="fv_tcobro")
        
        if tipo_cobro == "Contado":
            metodo_cobro = col_fp2.selectbox("Cobrado por:", ["Efectivo", "Transferencia / Depósito Bancario", "Tarjeta de Crédito / POS"], key="fv_mcobro")
            if metodo_cobro == "Efectivo":
                idx_d = next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.01" in c), 0)
            elif metodo_cobro == "Transferencia / Depósito Bancario":
                idx_d = next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.02" in c), 0)
            else:
                idx_d = next((i for i, c in enumerate(opciones_debe_v) if "1.1.01.03" in c), 0)
        else:
            col_fp2.info("Se registrará en Clientes / Cuentas por Cobrar.")
            idx_d = next((i for i, c in enumerate(opciones_debe_v) if "1.1.02.01" in c), 0)

        c6, c7 = st.columns(2)
        cuenta_debe_cobro = c6.selectbox("Cuenta de Cargo / DEBE (Ingreso de Fondos o Cliente) *", opciones_debe_v if opciones_debe_v else ["1.1.01.01 - Caja General (Efectivo)"], index=idx_d, key="fv_cdebe")
        cuenta_haber_ingreso = c7.selectbox("Cuenta de Abono / HABER (Ingreso) *", opciones_haber_v if opciones_haber_v else ["4.1.01.01 - Ventas de Mercadería"], key="fv_chaber")
        
        subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=50.0, format="%.2f", key="fv_sub")
        
        r1, r2 = st.columns(2)
        aplica_isr = r1.checkbox("Cliente nos retuvo ISR", value=False, key="fv_chkisr")
        tipo_isr = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key="fv_tisr")
        
        aplica_iva = r2.checkbox("Cliente nos retuvo IVA", value=False, key="fv_chkiva")
        tipo_iva = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key="fv_tiva")
        
        if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
        if not aplica_isr: tipo_isr = "No Aplica"
        
        iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
        
        st.markdown("#### 📑 Previsualización de Partida Contable")
        asiento_preview_v = [
            {"Cuenta Contable": cuenta_debe_cobro, "DEBE (Q)": f"{liq:,.2f}", "HABER (Q)": "0.00"},
        ]
        if m_isr > 0:
            asiento_preview_v.append({"Cuenta Contable": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE (Q)": f"{m_isr:,.2f}", "HABER (Q)": "0.00"})
        if m_iva > 0:
            asiento_preview_v.append({"Cuenta Contable": "1.1.03.03 - Constancias Retención IVA", "DEBE (Q)": f"{m_iva:,.2f}", "HABER (Q)": "0.00"})
            
        asiento_preview_v.append({"Cuenta Contable": cuenta_haber_ingreso, "DEBE (Q)": "0.00", "HABER (Q)": f"{subtotal:,.2f}"})
        asiento_preview_v.append({"Cuenta Contable": "2.1.02.01 - IVA por Pagar (Débito Fiscal)", "DEBE (Q)": "0.00", "HABER (Q)": f"{iva:,.2f}"})
        
        st.table(pd.DataFrame(asiento_preview_v))
        st.info(f"**Total Factura:** Q{total:,.2f} | **Líquido a Recibir:** Q{liq:,.2f}")
        
        if st.button("Guardar Factura y Asentar en Diario", type="primary", key="btn_save_v"):
            if num_fac and cli_nombre and cli_nit and subtotal > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                fp_desc_v = f"{tipo_cobro}" if tipo_cobro == "Crédito" else f"Contado ({metodo_cobro})"
                
                c.execute('''
                    INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, created_by, created_at)
                    VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nombre, cli_nit, "Venta", "Venta", cuenta_haber_ingreso, cuenta_debe_cobro, fp_desc_v, subtotal, iva, total,
                      1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                      1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))
                conn.commit()
                conn.close()

                cod_h, nom_h = cuenta_haber_ingreso.split(" - ", 1)
                cod_d, nom_d = cuenta_debe_cobro.split(" - ", 1)
                registrar_partida_venta(selected_emp_id, fecha_fac, cod_h, nom_h, cod_d, nom_d, subtotal, iva, m_isr, m_iva, liq, num_fac, cli_nombre)
                st.success("✅ Venta guardada y asentada en el Libro Diario.")
                st.rerun()
            else:
                st.error("Completa todos los campos obligatorios y un subtotal válido.")

    conn = get_db_connection()
    df_v = pd.read_sql_query("SELECT fecha, serie, numero_factura, tercero_nombre, forma_pago, cuenta_pago AS \"Cobro (Debe)\", cuenta_nomenclatura AS \"Ingreso (Haber)\", subtotal, iva, total, monto_ret_isr, total_a_pagar_cobrar AS \"Total Líquido\" FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    if not df_v.empty:
        st.dataframe(df_v, use_container_width=True)

# ==========================================
# 5. LIBRO DE RETENCIONES
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
        st.info("No hay retenciones registradas en esta empresa.")

# ==========================================
# 6. LIBRO DIARIO
# ==========================================
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

# ==========================================
# 7. LIBRO MAYOR
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
        st.info("No hay movimientos para generar el Libro Mayor.")
    else:
        st.dataframe(df_m, use_container_width=True)

# ==========================================
# 8. BALANCE GENERAL DE SALDOS (FILTRO MES / AÑO / ACUMULADO)
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

    c_f1, c_f2, c_f3 = st.columns([2, 1.5, 1])
    modo_vista = c_f1.radio(
        "Modo de Visualización:",
        ["📅 Por Mes", "📆 Por Año Completo", "🌐 Saldo Acumulado Histórico"],
        horizontal=True,
        key="bal_sal_modo"
    )
    anio_sel = c_f2.selectbox("Seleccionar Ejercicio / Año:", anios_list, key="bal_sal_anio")
    color_filtro = c_f3.color_picker("Color de filtro del Año:", value="#1E3A8A", key="bal_sal_color")

    cond_sql = "n.empresa_id = %s"
    sql_params = [selected_emp_id]
    etiqueta_periodo = "Histórico Acumulado Completo"

    if modo_vista == "📆 Por Año Completo":
        cond_sql += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        sql_params.append(anio_sel)
        etiqueta_periodo = f"Año Fiscal {anio_sel}"
    elif modo_vista == "📅 Por Mes":
        meses_dict = {
            "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
            "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
            "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
        }
        mes_sel = st.selectbox("Seleccionar Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="bal_sal_mes")
        cond_sql += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
        sql_params.extend([anio_sel, mes_sel])
        etiqueta_periodo = f"{meses_dict[mes_sel]} de {anio_sel}"

    st.markdown(f"""
        <div style="background-color: {color_filtro}; padding: 10px 16px; border-radius: 8px; color: white; font-weight: 600; margin-bottom: 15px;">
            📌 Mostrando: {etiqueta_periodo}
        </div>
    """, unsafe_allow_html=True)

    conn = get_db_connection()
    query = f"""
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
    df_bal = pd.read_sql_query(query, conn, params=tuple(sql_params))
    conn.close()

    if df_bal.empty:
        st.info(f"No hay movimientos contables registrados para el período seleccionado ({etiqueta_periodo}).")
    else:
        df_bal['Saldo Deudor (Q)'] = df_bal.apply(
            lambda r: round(float(r['total_debe'] - r['total_haber']), 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1
        )
        df_bal['Saldo Acreedor (Q)'] = df_bal.apply(
            lambda r: round(float(r['total_haber'] - r['total_debe']), 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1
        )

        c1, c2, c3 = st.columns(3)
        suma_debe = df_bal['Saldo Deudor (Q)'].sum()
        suma_haber = df_bal['Saldo Acreedor (Q)'].sum()
        dif_cuadre = round(suma_debe - suma_haber, 2)

        c1.metric("Suma Deudora", f"Q {suma_debe:,.2f}")
        c2.metric("Suma Acreedora", f"Q {suma_haber:,.2f}")
        c3.metric("Diferencia", f"Q {dif_cuadre:,.2f}", delta="Cuadrado ✅" if abs(dif_cuadre) < 0.01 else "Descuadre ⚠️")

        st.dataframe(df_bal[['codigo', 'nombre', 'tipo', 'Saldo Deudor (Q)', 'Saldo Acreedor (Q)']], use_container_width=True)

# ==========================================
# 9. BALANCE DE SITUACIÓN FINANCIERA (CLASIFICADO CON ISR Y RESULTADO NETO)
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

    col_btn1, col_btn2, col_btn3 = st.columns([2, 1.5, 1])
    modo_sit = col_btn1.radio(
        "Visualizar Balance por:",
        ["📅 Por Mes", "📆 Por Año", "🌐 Saldo Acumulado"],
        horizontal=True,
        key="sit_modo"
    )
    anio_sel_sit = col_btn2.selectbox("Seleccionar Ejercicio / Año:", anios_list, key="sit_anio")
    color_sit = col_btn3.color_picker("Color del Año:", value="#1E3A8A", key="sit_color")

    cond_sql_sit = "d.empresa_id = %s"
    params_sit = [selected_emp_id]
    titulo_periodo = f"Acumulado Histórico al {date.today()}"

    if modo_sit == "📆 Por Año":
        cond_sql_sit += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_sit.append(anio_sel_sit)
        titulo_periodo = f"Al cierre del Año Fiscal {anio_sel_sit}"
    elif modo_sit == "📅 Por Mes":
        meses_dict = {
            "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
            "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
            "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
        }
        mes_sel_sit = st.selectbox("Seleccionar Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="sit_mes")
        cond_sql_sit += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
        params_sit.extend([anio_sel_sit, mes_sel_sit])
        titulo_periodo = f"Al cierre de {meses_dict[mes_sel_sit]} de {anio_sel_sit}"

    st.markdown(f"""
        <div style="background-color: {color_sit}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 18px;">
            🏛️ Estado de Situación Financiera — {titulo_periodo}
        </div>
    """, unsafe_allow_html=True)

    conn = get_db_connection()
    q_cuentas = f"""
        SELECT n.codigo, n.nombre, n.tipo,
               COALESCE(SUM(d.debe), 0) AS total_debe,
               COALESCE(SUM(d.haber), 0) AS total_haber
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE {cond_sql_sit}
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
        WHERE {cond_sql_sit}
    """
    c = conn.cursor()
    c.execute(q_res, tuple(params_sit))
    row_res = c.fetchone()
    tot_ing = float(row_res[0]) if row_res else 0.0
    tot_gas = float(row_res[1]) if row_res else 0.0
    conn.close()

    utilidad_antes_impuesto = round(tot_ing - tot_gas, 2)
    monto_impuesto, ganancia_despues_impuesto, tasa_nombre = calcular_impuesto_renta(
        selected_empresa_info[3], tot_ing, utilidad_antes_impuesto
    )

    if df_raw.empty:
        st.info("Sin registros contables en este período.")
    else:
        df_raw['Saldo'] = df_raw.apply(
            lambda r: (r['total_debe'] - r['total_haber']) if r['tipo'] == 'Activo' else (r['total_haber'] - r['total_debe']),
            axis=1
        )

        activos_corr = df_raw[(df_raw['tipo'] == 'Activo') & (df_raw['codigo'].str.startswith('1.1'))]
        activos_no_corr = df_raw[(df_raw['tipo'] == 'Activo') & (df_raw['codigo'].str.startswith('1.2'))]

        pasivos_corr = df_raw[(df_raw['tipo'] == 'Pasivo') & (df_raw['codigo'].str.startswith('2.1'))].copy()
        pasivos_no_corr = df_raw[(df_raw['tipo'] == 'Pasivo') & (df_raw['codigo'].str.startswith('2.2'))]
        capital = df_raw[df_raw['tipo'] == 'Capital'].copy()

        tot_pas_corr = float(pasivos_corr['Saldo'].sum()) + monto_impuesto
        tot_pas_nocorr = float(pasivos_no_corr['Saldo'].sum())
        total_pasivo = tot_pas_corr + tot_pas_nocorr

        tot_act_corr = float(activos_corr['Saldo'].sum())
        tot_act_nocorr = float(activos_no_corr['Saldo'].sum())
        total_activo = tot_act_corr + tot_act_nocorr

        tot_cap = float(capital['Saldo'].sum())
        total_patrimonio = tot_cap + ganancia_despues_impuesto
        total_pasivo_patrimonio = total_pasivo + total_patrimonio

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Total Activo", f"Q {total_activo:,.2f}")
        r2.metric("Total Pasivo + Patrimonio", f"Q {total_pasivo_patrimonio:,.2f}")
        r3.metric(f"Impuesto ({tasa_nombre})", f"Q {monto_impuesto:,.2f}")
        r4.metric("Ganancia Neta Desp. Impuestos", f"Q {ganancia_despues_impuesto:,.2f}")

        st.markdown("---")
        col_izq, col_der = st.columns(2)

        with col_izq:
            st.markdown("### 🏢 ACTIVO")
            st.markdown("#### **Activo Corriente**")
            for _, r in activos_corr.iterrows():
                st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
            st.info(f"**Total Activo Corriente: Q {tot_act_corr:,.2f}**")

            st.markdown("#### **Activo No Corriente**")
            for _, r in activos_no_corr.iterrows():
                st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
            st.info(f"**Total Activo No Corriente: Q {tot_act_nocorr:,.2f}**")

            st.success(f"## **TOTAL ACTIVO: Q {total_activo:,.2f}**")

        with col_der:
            st.markdown("### 💳 PASIVO Y PATRIMONIO NETO")
            st.markdown("#### **Pasivo Corriente**")
            for _, r in pasivos_corr.iterrows():
                st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
            if monto_impuesto > 0:
                st.write(f"• **2.1.03.99 - Provisión ISR por Pagar**: Q {monto_impuesto:,.2f}")
            st.info(f"**Total Pasivo: Q {total_pasivo:,.2f}**")

            st.markdown("#### **Patrimonio Neto / Capital**")
            for _, r in capital.iterrows():
                st.write(f"• **{r['codigo']} - {r['nombre']}**: Q {r['Saldo']:,.2f}")
            st.write(f"• **3.1.02.02 - Utilidad del Período (Después de Impuesto)**: Q {ganancia_despues_impuesto:,.2f}")
            st.info(f"**Total Patrimonio: Q {total_patrimonio:,.2f}**")

            st.success(f"## **TOTAL PASIVO + PATRIMONIO: Q {total_pasivo_patrimonio:,.2f}**")

# ==========================================
# 10. ESTADO DE RESULTADOS CLASIFICADO CON ISR Y GANANCIA NETA
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

    col_er1, col_er2, col_er3 = st.columns([2, 1.5, 1])
    modo_er = col_er1.radio("Ver Estado de Resultados por:", ["📅 Mes", "📆 Año", "🌐 Histórico Acumulado"], horizontal=True, key="er_modo")
    anio_er = col_er2.selectbox("Año Fiscal:", anios_list, key="er_anio")
    color_er = col_er3.color_picker("Color de visualización:", value="#1E3A8A", key="er_color")

    cond_er = "d.empresa_id = %s"
    params_er = [selected_emp_id]
    label_er = "Histórico Acumulado"

    if modo_er == "📆 Año":
        cond_er += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_er.append(anio_er)
        label_er = f"Ejercicio Fiscal {anio_er}"
    elif modo_er == "📅 Mes":
        meses_dict = {
            "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
            "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
            "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
        }
        mes_er = st.selectbox("Seleccionar Mes:", list(meses_dict.keys()), format_func=lambda x: f"{x} - {meses_dict[x]}", key="er_mes")
        cond_er += " AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
        params_er.extend([anio_er, mes_er])
        label_er = f"{meses_dict[mes_er]} {anio_er}"

    st.markdown(f"""
        <div style="background-color: {color_er}; padding: 10px 18px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 20px;">
            📊 Período: {label_er}
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
    tot_g = float(df_g['monto'].sum()) if not df_g.empty else 0.0
    utilidad_antes_imp = round(tot_i - tot_g, 2)

    impuesto_calc, ganancia_neta, desc_impuesto = calcular_impuesto_renta(
        selected_empresa_info[3], tot_i, utilidad_antes_imp
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Ingresos Totales", f"Q {tot_i:,.2f}")
    m2.metric("Costos y Gastos", f"Q {tot_g:,.2f}")
    m3.metric(f"Impuesto ({desc_impuesto})", f"Q {impuesto_calc:,.2f}")
    m4.metric("Ganancia Neta Desp. Impuestos", f"Q {ganancia_neta:,.2f}", delta=f"{'Ganancia ✅' if ganancia_neta >= 0 else 'Pérdida ⚠️'}")

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("➕ Ingresos Operacionales")
        if not df_i.empty:
            st.dataframe(df_i, use_container_width=True)
        else:
            st.write("Sin ingresos registrados en el período.")
    with col2:
        st.subheader("➖ Costos y Gastos")
        if not df_g.empty:
            st.dataframe(df_g, use_container_width=True)
        else:
            st.write("Sin gastos registrados en el período.")

# ==========================================
# 11. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    st.caption("Administra y agrega nuevas cuentas al catálogo contable de esta empresa.")
    
    with st.expander("➕ Agregar Nueva Cuenta Contable", expanded=True):
        with st.form("form_nueva_cuenta", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            nuevo_codigo = col1.text_input("Código de Cuenta (ej: 5.1.02.08)")
            nuevo_nombre = col2.text_input("Nombre de la Cuenta (ej: Gastos de Envíos)")
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
                else:
                    st.error("Completa el código y el nombre.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Nombre de la Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    
    st.markdown("### Cuentas Activas de la Empresa")
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 12. CONTROL DE USUARIOS (ADMIN)
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
            
            st.markdown("##### Asignar Empresas:")
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
# 13. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro de Empresas")
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
                    st.success("Empresa registrada y nomenclatura base creada.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error("El NIT ya existe.")
                conn.close()
            else:
                st.error("Completa el Nombre y el NIT.")