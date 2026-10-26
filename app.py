import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import io
from datetime import datetime, date

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
# CONEXIÓN PERMANENTE A SUPABASE (POSTGRESQL)
# ==========================================
def get_db_connection():
    db_url = st.secrets.get("DATABASE_URL")
    if not db_url:
        st.error("No se encontró DATABASE_URL en los Secrets de Streamlit.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def inicializar_tablas_adicionales():
    conn = get_db_connection()
    c = conn.cursor()
    # Tablas de Despensa Familiar y Cocina
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
    conn.commit()
    conn.close()

try:
    inicializar_tablas_adicionales()
except Exception:
    pass

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
        c.executemany("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
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
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE username = %s AND password_hash = %s", (u, hash_password(p)))
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
# SIDEBAR Y EMPRESA ACTIVA
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
        st.warning("No hay empresas creadas.")

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
# 🏠 CONTROL DE DESPENSA FAMILIAR (NO FISCAL)
# ==========================================
elif selected_menu == "🏠 Despensa Familiar (Cocina)":
    st.title("🏠 Control de Despensa y Cocina Familiar")
    st.caption("Control doméstico de compras e insumos del hogar (no fiscal).")

    tab_stock, tab_ingreso, tab_rebaja, tab_nuevo = st.tabs([
        "📦 Stock Actual",
        "🛒 Registrar Ingreso / Compra",
        "🍳 Rebajar Producto (Consumo)",
        "➕ Nuevo Producto Hogar"
    ])

    conn = get_db_connection()
    df_articulos = pd.read_sql_query(
        "SELECT id, nombre, categoria, unidad_medida, stock_actual, stock_minimo FROM despensa_articulos ORDER BY nombre ASC",
        conn
    )
    conn.close()

    with tab_stock:
        st.subheader("📋 Existencias en Despensa y Refrigerador")
        if df_articulos.empty:
            st.info("Aún no tienes productos registrados. Agrégalos en '➕ Nuevo Producto Hogar'.")
        else:
            por_agotarse = df_articulos[df_articulos["stock_actual"] <= df_articulos["stock_minimo"]]
            if not por_agotarse.empty:
                st.warning(f"⚠️ **Por agotarse ({len(por_agotarse)}):** " + 
                           ", ".join([f"{r['nombre']} ({r['stock_actual']} {r['unidad_medida']})" for _, r in por_agotarse.iterrows()]))

            st.dataframe(
                df_articulos[["nombre", "categoria", "stock_actual", "unidad_medida", "stock_minimo"]].rename(
                    columns={
                        "nombre": "Producto", "categoria": "Categoría",
                        "stock_actual": "Stock Disponible", "unidad_medida": "Unidad",
                        "stock_minimo": "Alerta Mínima"
                    }
                ),
                use_container_width=True, hide_index=True
            )

            with st.expander("🕒 Ver Historial de Movimientos de Entrada y Salida"):
                conn = get_db_connection()
                df_historial = pd.read_sql_query("""
                    SELECT m.fecha AS "Fecha", a.nombre AS "Producto", m.tipo AS "Operación", 
                           m.cantidad AS "Cantidad", a.unidad_medida AS "Unidad", 
                           m.registrado_por AS "Registró", m.detalle AS "Notas"
                    FROM despensa_movimientos m
                    JOIN despensa_articulos a ON m.articulo_id = a.id
                    ORDER BY m.fecha DESC, m.created_at DESC LIMIT 50
                """, conn)
                conn.close()
                if not df_historial.empty:
                    st.dataframe(df_historial, use_container_width=True, hide_index=True)
                else:
                    st.caption("No hay movimientos registrados.")

    with tab_ingreso:
        st.subheader("🛒 Ingreso de Compras del Supermercado")
        if df_articulos.empty:
            st.info("Primero registra productos en la despensa.")
        else:
            with st.form("form_ingreso_despensa", clear_on_submit=True):
                c1, c2 = st.columns(2)
                opciones_prod = {f"{r['nombre']} (Actual: {r['stock_actual']} {r['unidad_medida']})": r["id"] for _, r in df_articulos.iterrows()}
                prod_sel_label = c1.selectbox("Seleccionar Producto:", list(opciones_prod.keys()))
                prod_id = opciones_prod[prod_sel_label]
                fecha_ingreso = c2.date_input("Fecha de Compra:", value=date.today())

                c3, c4 = st.columns(2)
                cant_ingreso = c3.number_input("Cantidad que ingresa:", min_value=0.25, step=1.0, value=1.0)
                lugar_compra = c4.text_input("Lugar / Detalle:", value="Supermercado")

                if st.form_submit_button("📥 Registrar Ingreso al Stock", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    mov_id = f"desp-in-{int(datetime.now().timestamp())}"
                    c.execute("""
                        INSERT INTO despensa_movimientos (id, articulo_id, tipo, cantidad, fecha, registrado_por, detalle, created_at)
                        VALUES (%s, %s, 'INGRESO', %s, %s, %s, %s, %s)
                    """, (mov_id, prod_id, cant_ingreso, str(fecha_ingreso), curr_u['name'], lugar_compra, datetime.now().isoformat()))
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual + %s WHERE id = %s", (cant_ingreso, prod_id))
                    conn.commit()
                    conn.close()
                    st.success(f"✅ Se sumaron {cant_ingreso} unidades.")
                    st.rerun()

    with tab_rebaja:
        st.subheader("🍳 Rebajar Insumo (Consumo en Cocina)")
        if df_articulos.empty:
            st.info("No hay productos disponibles.")
        else:
            with st.form("form_salida_despensa", clear_on_submit=True):
                c1, c2 = st.columns(2)
                opciones_prod_out = {f"{r['nombre']} (Disponible: {r['stock_actual']} {r['unidad_medida']})": (r["id"], r["stock_actual"], r["nombre"]) for _, r in df_articulos.iterrows()}
                prod_out_label = c1.selectbox("¿Qué producto se consumió?:", list(opciones_prod_out.keys()))
                prod_out_id, stock_disp, nom_prod = opciones_prod_out[prod_out_label]
                fecha_rebaja = c2.date_input("Fecha de Consumo:", value=date.today())

                c3, c4 = st.columns(2)
                cant_rebaja = c3.number_input("Cantidad a restar:", min_value=0.25, step=1.0, value=1.0)
                motivo = c4.text_input("Uso / Destino:", value="Cocina")

                if st.form_submit_button("➖ Aplicar Rebaja de Stock", type="primary"):
                    if cant_rebaja > stock_disp:
                        st.error(f"No puedes rebajar {cant_rebaja}. Solo hay {stock_disp} en existencia.")
                    else:
                        conn = get_db_connection()
                        c = conn.cursor()
                        mov_id = f"desp-out-{int(datetime.now().timestamp())}"
                        c.execute("""
                            INSERT INTO despensa_movimientos (id, articulo_id, tipo, cantidad, fecha, registrado_por, detalle, created_at)
                            VALUES (%s, %s, 'REBAJA', %s, %s, %s, %s, %s)
                        """, (mov_id, prod_out_id, cant_rebaja, str(fecha_rebaja), curr_u['name'], motivo, datetime.now().isoformat()))
                        c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual - %s WHERE id = %s", (cant_rebaja, prod_out_id))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Se rebajaron {cant_rebaja} de {nom_prod}.")
                        st.rerun()

    with tab_nuevo:
        st.subheader("➕ Registrar Nuevo Artículo para el Hogar")
        with st.form("form_nuevo_prod_hogar", clear_on_submit=True):
            col1, col2 = st.columns(2)
            n_nombre = col1.text_input("Nombre del Producto * (ej. Arroz blanco, Leche entera, Aceite)")
            n_cat = col2.selectbox("Categoría:", ["Granos y Abarrotes", "Lácteos y Huevos", "Carnes y Embutidos", "Frutas y Verduras", "Limpieza del Hogar", "Bebidas", "Higiene Personal", "Otros"])

            col3, col4, col5 = st.columns(3)
            n_unidad = col3.selectbox("Unidad de Medida:", ["Unidades / Bolsas", "Libras", "Litros / Botellas", "Latas", "Cajas", "Paquetes"])
            n_inicial = col4.number_input("Existencia Inicial:", min_value=0.0, step=1.0, value=0.0)
            n_min = col5.number_input("Alerta de Stock Mínimo:", min_value=0.0, step=1.0, value=1.0)

            if st.form_submit_button("✅ Guardar Producto", type="primary"):
                if n_nombre.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        p_id = f"art-{int(datetime.now().timestamp())}"
                        c.execute("""
                            INSERT INTO despensa_articulos (id, nombre, categoria, unidad_medida, stock_actual, stock_minimo, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (p_id, n_nombre.strip(), n_cat, n_unidad, n_inicial, n_min, datetime.now().isoformat()))
                        conn.commit()
                        st.success(f"¡'{n_nombre}' guardado exitosamente!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al guardar: es posible que el producto ya exista ({e}).")
                    finally:
                        conn.close()
                else:
                    st.error("Por favor ingresa el nombre del producto.")

# ==========================================
# 2. DIRECTORIO TERCEROS
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
                    c = conn.cursor()
                    try:
                        c.execute("""INSERT INTO terceros (empresa_id, tipo, nit, nombre, direccion, telefono, dias_credito)
                                     VALUES (%s, %s, %s, %s, %s, %s, %s)""", (selected_emp_id, t_tipo, t_nit.strip().upper(), t_nom.strip(), t_dir, t_tel, t_dias))
                        conn.commit()
                        st.success(f"Tercero '{t_nom}' registrado.")
                        st.rerun()
                    except Exception:
                        st.error("Ya existe un tercero registrado con este NIT.")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el NIT y el Nombre.")

    conn = get_db_connection()
    df_t = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre\", direccion AS \"Dirección\", telefono AS \"Teléfono\", dias_credito AS \"Días Crédito\" FROM terceros WHERE empresa_id = %s ORDER BY nombre", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_t, use_container_width=True)

# ==========================================
# 3. FACTURA DE COMPRAS
# ==========================================
elif selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title("🛒 Registro de Factura de Compra")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT nit, nombre FROM terceros WHERE empresa_id = %s AND tipo IN ('PROVEEDOR', 'AMBOS')", (selected_emp_id,))
    provs = c.fetchall()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_haber = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
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
            c4.warning("No hay proveedores registrados.")
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

                c.execute("""INSERT INTO facturas VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, 'Compra', 'Gasto',
                             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                          (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom, prov_nit,
                           cta_debe, cta_haber, fp_desc, subtotal, iva, total,
                           1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                           1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                           liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))
                
                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                glosa = f"Factura Compra {num_fac} de {prov_nom}"

                cod_d, nom_d = cta_debe.split(" - ", 1)
                cod_h, nom_h = cta_haber.split(" - ", 1)
                c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, subtotal, glosa, num_fac, now_str))
                if iva > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.01', 'IVA por Cobrar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))
                if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.03.01', 'Retenciones ISR por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.03.02', 'Retenciones IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, liq, glosa, num_fac, now_str))

                conn.commit()
                conn.close()
                st.success("Factura de compra registrada exitosamente.")
                st.rerun()

