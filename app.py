import streamlit as st
import pandas as pd
import sqlite3
import hashlib
import io
from datetime import datetime, date

# ==========================================
# CONFIGURACIÓN GENERAL
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
# INICIALIZACIÓN DE TABLAS Y MIGRACIONES
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

    # Directorio Terceros (Clientes y Proveedores)
    c.execute('''CREATE TABLE IF NOT EXISTS terceros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id TEXT NOT NULL,
        tipo TEXT NOT NULL, -- 'CLIENTE', 'PROVEEDOR', 'AMBOS'
        nit TEXT NOT NULL,
        nombre TEXT NOT NULL,
        direccion TEXT,
        telefono TEXT,
        email TEXT,
        dias_credito INTEGER DEFAULT 0,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id),
        UNIQUE(empresa_id, nit)
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

    # Catálogo Nomenclatura
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

    # Inventario (Kárdex y Productos)
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

    # Pagos y Abonos (Cartera CXC / CXP)
    c.execute('''CREATE TABLE IF NOT EXISTS movimientos_cartera (
        id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id TEXT NOT NULL, factura_id TEXT NOT NULL,
        fecha TEXT NOT NULL, tipo TEXT NOT NULL, monto REAL NOT NULL, metodo_pago TEXT,
        cuenta_afectada TEXT, observaciones TEXT, created_at TEXT
    )''')

    # Migración de columnas por si existen bases de datos previas
    c.execute("PRAGMA table_info(facturas)")
    cols = [col[1] for col in c.fetchall()]
    if "saldo_pendiente" not in cols:
        c.execute("ALTER TABLE facturas ADD COLUMN saldo_pendiente REAL DEFAULT 0")
    if "estado_pago" not in cols:
        c.execute("ALTER TABLE facturas ADD COLUMN estado_pago TEXT DEFAULT 'PAGADO'")
    if "cuenta_pago" not in cols:
        c.execute("ALTER TABLE facturas ADD COLUMN cuenta_pago TEXT DEFAULT 'Sin asignar'")
    if "forma_pago" not in cols:
        c.execute("ALTER TABLE facturas ADD COLUMN forma_pago TEXT DEFAULT 'Contado'")

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
# MANEJO DE SESIÓN Y AUTENTICACIÓN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP y Contabilidad Multiempresa</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.6, 1])
    with c2:
        st.info("💡 **Acceso:** Usuario: `admin` | Clave: `admin123`")
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
                else:
                    st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                    st.rerun()
            else:
                st.error("Credenciales incorrectas.")

if not st.session_state.user:
    show_login()
    st.stop()

# ==========================================
# SIDEBAR: SELECCIÓN DE EMPRESA Y MENÚ
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
        st.warning("No hay empresas creadas.")

    st.divider()
    menu_options = [
        "📊 Resumen General",
        "🛒 Factura de Compras",
        "📈 Factura de Ventas",
        "👥 Directorio Terceros (Clientes/Proveedores)",
        "💳 Cuentas por Pagar (Proveedores)",
        "💰 Cuentas por Cobrar (Clientes)",
        "📦 Control de Inventarios (Kárdex)",
        "📋 Libro de Compras (SAT)",
        "📋 Libro de Ventas (SAT)",
        "📑 Libro de Retenciones",
        "📖 Libro Diario",
        "📚 Libro Mayor",
        "⚖️ Balance General",
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
    if not selected_emp_id: st.stop()
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
# 2. DIRECTORIO TERCEROS (CLIENTES Y PROVEEDORES)
# ==========================================
elif selected_menu == "👥 Directorio Terceros (Clientes/Proveedores)":
    if not selected_emp_id: st.stop()
    st.title(f"👥 Directorio de Clientes y Proveedores — {selected_empresa_info[1]}")

    with st.expander("➕ Registrar Nuevo Cliente o Proveedor", expanded=True):
        with st.form("form_tercero", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            t_tipo = col1.selectbox("Tipo de Entidad:", ["CLIENTE", "PROVEEDOR", "AMBOS"])
            t_nit = col2.text_input("NIT * (Sin guiones)")
            t_nom = col3.text_input("Razón Social / Nombre Comercial *")
            col4, col5, col6 = st.columns(3)
            t_dir = col4.text_input("Dirección")
            t_tel = col5.text_input("Teléfono")
            t_dias = col6.number_input("Días de Crédito Otorgados", min_value=0, step=15)
            
            if st.form_submit_button("Guardar Tercero", type="primary"):
                if t_nit and t_nom:
                    conn = get_db_connection()
                    try:
                        conn.execute('''INSERT INTO terceros (empresa_id, tipo, nit, nombre, direccion, telefono, dias_credito)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)''', (selected_emp_id, t_tipo, t_nit.strip().upper(), t_nom.strip(), t_dir, t_tel, t_dias))
                        conn.commit()
                        st.success(f"Tercero '{t_nom}' registrado.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un tercero registrado con este NIT.")
                    conn.close()
                else:
                    st.error("Completa el NIT y el Nombre.")

    conn = get_db_connection()
    df_t = pd.read_sql_query("SELECT tipo AS 'Tipo', nit AS 'NIT', nombre AS 'Nombre', direccion AS 'Dirección', telefono AS 'Teléfono', dias_credito AS 'Días Crédito' FROM terceros WHERE empresa_id = ? ORDER BY nombre", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_t, use_container_width=True)

# ==========================================
# 3. FACTURA DE COMPRAS
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title("🛒 Registro de Factura de Compra")

    conn = get_db_connection()
    provs = conn.execute("SELECT nit, nombre FROM terceros WHERE empresa_id = ? AND tipo IN ('PROVEEDOR', 'AMBOS')", (selected_emp_id,)).fetchall()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_haber = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    dict_prov = {f"{p[1]} (NIT: {p[0]})": p for p in provs}
    opc_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    opc_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_haber.iterrows()]

    with st.expander("➕ Ingresar Nueva Compra", expanded=True):
        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / DTE *", key="c_num")
        serie = c2.text_input("Serie", key="c_ser")
        fecha_fac = c3.date_input("Fecha", value=date.today(), key="c_fec")

        c4, c5 = st.columns(2)
        if dict_prov:
            prov_sel = c4.selectbox("Seleccionar Proveedor:", list(dict_prov.keys()), key="c_prv_sel")
            prov_nom = dict_prov[prov_sel][1]
            prov_nit = dict_prov[prov_sel][0]
        else:
            c4.warning("No hay proveedores en el directorio.")
            prov_nom = c4.text_input("Nombre Proveedor *", key="c_pnom")
            prov_nit = c5.text_input("NIT Proveedor *", key="c_pnit")

        cp1, cp2 = st.columns(2)
        cond_pago = cp1.selectbox("Condición de Pago:", ["Contado", "Crédito"], key="c_cond")
        if cond_pago == "Contado":
            metodo_p = cp2.selectbox("Método de Pago:", ["Efectivo", "Transferencia Bancaria", "Tarjeta de Crédito"], key="c_met")
            idx_h = next((i for i, c in enumerate(opc_haber) if ("1.1.01.01" in c if metodo_p=="Efectivo" else ("1.1.01.02" in c if metodo_p=="Transferencia Bancaria" else "2.1.01.02" in c))), 0)
        else:
            cp2.info("Asentado a Cuentas por Pagar (Proveedores).")
            idx_h = next((i for i, c in enumerate(opc_haber) if "2.1.01.01" in c), 0)

        cd1, cd2 = st.columns(2)
        cta_debe = cd1.selectbox("Cuenta de Cargo (DEBE) *", opc_debe if opc_debe else ["5.1.01.01 - Compras"], key="c_cdebe")
        cta_haber = cd2.selectbox("Cuenta de Abono (HABER) *", opc_haber if opc_haber else ["2.1.01.01 - Proveedores"], index=idx_h, key="c_chaber")

        subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=100.0, format="%.2f", key="c_sub")

        r1, r2 = st.columns(2)
        aplica_isr = r1.checkbox("Practicar Retención ISR", value=(subtotal >= 2800.0), key="c_rk")
        tipo_isr = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Honorarios", "No Aplica"], index=0 if subtotal >= 2800.0 else 3, key="c_rt")
        aplica_iva = r2.checkbox("Practicar Retención IVA", value=False, key="c_rvk")
        tipo_iva = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key="c_rvt")

        if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
        if not aplica_isr: tipo_isr = "No Aplica"

        iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
        
        # Asiento en pantalla
        partida = [
            {"Cuenta": cta_debe, "DEBE": f"Q {subtotal:,.2f}", "HABER": "Q 0.00"},
            {"Cuenta": "1.1.03.01 - IVA por Cobrar (Crédito)", "DEBE": f"Q {iva:,.2f}", "HABER": "Q 0.00"},
        ]
        if m_isr > 0: partida.append({"Cuenta": "2.1.03.01 - Retenciones ISR por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_isr:,.2f}"})
        if m_iva > 0: partida.append({"Cuenta": "2.1.03.02 - Retenciones IVA por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_iva:,.2f}"})
        partida.append({"Cuenta": cta_haber, "DEBE": "Q 0.00", "HABER": f"Q {liq:,.2f}"})
        st.table(pd.DataFrame(partida))

        if st.button("Guardar Factura y Asentar Contabilidad", type="primary", key="c_save"):
            if num_fac and prov_nom and prov_nit and subtotal > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                saldo = liq if cond_pago == "Crédito" else 0.0
                estado = "PENDIENTE" if cond_pago == "Crédito" else "PAGADO"
                fp_desc = cond_pago if cond_pago == "Crédito" else f"Contado ({metodo_p})"

                c.execute('''INSERT INTO facturas VALUES (?, ?, 'COMPRA', ?, ?, ?, ?, ?, 'Compra', 'Gasto',
                             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom, prov_nit,
                           cta_debe, cta_haber, fp_desc, subtotal, iva, total,
                           1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                           1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                           liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))
                
                # Partida Diario
                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                glosa = f"Factura Compra {num_fac} de {prov_nom}"

                cod_d, nom_d = cta_debe.split(" - ", 1)
                cod_h, nom_h = cta_haber.split(" - ", 1)
                c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal, glosa, num_fac, now_str))
                if iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.01', 'IVA por Cobrar', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))
                if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.03.01', 'Retenciones ISR por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.03.02', 'Retenciones IVA por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, liq, glosa, num_fac, now_str))

                conn.commit()
                conn.close()
                st.success("Factura de compra asentada en contabilidad y kárdex.")
                st.rerun()

# ==========================================
# 4. FACTURA DE VENTAS
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title("📈 Registro de Factura de Venta")

    conn = get_db_connection()
    clientes = conn.execute("SELECT nit, nombre FROM terceros WHERE empresa_id = ? AND tipo IN ('CLIENTE', 'AMBOS')", (selected_emp_id,)).fetchall()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_debe = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = ? AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    dict_cli = {f"{c[1]} (NIT: {c[0]})": c for c in clientes}
    opc_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    opc_debe_v = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_debe.iterrows()]

    with st.expander("➕ Emitir Nueva Factura de Venta", expanded=True):
        c1, c2, c3 = st.columns(3)
        num_fac = c1.text_input("Número Factura / DTE Emitido *", key="v_num")
        serie = c2.text_input("Serie", key="v_ser")
        fecha_fac = c3.date_input("Fecha", value=date.today(), key="v_fec")

        c4, c5 = st.columns(2)
        if dict_cli:
            cli_sel = c4.selectbox("Seleccionar Cliente:", list(dict_cli.keys()), key="v_cli_sel")
            cli_nom = dict_cli[cli_sel][1]
            cli_nit = dict_cli[cli_sel][0]
        else:
            c4.warning("No hay clientes registrados.")
            cli_nom = c4.text_input("Nombre Cliente *", key="v_cnom")
            cli_nit = c5.text_input("NIT Cliente *", key="v_cnit")

        cv1, cv2 = st.columns(2)
        cond_v = cv1.selectbox("Condición de Venta:", ["Contado", "Crédito"], key="v_cond")
        if cond_v == "Contado":
            metodo_v = cv2.selectbox("Medio de Cobro:", ["Efectivo", "Depósito Bancario", "Tarjeta de Crédito / POS"], key="v_met")
            idx_dv = next((i for i, c in enumerate(opc_debe_v) if ("1.1.01.01" in c if metodo_v=="Efectivo" else ("1.1.01.02" in c if metodo_v=="Depósito Bancario" else "1.1.01.03" in c))), 0)
        else:
            cv2.info("Asentado a Clientes por Cobrar (Crédito).")
            idx_dv = next((i for i, c in enumerate(opc_debe_v) if "1.1.02.01" in c), 0)

        cta_debe_v = cv1.selectbox("Cuenta de Cargo (DEBE) *", opc_debe_v if opc_debe_v else ["1.1.01.01 - Caja"], index=idx_dv, key="v_cdebe")
        cta_haber_v = cv2.selectbox("Cuenta de Ingreso (HABER) *", opc_ing if opc_ing else ["4.1.01.01 - Ventas"], key="v_chaber")

        subtotal = st.number_input("Subtotal (Sin IVA) Q *", min_value=0.0, step=100.0, format="%.2f", key="v_sub")

        r1, r2 = st.columns(2)
        aplica_isr = r1.checkbox("Cliente nos practicó Retención ISR", value=False, key="v_rk")
        tipo_isr = r1.selectbox("Tasa ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key="v_rt")
        aplica_iva = r2.checkbox("Cliente nos practicó Retención IVA", value=False, key="v_rvk")
        tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key="v_rvt")

        if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
        if not aplica_isr: tipo_isr = "No Aplica"

        iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)

        partida_v = [
            {"Cuenta": cta_debe_v, "DEBE": f"Q {liq:,.2f}", "HABER": "Q 0.00"},
        ]
        if m_isr > 0: partida_v.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr:,.2f}", "HABER": "Q 0.00"})
        if m_iva > 0: partida_v.append({"Cuenta": "1.1.03.03 - Constancias Retención IVA", "DEBE": f"Q {m_iva:,.2f}", "HABER": "Q 0.00"})
        partida_v.append({"Cuenta": cta_haber_v, "DEBE": "Q 0.00", "HABER": f"Q {subtotal:,.2f}"})
        partida_v.append({"Cuenta": "2.1.02.01 - IVA por Pagar (Débito Fiscal)", "DEBE": "Q 0.00", "HABER": f"Q {iva:,.2f}"})
        st.table(pd.DataFrame(partida_v))

        if st.button("Guardar Factura de Venta y Asentar", type="primary", key="v_save"):
            if num_fac and cli_nom and cli_nit and subtotal > 0:
                conn = get_db_connection()
                c = conn.cursor()
                fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                saldo = liq if cond_v == "Crédito" else 0.0
                estado = "PENDIENTE" if cond_v == "Crédito" else "PAGADO"
                fp_desc = cond_v if cond_v == "Crédito" else f"Contado ({metodo_v})"

                c.execute('''INSERT INTO facturas VALUES (?, ?, 'VENTA', ?, ?, ?, ?, ?, 'Venta', 'Ingreso',
                             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nom, cli_nit,
                           cta_haber_v, cta_debe_v, fp_desc, subtotal, iva, total,
                           1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                           1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                           liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = ?", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                glosa = f"Factura Venta {num_fac} a {cli_nom}"

                cod_h, nom_h = cta_haber_v.split(" - ", 1)
                cod_d, nom_d = cta_debe_v.split(" - ", 1)
                c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, liq, glosa, num_fac, now_str))
                if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.02', 'ISR Retenido por Acreditar', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '1.1.03.03', 'Constancias Retención IVA', ?, 0, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, subtotal, glosa, num_fac, now_str))
                if iva > 0: c.execute("INSERT INTO libro_diario VALUES (NULL, ?, ?, ?, '2.1.02.01', 'IVA por Pagar', 0, ?, ?, ?, ?)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))

                conn.commit()
                conn.close()
                st.success("Factura de venta emitida y registrada exitosamente.")
                st.rerun()

# ==========================================
# 5. CUENTAS POR PAGAR (PROVEEDORES)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("💳 Control de Cuentas por Pagar (CXP)")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
                                  FROM facturas WHERE empresa_id = ? AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxp.empty:
        st.success("✨ Todas las facturas de proveedores están pagadas al 100%.")
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
                    st.error("El abono no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(row_f['saldo_pendiente'] - monto_abono, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = ?, estado_pago = ? WHERE id = ?", (nuevo_saldo, nuevo_estado, fac_sel))
                    # Partida de Abono: DEBE Proveedores, HABER Bancos/Caja
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
# 6. CUENTAS POR COBRAR (CLIENTES)
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title("💰 Control de Cuentas por Cobrar (CXC)")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
                                  FROM facturas WHERE empresa_id = ? AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxc.empty:
        st.success("✨ Todos los clientes están al día con sus pagos.")
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
                    st.error("El cobro no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo = round(row_v['saldo_pendiente'] - monto_cobro, 2)
                    nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = ?, estado_pago = ? WHERE id = ?", (nuevo_saldo, nuevo_estado, fac_sel))
                    # Partida de Cobro: DEBE Bancos/Caja, HABER Clientes
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
# 7. CONTROL DE INVENTARIOS (KÁRDEX)
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
                        st.success("Producto creado.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un producto con este código.")
                    conn.close()

    conn = get_db_connection()
    df_p = pd.read_sql_query("SELECT id, codigo AS 'Código', descripcion AS 'Descripción', existencia AS 'Stock Actual', costo_promedio AS 'Costo Promedio (Q)', precio_venta AS 'Precio Venta (Q)' FROM productos WHERE empresa_id = ?", conn, params=(selected_emp_id,))
    st.dataframe(df_p, use_container_width=True)
    conn.close()

# ==========================================
# 8. LIBRO DE COMPRAS (FORMATO SAT)
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
        buffer = io.BytesIO()
        df_compras_sat.to_excel(buffer, index=False, engine='openpyxl')
        st.download_button("📥 Descargar Libro de Compras Excel", buffer.getvalue(), f"Libro_Compras_{selected_empresa_info[1]}.xlsx")

# ==========================================
# 9. LIBRO DE VENTAS (FORMATO SAT)
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
        buffer = io.BytesIO()
        df_ventas_sat.to_excel(buffer, index=False, engine='openpyxl')
        st.download_button("📥 Descargar Libro de Ventas Excel", buffer.getvalue(), f"Libro_Ventas_{selected_empresa_info[1]}.xlsx")

# ==========================================
# 10. LIBRO DE RETENCIONES
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title("📑 Libro de Retenciones Practicadas y Recibidas")
    conn = get_db_connection()
    df_r = pd.read_sql_query("SELECT tipo AS 'Operación', fecha AS 'Fecha', numero_factura AS 'Documento', tercero_nombre AS 'Tercero', subtotal AS 'Base (Q)', monto_ret_isr AS 'Retención ISR (Q)', monto_ret_iva AS 'Retención IVA (Q)' FROM facturas WHERE empresa_id = ? AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_r, use_container_width=True)

# ==========================================
# 11. LIBRO DIARIO
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

# ==========================================
# 12. LIBRO MAYOR
# ==========================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title("📚 Libro Mayor")
    conn = get_db_connection()
    df_m = pd.read_sql_query("""SELECT codigo_cuenta AS 'Código', nombre_cuenta AS 'Cuenta',
                                       ROUND(SUM(debe), 2) AS 'Total Debe (Q)', ROUND(SUM(haber), 2) AS 'Total Haber (Q)',
                                       ROUND(SUM(debe) - SUM(haber), 2) AS 'Saldo (Q)'
                                FROM libro_diario WHERE empresa_id = ? GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta""", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_m, use_container_width=True)

# ==========================================
# 13. BALANCE GENERAL
# ==========================================
elif selected_menu == "⚖️ Balance General":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
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

# ==========================================
# 14. ESTADO DE RESULTADOS
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados")
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
# 15. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title("🗂️ Nomenclatura Contable")
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
                    st.success("Cuenta guardada.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("El código ya existe.")
                conn.close()
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS 'Código', nombre AS 'Cuenta', tipo AS 'Tipo' FROM nomenclatura WHERE empresa_id = ? ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 16. CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores")
    conn = get_db_connection()
    u_list = conn.execute("SELECT id, username, name, email, status, assigned_empresas FROM users WHERE role != 'admin'").fetchall()
    all_e = conn.execute("SELECT id, nombre, nit FROM empresas").fetchall()
    conn.close()
    with st.form("crear_op"):
        u1, u2 = st.columns(2)
        un = u1.text_input("Usuario")
        no = u2.text_input("Nombre")
        p1, p2 = st.columns(2)
        em = p1.text_input("Correo")
        pw = p2.text_input("Contraseña", type="password")
        emps_asig = [e[0] for e in all_e if st.checkbox(f"{e[1]}", key=f"as_{e[0]}", value=True)]
        if st.form_submit_button("Crear Operador"):
            conn = get_db_connection()
            try:
                conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, 'operator', 'active', ?, ?, 0)",
                             (f"usr-{int(datetime.now().timestamp())}", un, hash_password(pw), no, em, ",".join(emps_asig), datetime.now().isoformat()))
                conn.commit()
                st.success("Operador creado.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("El usuario ya existe.")
            conn.close()

# ==========================================
# 17. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro de Empresas")
    with st.form("f_emp"):
        c1, c2 = st.columns(2)
        en = c1.text_input("Razón Social *")
        nt = c2.text_input("NIT *")
        c3, c4 = st.columns(2)
        dr = c3.text_input("Dirección")
        tl = c4.text_input("Teléfono")
        rg = st.selectbox("Régimen de ISR SAT", ["Opcional Simplificado (5% y 7%)", "Sobre las Utilidades (25%)", "Pequeño Contribuyente (5%)"])
        if st.form_submit_button("Guardar Empresa"):
            if en and nt:
                conn = get_db_connection()
                try:
                    eid = f"emp-{int(datetime.now().timestamp())}"
                    conn.execute("INSERT INTO empresas VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (eid, en, nt, dr, tl, rg, "Contabilidad", datetime.now().isoformat()))
                    conn.commit()
                    sembrar_nomenclatura_inicial(eid)
                    st.success("Empresa registrada.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("NIT ya registrado.")
                conn.close()