# ==========================================
# 4. FACTURA DE VENTAS
# ==========================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title("📈 Registro de Factura de Venta")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT nit, nombre FROM terceros WHERE empresa_id = %s AND tipo IN ('CLIENTE', 'AMBOS')", (selected_emp_id,))
    clientes = c.fetchall()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cuentas_debe = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Activo' ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()

    dict_cli = {f"{cl[1]} (NIT: {cl[0]})": cl for cl in clientes}
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
        aplica_isr = r1.checkbox("Cliente practicó Retención ISR", value=False, key="v_rk")
        tipo_isr = r1.selectbox("Tasa ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key="v_rt")
        aplica_iva = r2.checkbox("Cliente practicó Retención IVA", value=False, key="v_rvk")
        tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Total IVA"], key="v_rvt")

        if not aplica_iva: tipo_iva = "No Aplica / No es Agente Retenedor"
        if not aplica_isr: tipo_isr = "No Aplica"

        iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_retenciones(subtotal, aplica_isr, tipo_isr, aplica_iva, tipo_iva)

        partida_v = [
            {"Cuenta": cta_debe_v, "DEBE": f"Q {liq:,.2f}", "HABER": "Q 0.00"},
        ]
        if m_isr > 0: partida_v.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr:,.2f}", "HABER": "Q 0.00"},)
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

                c.execute("""INSERT INTO facturas VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, 'Venta', 'Ingreso',
                             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                          (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nom, cli_nit,
                           cta_haber_v, cta_debe_v, fp_desc, subtotal, iva, total,
                           1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                           1 if (aplica_iva and tipo_iva != "No Aplica / No es Agente Retenedor") else 0, t_iva, m_iva,
                           liq, saldo, estado, curr_u['username'], datetime.now().isoformat()))

                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                p_no = c.fetchone()[0]
                now_str = datetime.now().isoformat()
                glosa = f"Factura Venta {num_fac} a {cli_nom}"

                cod_h, nom_h = cta_haber_v.split(" - ", 1)
                cod_d, nom_d = cta_debe_v.split(" - ", 1)
                c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_d, nom_d, liq, glosa, num_fac, now_str))
                if m_isr > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.02', 'ISR Retenido por Acreditar', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_isr, glosa, num_fac, now_str))
                if m_iva > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.03.03', 'Constancias Retención IVA', %s, 0, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), m_iva, glosa, num_fac, now_str))
                c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), cod_h, nom_h, subtotal, glosa, num_fac, now_str))
                if iva > 0: c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.02.01', 'IVA por Pagar', 0, %s, %s, %s, %s)", (selected_emp_id, p_no, str(fecha_fac), iva, glosa, num_fac, now_str))

                conn.commit()
                conn.close()
                st.success("Factura de venta emitida exitosamente.")
                st.rerun()

# ==========================================
# 5. CUENTAS POR PAGAR (PROVEEDORES)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title("💳 Control de Cuentas por Pagar (CXP)")
    conn = get_db_connection()
    df_cxp = pd.read_sql_query("""SELECT id, fecha, numero_factura, tercero_nombre, total, saldo_pendiente
                                  FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxp.empty:
        st.success("✨ Todas las facturas de proveedores están canceladas al día.")
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
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '2.1.01.01', 'Proveedores / Cuentas por Pagar', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), monto_abono, f"Abono Fac {row_f['numero_factura']}", row_f['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, 'Salida de Fondos', 0, %s, %s, %s, %s)",
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
                                  FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'""", conn, params=(selected_emp_id,))
    
    if df_cxc.empty:
        st.success("✨ Todos los clientes se encuentran al día con sus pagos.")
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
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo, nuevo_estado, fac_sel))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, %s, 'Ingreso de Fondos', %s, 0, %s, %s, %s)",
                              (selected_emp_id, p_no, str(date.today()), cta_destino, monto_cobro, f"Cobro Fac {row_v['numero_factura']}", row_v['numero_factura'], datetime.now().isoformat()))
                    c.execute("INSERT INTO libro_diario VALUES (DEFAULT, %s, %s, %s, '1.1.02.01', 'Clientes / Cuentas por Cobrar', 0, %s, %s, %s, %s)",
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
    st.title("📦 Inventario Corporativo y Kárdex Ponderado")

    with st.expander("➕ Crear Nuevo Producto en Catálogo Corporativo"):
        with st.form("form_prod"):
            c1, c2, c3 = st.columns(3)
            p_cod = c1.text_input("Código de Artículo *")
            p_nom = c2.text_input("Descripción *")
            p_pre = c3.number_input("Precio de Venta Sugerido (Q)", min_value=0.0)
            if st.form_submit_button("Guardar Producto Corporativo"):
                if p_cod and p_nom:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        p_id = f"prod-{int(datetime.now().timestamp())}"
                        c.execute("INSERT INTO productos (id, empresa_id, codigo, descripcion, precio_venta, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                                  (p_id, selected_emp_id, p_cod.strip(), p_nom.strip(), p_pre, datetime.now().isoformat()))
                        conn.commit()
                        st.success("Producto corporativo creado exitosamente.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al guardar producto: {err}")
                    finally:
                        conn.close()

    conn = get_db_connection()
    df_p = pd.read_sql_query("SELECT codigo AS \"Código\", descripcion AS \"Descripción\", existencia AS \"Stock Actual\", costo_promedio AS \"Costo Promedio (Q)\", precio_venta AS \"Precio Venta (Q)\" FROM productos ORDER BY codigo", conn)
    st.dataframe(df_p, use_container_width=True)
    conn.close()

# ==========================================
# 8. LIBRO DE COMPRAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_compras_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "Factura",欺tercero_nit AS "NIT Proveedor",
               tercero_nombre AS "Proveedor", subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR (Q)", monto_ret_iva AS "Ret. IVA (Q)", total AS "Total Factura (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha ASC
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
# 9. LIBRO DE VENTAS (SAT)
# ==========================================
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
    df_r = pd.read_sql_query("""
        SELECT tipo AS "Operación", fecha AS "Fecha", numero_factura AS "Documento", tercero_nombre AS "Tercero", 
               subtotal AS "Base (Q)", monto_ret_isr AS "Retención ISR (Q)", monto_ret_iva AS "Retención IVA (Q)" 
        FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0) ORDER BY fecha DESC
    """, conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_r, use_container_width=True)

# ==========================================
# 11. LIBRO DIARIO
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

# ==========================================
# 12. LIBRO MAYOR
# ==========================================
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
        WHERE n.empresa_id = %s GROUP BY n.codigo, n.nombre, n.tipo HAVING (COALESCE(SUM(d.debe), 0) > 0 OR COALESCE(SUM(d.haber), 0) > 0) ORDER BY n.codigo
    """
    df_b = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()
    if not df_b.empty:
        df_b['Saldo Deudor'] = df_b.apply(lambda r: round(float(r['total_debe']) - float(r['total_haber']), 2) if float(r['total_debe']) > float(r['total_haber']) else 0.0, axis=1)
        df_b['Saldo Acreedor'] = df_b.apply(lambda r: round(float(r['total_haber']) - float(r['total_debe']), 2) if float(r['total_haber']) > float(r['total_debe']) else 0.0, axis=1)
        st.dataframe(df_b[['codigo', 'nombre', 'tipo', 'Saldo Deudor', 'Saldo Acreedor']].rename(columns={'codigo': 'Código', 'nombre': 'Cuenta', 'tipo': 'Tipo'}), use_container_width=True)

# ==========================================
# 14. ESTADO DE RESULTADOS
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title("📉 Estado de Resultados")
    conn = get_db_connection()
    q_i = "SELECT n.nombre, ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = %s AND n.tipo = 'Ingreso' GROUP BY n.nombre"
    q_g = "SELECT n.nombre, ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS monto FROM nomenclatura n JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id WHERE n.empresa_id = %s AND n.tipo = 'Gasto' GROUP BY n.nombre"
    df_i = pd.read_sql_query(q_i, conn, params=(selected_emp_id,))
    df_g = pd.read_sql_query(q_g, conn, params=(selected_emp_id,))
    conn.close()
    tot_i = float(df_i['monto'].sum()) if not df_i.empty else 0.0
    tot_g = float(df_g['monto'].sum()) if not df_g.empty else 0.0
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
                c = conn.cursor()
                try:
                    c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)", (selected_emp_id, cod_c.strip(), nom_c.strip(), tip_c))
                    conn.commit()
                    st.success("Cuenta contable guardada.")
                    st.rerun()
                except Exception:
                    st.error("El código ya existe.")
                finally:
                    conn.close()
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==========================================
# 16. CONTROL DE USUARIOS (ADMIN)
# ==========================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_e = c.fetchall()
    conn.close()
    with st.form("crear_op"):
        u1, u2 = st.columns(2)
        un = u1.text_input("Usuario")
        no = u2.text_input("Nombre")
        p1, p2 = st.columns(2)
        em = p1.text_input("Correo")
        pw = p2.text_input("Contraseña", type="password")
        emps_asig = [e[0] for e in all_e if st.checkbox(f"{e[1]} (NIT: {e[2]})", key=f"as_{e[0]}", value=True)]
        if st.form_submit_button("Crear Operador"):
            conn = get_db_connection()
            c = conn.cursor()
            try:
                c.execute("INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at, reset_requested) VALUES (%s, %s, %s, %s, %s, 'operator', 'active', %s, %s, 0)",
                          (f"usr-{int(datetime.now().timestamp())}", un, hash_password(pw), no, em, ",".join(emps_asig), datetime.now().isoformat()))
                conn.commit()
                st.success("Operador creado exitosamente.")
                st.rerun()
            except Exception:
                st.error("El usuario ya existe.")
            finally:
                conn.close()

# ==========================================
# 17. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.stop()
    st.title("🏢 Registro y Consulta de Empresas")

    tab_crear_e, tab_lista_e = st.tabs(["➕ Registrar Empresa", "📋 Empresas Registradas"])

    with tab_crear_e:
        with st.form("f_emp"):
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
                        st.success(f"Empresa '{en}' registrada exitosamente en Supabase.")
                        st.rerun()
                    except Exception:
                        st.error("El NIT ya se encuentra registrado.")
                    finally:
                        conn.close()
                else:
                    st.error("Completa el Nombre y el NIT.")

    with tab_lista_e:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, nombre, nit, direccion, telefono, regimen_isr, created_at FROM empresas ORDER BY nombre")
        empresas_registradas = c.fetchall()
        conn.close()
        if not empresas_registradas:
            st.info("No hay empresas registradas.")
        else:
            for emp in empresas_registradas:
                e_id, nom, nit, dire, tel, reg, fec_crea = emp
                with st.expander(f"🏢 {nom} — NIT: {nit}"):
                    st.write(f"**Dirección:** {dire or 'No indicada'} | **Tel:** {tel or 'No indicado'} | **Régimen:** {reg}")
                    st.caption(f"ID: `{e_id}` | Registrada: {fec_crea or 'N/A'}")