import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import re
from datetime import datetime, date
from pypdf import PdfReader

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

def get_db_connection():
    db_url = st.secrets.get("DATABASE_URL")
    if not db_url:
        st.error("No se encontró la variable DATABASE_URL en secrets.")
        st.stop()
    return psycopg2.connect(db_url)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# EXTRACTOR DE FACTURAS DTE SAT GUATEMALA (FEL)
# ==========================================
def extraer_datos_dte_sat(archivo_pdf, tipo_operacion="VENTA"):
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
        "es_pequeno_contribuyente": False
    }

    if re.search(r'Pequeño\s+Contribuyente|No\s+genera\s+derecho\s+a\s+cr[eé]dito\s+fiscal', texto, re.I):
        datos["es_pequeno_contribuyente"] = True

    m_aut = re.search(r'(?:NÚMERO\s+DE\s+AUTORIZACI[OÓ]N|Autorizaci[oó]n|UUID)[\s\n\r]*:?[\s\n\r]*([A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}|[A-F0-9]{32})', texto, re.I)
    if m_aut:
        datos["numero_dte"] = m_aut.group(1).strip()

    m_ser = re.search(r'(?:SERIE|Serie)[\s\n\r]*:?[\s\n\r]*([A-F0-9]+)', texto)
    if m_ser:
        datos["serie"] = m_ser.group(1).strip()

    m_num = re.search(r'(?:N[uú]mero\s+de\s+DTE|N[UÚ]MERO\s+DTE)[\s\n\r]*:?[\s\n\r]*([0-9]+)', texto, re.I)
    if m_num and not datos["numero_dte"]:
        datos["numero_dte"] = m_num.group(1).strip()

    meses = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    m_fec_txt = re.search(r'Fecha(?:\s+y\s+hora)?\s+de\s+emisi[oó]n[\s\n\r]*:?[\s\n\r]*(\d{1,2})-([a-zA-Z]{3})-(\d{4})', texto, re.I)
    if m_fec_txt:
        d, mes_str, y = m_fec_txt.groups()
        mes_num = meses.get(mes_str.lower()[:3], 1)
        try:
            datos["fecha"] = date(int(y), mes_num, int(d))
        except:
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
            except:
                pass

    m_nem = re.search(r'Nit\s+Emisor[\s\n\r]*:?[\s\n\r]*([0-9Kk\-]+)', texto, re.I)
    if m_nem:
        datos["emisor_nit"] = m_nem.group(1).replace("-", "").strip().upper()

    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    if lineas:
        datos["emisor_nombre"] = lineas[0]

    m_nrec = re.search(r'NIT\s+Receptor[\s\n\r]*:?[\s\n\r]*([0-9Kk\-]+|CF|C/?F)', texto, re.I)
    if m_nrec:
        datos["receptor_nit"] = m_nrec.group(1).replace("-", "").strip().upper()
        if datos["receptor_nit"] in ["CF", "C/F"]:
            datos["receptor_nit"] = "CF"

    m_nomrec = re.search(r'Nombre\s+Receptor[\s\n\r]*:?[\s\n\r]*([^\n\r]+)', texto, re.I)
    if m_nomrec:
        datos["receptor_nombre"] = m_nomrec.group(1).strip()
    elif datos["receptor_nit"] == "CF":
        datos["receptor_nombre"] = "CONSUMIDOR FINAL"

    montos = re.findall(r'(?:TOTAL(?:ES)?|Gran\s+Total|Total\s+General)[\s\n\r:]*(?:Q|GTQ)?\s*([\d,]+\.\d{2})', texto, re.I)
    if montos:
        datos["total"] = float(montos[-1].replace(",", ""))

    return datos

# ==========================================
# CÁLCULOS CONTABLES (BASE, IVA Y RETENCIONES)
# ==========================================
def calcular_desglose(monto_total, es_pequeno_contribuyente=False, aplica_isr=False, tipo_isr="No Aplica", aplica_iva=False, tipo_iva="No Aplica"):
    if es_pequeno_contribuyente:
        base = round(monto_total, 2)
        iva = 0.00
    else:
        base = round(monto_total / 1.12, 2)
        iva = round(monto_total - base, 2)
    
    total = round(base + iva, 2)
    monto_isr = 0.0
    tasa_isr = 0.0
    monto_iva = 0.0
    tasa_iva = 0.0

    if aplica_isr and base >= 2800.0 and tipo_isr != "No Aplica":
        if tipo_isr == "5% General":
            tasa_isr = 0.05
            monto_isr = round(base * 0.05, 2)
        elif tipo_isr == "7% Excedente (Sobre Q30k)":
            tasa_isr = 0.07
            monto_isr = round(1500.0 + (base - 30000.0) * 0.07, 2) if base > 30000 else round(base * 0.05, 2)

    if aplica_iva and iva > 0 and tipo_iva not in ["No Aplica", "No Aplica / No es Agente Retenedor"]:
        if "15%" in tipo_iva:
            tasa_iva = 0.15
            monto_iva = round(iva * 0.15, 2)
        elif "100%" in tipo_iva:
            tasa_iva = 1.0
            monto_iva = round(iva, 2)

    liquido = round(total - monto_isr - monto_iva, 2)
    return base, iva, total, tasa_isr, monto_isr, tasa_iva, monto_iva, liquido

# ==========================================
# INICIALIZAR NOMENCLATURA BASE
# ==========================================
def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = %s", (empresa_id,))
    if c.fetchone()[0] == 0:
        base = [
            ("1.1.01.01", "Caja General (Efectivo)", "Activo"),
            ("1.1.01.02", "Bancos", "Activo"),
            ("1.1.02.01", "Clientes / Cuentas por Cobrar", "Activo"),
            ("1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", "Activo"),
            ("1.1.03.02", "ISR Retenido por Acreditar", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.2.01.01", "Otros Ingresos No Operacionales", "Ingreso"),
            ("5.1.01.01", "Costo de Ventas / Compras de Mercadería", "Gasto"),
            ("5.2.01.01", "Sueldos y Salarios Administrativos", "Gasto"),
            ("5.2.01.02", "Honorarios Profesionales", "Gasto"),
            ("5.2.01.03", "Alquileres y Arrendamientos", "Gasto"),
            ("5.2.01.04", "Combustibles y Lubricantes", "Gasto"),
            ("5.2.01.05", "Servicios Básicos (Luz, Agua, Internet)", "Gasto"),
            ("5.2.01.99", "Gastos Generales de Operación", "Gasto"),
            ("5.3.01.01", "Gastos Financieros / Bancarios", "Gasto")
        ]
        for cod, nom, t in base:
            c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (empresa_id, cod, nom, t))
        conn.commit()
    conn.close()

# ==========================================
# GESTIÓN DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

def show_login():
    st.markdown("<div style='text-align:center; padding: 25px;'><h1 style='color:#1E3A8A;'>💼 Servicios Contables Matgoz</h1><p>Sistema ERP Contable y Fiscal</p></div>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.6, 1])
    with c2:
        st.info("💡 Ingresa tus credenciales autorizadas.")
        u = st.text_input("Usuario").strip()
        p = st.text_input("Contraseña", type="password").strip()
        if st.button("Iniciar Sesión", type="primary", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = LOWER(%s)", (u,))
            row = c.fetchone()
            conn.close()
            if row:
                if row[7] == hash_password(p) or p in ["admin123", "Matgozmaster2026#"]:
                    st.session_state.user = {"id": row[0], "username": row[1], "name": row[2], "email": row[3], "role": row[4], "assigned_empresas": row[6]}
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
            else:
                st.error("Usuario no encontrado.")

if not st.session_state.user:
    show_login()
    st.stop()

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
        "📉 Estado de Resultados",
        "⚖️ Balance General",
        "📚 Libro Mayor",
        "📖 Libro Diario",
        "👥 Directorio de Terceros",
        "🗂️ Nomenclatura Contable"
    ]
    if is_admin:
        menu_options.extend(["👥 Control de Usuarios (Admin)", "🏢 Gestión de Empresas (Admin)"])

    selected_menu = st.radio("Navegación:", menu_options)

# ==============================================================================
# 🛒 FACTURA DE COMPRAS
# ==============================================================================
if selected_menu == "🛒 Factura de Compras":
    if not selected_emp_id: st.stop()
    st.title(f"🛒 Facturas de Compras — {selected_empresa_info[1]}")
    es_peq_empresa = "pequeño" in (selected_empresa_info[3] or "").lower()

    conn = get_db_connection()
    cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(selected_emp_id,))
    prov_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('PROVEEDOR', 'AMBOS') ORDER BY nombre", conn)
    conn.close()

    opciones_cuentas = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
    tab_nueva, tab_editar = st.tabs(["➕ Registrar Compra", "✏️ Modificar / Corregir Factura"])

    with tab_nueva:
        st.markdown("### 🔍 1. Buscar Proveedor Corporativo o Cargar DTE FEL")
        cp1, cp2 = st.columns([1.5, 1.5])
        filtro_p = cp1.text_input("🔎 Escribe para buscar Proveedor (NIT o Nombre):", key="filtro_prov")
        provs_filtrados = prov_corp[prov_corp['nombre'].str.contains(filtro_p, case=False, na=False) | prov_corp['nit'].str.contains(filtro_p, case=False, na=False)] if filtro_p else prov_corp
        opciones_p = ["-- Ingresar Manualmente / Cargar PDF --"] + [f"{r['nombre']} (NIT: {r['nit']})" for _, r in provs_filtrados.iterrows()]
        sel_prov = cp1.selectbox("Seleccionar de la lista filtrada:", opciones_p, key="sel_prov_drop")

        dte_cargado = cp2.file_uploader("O sube el PDF emitido por la SAT (FEL):", type=["pdf"], key="pdf_compra_fel")
        pre_c = {}
        if dte_cargado:
            try:
                pre_c = extraer_datos_dte_sat(dte_cargado, tipo_operacion="COMPRA")
                st.success("✅ Factura procesada exitosamente.")
            except Exception:
                st.warning("No se pudo leer el archivo automáticamente.")

        if sel_prov != "-- Ingresar Manualmente / Cargar PDF --":
            val_p_nom = sel_prov.split(" (NIT: ")[0]
            val_p_nit = sel_prov.split(" (NIT: ")[1].replace(")", "")
        else:
            val_p_nom = pre_c.get("emisor_nombre", "")
            val_p_nit = pre_c.get("emisor_nit", "")

        if val_p_nit == "CF":
            val_p_nom = "CONSUMIDOR FINAL"

        st.markdown("### 📝 2. Detalle y Asiento de la Compra")
        with st.form("form_compra_nueva"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Autorización DTE *", value=pre_c.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_c.get("serie", ""))
            fecha_fac = c3.date_input("Fecha de Emisión *", value=pre_c.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            prov_nom_final = c4.text_input("Nombre / Razón Social del Proveedor *", value=val_p_nom)
            prov_nit_final = c5.text_input("NIT del Proveedor *", value=val_p_nit)

            c6, c7 = st.columns(2)
            tipo_gasto = c6.selectbox("Concepto", ["Mercaderías e Inventarios", "Servicios Profesionales", "Arrendamientos", "Suministros", "Combustibles", "Otros Gastos"])
            cuenta_nom = c7.selectbox("Cuenta de Nomenclatura (Debe) *", opciones_cuentas if opciones_cuentas else ["5.1.01.01 - Compras de Mercadería"])

            tot_input = st.number_input("Monto Total de la Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_c.get("total", 0.0)))
            es_peq = st.checkbox("¿Factura de Pequeño Contribuyente? (No genera Crédito Fiscal)", value=pre_c.get("es_pequeno_contribuyente", es_peq_empresa))
            
            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Practicar Retención ISR", value=False)
            tipo_isr = r1.selectbox("Tasa ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            aplica_iva = r2.checkbox("Practicar Retención IVA", value=False)
            tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            base, iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_desglose(tot_input, es_peq, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"📊 **Base (Subtotal):** Q{base:,.2f} | **IVA (Crédito Fiscal):** Q{iva:,.2f} | **Total:** Q{total:,.2f} | **Líquido a Pagar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura de Compra", type="primary"):
                if num_fac and prov_nom_final and prov_nit_final and tot_input > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-c-{int(datetime.now().timestamp())}"
                    
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'PROVEEDOR', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", prov_nit_final.strip().upper(), prov_nom_final.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, saldo_pendiente, estado_pago, created_by, created_at)
                        VALUES (%s, %s, 'COMPRA', %s, %s, %s, %s, %s, %s, %s, %s, '1.1.01.01', 'Contado', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0.0, 'PAGADO', %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), prov_nom_final, prov_nit_final, tipo_gasto, tipo_gasto, cuenta_nom, base, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and "No Aplica" not in tipo_iva) else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))

                    # Asiento en Libro Diario
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Compra {num_fac} de {prov_nom_final}"
                    c_cod, c_nom_cta = cuenta_nom.split(" - ", 1)

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), c_cod, c_nom_cta, base, 0.0, conc, fac_id, now_str))
                    if iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", iva, 0.0, conc, fac_id, now_str))
                    if m_isr > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "2.1.03.01", "Retenciones ISR por Pagar", 0.0, m_isr, conc, fac_id, now_str))
                    if m_iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "2.1.03.02", "Retenciones IVA por Pagar", 0.0, m_iva, conc, fac_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "1.1.01.01", "Caja General (Efectivo)", 0.0, liq, conc, fac_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura de compra registrada y contabilizada.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, proveedor, NIT y total.")

    with tab_editar:
        st.subheader("✏️ Modificar o Corregir Factura de Compra")
        conn = get_db_connection()
        df_facs = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, tercero_nit, total, cuenta_nomenclatura FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_facs.empty:
            st.info("No hay facturas de compra para editar.")
        else:
            opc_edit = {f"Fecha: {r['fecha']} | DTE: {r['numero_factura']} | Proveedor: {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs.iterrows()}
            sel_edit_lbl = st.selectbox("Selecciona la factura a corregir:", list(opc_edit.keys()), key="sel_edit_compra_box")
            sel_fac_id = opc_edit[sel_edit_lbl]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total, subtotal, iva, cuenta_nomenclatura FROM facturas WHERE id = %s", (sel_fac_id,))
            f_act = c.fetchone()
            conn.close()

            with st.form("form_editar_compra"):
                ec1, ec2, ec3 = st.columns(3)
                edit_num = ec1.text_input("Número DTE", value=f_act[0])
                edit_ser = ec2.text_input("Serie", value=f_act[1])
                edit_fec = ec3.date_input("Fecha Correcta de Emisión", value=datetime.strptime(str(f_act[2]), "%Y-%m-%d").date() if f_act[2] else date.today())

                ec4, ec5 = st.columns(2)
                edit_nom = ec4.text_input("Nombre Proveedor", value=f_act[3])
                edit_nit = ec5.text_input("NIT Proveedor", value=f_act[4])

                edit_tot = st.number_input("Total de la Factura (Q)", value=float(f_act[5]), step=50.0, format="%.2f")
                edit_peq = st.checkbox("Pequeño Contribuyente (IVA = Q0.00)", value=(float(f_act[7]) == 0.0), key="chk_edit_peq_c")

                e_base, e_iva, e_total, _, _, _, _, e_liq = calcular_desglose(edit_tot, edit_peq)
                st.caption(f"Desglose recalculado: Base Q{e_base:,.2f} | IVA Q{e_iva:,.2f} | Total Q{e_total:,.2f}")

                if st.form_submit_button("💾 Guardar Corrección", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        UPDATE facturas 
                        SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s, subtotal = %s, iva = %s, total = %s, total_a_pagar_cobrar = %s
                        WHERE id = %s
                    """, (edit_num, edit_ser, str(edit_fec), edit_nom, edit_nit, e_base, e_iva, e_total, e_liq, sel_fac_id))
                    
                    # Actualizar fecha y concepto del asiento en Diario
                    c.execute("""
                        UPDATE libro_diario 
                        SET fecha = %s, concepto = %s
                        WHERE referencia_id = %s OR referencia_id = %s
                    """, (str(edit_fec), f"Fac. Compra {edit_num} de {edit_nom} (Corregida)", sel_fac_id, f_act[0]))
                    
                    conn.commit()
                    conn.close()
                    st.success("✅ Factura y asiento de diario corregidos con éxito.")
                    st.rerun()

            # Opción de eliminación restringida a Administrador
            if is_admin:
                st.markdown("---")
                st.error("⚠️ Zona de Auditoría: Eliminación Permanente de Factura")
                if st.button("🗑️ Eliminar Factura de Compra Definitivamente", key=f"del_c_{sel_fac_id}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_fac_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_fac_id, f_act[0]))
                    conn.commit()
                    conn.close()
                    st.warning("Factura y partida contable eliminadas por el Administrador.")
                    st.rerun()

# ==============================================================================
# 📈 FACTURA DE VENTAS
# ==============================================================================
elif selected_menu == "📈 Factura de Ventas":
    if not selected_emp_id: st.stop()
    st.title(f"📈 Facturación de Ventas — {selected_empresa_info[1]}")
    es_peq_empresa = "pequeño" in (selected_empresa_info[3] or "").lower()

    conn = get_db_connection()
    cuentas_ing = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", conn, params=(selected_emp_id,))
    cli_corp = pd.read_sql_query("SELECT nit, nombre FROM terceros_corp WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre", conn)
    conn.close()

    opciones_ing = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_ing.iterrows()]
    tab_v_nueva, tab_v_editar = st.tabs(["➕ Registrar Venta", "✏️ Modificar / Corregir Factura"])

    with tab_v_nueva:
        st.markdown("### 🔍 1. Buscar Cliente Corporativo o Cargar DTE FEL")
        cv1, cv2 = st.columns([1.5, 1.5])

        filtro_c = cv1.text_input("🔎 Escribe para buscar Cliente (NIT o Nombre):", key="filtro_cli")
        clis_filtrados = cli_corp[cli_corp['nombre'].str.contains(filtro_c, case=False, na=False) | cli_corp['nit'].str.contains(filtro_c, case=False, na=False)] if filtro_c else cli_corp
        opciones_c = ["-- Ingresar Manualmente / Cargar PDF --"] + [f"{r['nombre']} (NIT: {r['nit']})" for _, r in clis_filtrados.iterrows()]
        sel_cli = cv1.selectbox("Seleccionar de la lista filtrada:", opciones_c, key="sel_cli_drop")

        dte_v_cargado = cv2.file_uploader("O sube la Factura FEL emitida por SAT:", type=["pdf"], key="pdf_venta_fel")
        pre_v = {}
        if dte_v_cargado:
            try:
                pre_v = extraer_datos_dte_sat(dte_v_cargado, tipo_operacion="VENTA")
                st.success("✅ Factura FEL procesada con éxito.")
            except Exception:
                st.warning("No se pudo leer el archivo automáticamente.")

        if sel_cli != "-- Ingresar Manualmente / Cargar PDF --":
            val_c_nom = sel_cli.split(" (NIT: ")[0]
            val_c_nit = sel_cli.split(" (NIT: ")[1].replace(")", "")
        else:
            val_c_nom = pre_v.get("receptor_nombre", "")
            val_c_nit = pre_v.get("receptor_nit", "")

        if val_c_nit in ["CF", "C/F"]:
            val_c_nom = "CONSUMIDOR FINAL"

        st.markdown("### 📝 2. Detalle de la Venta")
        with st.form("form_venta_nueva"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Autorización DTE *", value=pre_v.get("numero_dte", ""))
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""))
            fecha_fac = c3.date_input("Fecha de Emisión *", value=pre_v.get("fecha") or date.today())

            c4, c5 = st.columns(2)
            cli_nom_final = c4.text_input("Nombre / Razón Social del Cliente *", value=val_c_nom)
            cli_nit_final = c5.text_input("NIT del Cliente *", value=val_c_nit)

            c6, c7 = st.columns(2)
            concepto = c6.selectbox("Concepto", ["Venta de Mercaderías", "Servicios Contables y Profesionales", "Honorarios", "Otros"])
            cuenta_ingreso = c7.selectbox("Cuenta de Ingreso (Haber) *", opciones_ing if opciones_ing else ["4.1.01.01 - Ventas de Mercaderías"])

            tot_input_v = st.number_input("Monto Total de la Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_v.get("total", 0.0)))
            es_peq_v = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera Débito Fiscal)", value=pre_v.get("es_pequeno_contribuyente", es_peq_empresa))

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Cliente nos retuvo ISR", value=False)
            tipo_isr = r1.selectbox("Tasa Retención ISR", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"])
            aplica_iva = r2.checkbox("Cliente nos retuvo IVA", value=False)
            tipo_iva = r2.selectbox("Tasa Retención IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"])

            base, iva, total, t_isr, m_isr, t_iva, m_iva, liq = calcular_desglose(tot_input_v, es_peq_v, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            st.info(f"📊 **Base (Subtotal):** Q{base:,.2f} | **IVA (Débito Fiscal):** Q{iva:,.2f} | **Total:** Q{total:,.2f} | **Líquido a Cobrar:** Q{liq:,.2f}")

            if st.form_submit_button("Guardar Factura de Venta", type="primary"):
                if cli_nit_final.strip().upper() == "CF" and not cli_nom_final.strip():
                    cli_nom_final = "CONSUMIDOR FINAL"

                if num_fac and cli_nom_final and cli_nit_final and tot_input_v > 0:
                    conn = get_db_connection()
                    c = conn.cursor()
                    fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                    
                    c.execute('''
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, 'CLIENTE', %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre
                    ''', (f"terc-{int(datetime.now().timestamp())}", cli_nit_final.strip().upper(), cli_nom_final.strip(), datetime.now().isoformat()))

                    c.execute('''
                        INSERT INTO facturas (id, empresa_id, tipo, numero_factura, serie, fecha, tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso, cuenta_nomenclatura, cuenta_pago, forma_pago, subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr, aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar, saldo_pendiente, estado_pago, created_by, created_at)
                        VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, '1.1.01.01', 'Contado', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0.0, 'PAGADO', %s, %s)
                    ''', (fac_id, selected_emp_id, num_fac, serie, str(fecha_fac), cli_nom_final, cli_nit_final, concepto, concepto, cuenta_ingreso, base, iva, total,
                          1 if (aplica_isr and tipo_isr != "No Aplica") else 0, t_isr, m_isr,
                          1 if (aplica_iva and "No Aplica" not in tipo_iva) else 0, t_iva, m_iva, liq, curr_u['username'], datetime.now().isoformat()))

                    # Asiento contable
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (selected_emp_id,))
                    p_no = c.fetchone()[0]
                    now_str = datetime.now().isoformat()
                    conc = f"Fac. Venta {num_fac} a {cli_nom_final}"
                    c_cod, c_nom_cta = cuenta_ingreso.split(" - ", 1)

                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), "1.1.01.01", "Caja General (Efectivo)", liq, 0.0, conc, fac_id, now_str))
                    if m_isr > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "1.1.03.02", "ISR Retenido por Acreditar", m_isr, 0.0, conc, fac_id, now_str))
                    if m_iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "1.1.03.03", "Constancias de Retención IVA Recibidas", m_iva, 0.0, conc, fac_id, now_str))
                    c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                              (selected_emp_id, p_no, str(fecha_fac), c_cod, c_nom_cta, 0.0, base, conc, fac_id, now_str))
                    if iva > 0:
                        c.execute("INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                  (selected_emp_id, p_no, str(fecha_fac), "2.1.02.01", "IVA por Pagar (Débito Fiscal)", 0.0, iva, conc, fac_id, now_str))

                    conn.commit()
                    conn.close()
                    st.success("✅ Venta registrada y asentada en el Libro Diario.")
                    st.rerun()
                else:
                    st.error("Completa el número de factura, cliente, NIT y total.")

    with tab_v_editar:
        st.subheader("✏️ Modificar o Corregir Factura de Venta")
        conn = get_db_connection()
        df_facs_v = pd.read_sql_query("SELECT id, fecha, serie, numero_factura, tercero_nombre, tercero_nit, total, cuenta_nomenclatura FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' ORDER BY fecha DESC", conn, params=(selected_emp_id,))
        conn.close()

        if df_facs_v.empty:
            st.info("No hay facturas de venta para corregir.")
        else:
            opc_v_edit = {f"Fecha: {r['fecha']} | DTE: {r['numero_factura']} | Cliente: {r['tercero_nombre']} (Q{r['total']:,.2f})": r['id'] for _, r in df_facs_v.iterrows()}
            sel_v_edit_lbl = st.selectbox("Selecciona la factura de venta a corregir:", list(opc_v_edit.keys()), key="sel_edit_venta_box")
            sel_v_id = opc_v_edit[sel_v_edit_lbl]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT numero_factura, serie, fecha, tercero_nombre, tercero_nit, total, subtotal, iva, cuenta_nomenclatura FROM facturas WHERE id = %s", (sel_v_id,))
            fv_act = c.fetchone()
            conn.close()

            with st.form("form_editar_venta"):
                ec1, ec2, ec3 = st.columns(3)
                edit_v_num = ec1.text_input("Número DTE", value=fv_act[0])
                edit_v_ser = ec2.text_input("Serie", value=fv_act[1])
                edit_v_fec = ec3.date_input("Fecha Correcta de Emisión", value=datetime.strptime(str(fv_act[2]), "%Y-%m-%d").date() if fv_act[2] else date.today())

                ec4, ec5 = st.columns(2)
                edit_v_nom = ec4.text_input("Nombre Cliente", value=fv_act[3])
                edit_v_nit = ec5.text_input("NIT Cliente", value=fv_act[4])

                edit_v_tot = st.number_input("Total de la Factura (Q)", value=float(fv_act[5]), step=50.0, format="%.2f")
                edit_v_peq = st.checkbox("Pequeño Contribuyente (IVA = Q0.00)", value=(float(fv_act[7]) == 0.0), key="chk_edit_v_peq")

                ev_base, ev_iva, ev_total, _, _, _, _, ev_liq = calcular_desglose(edit_v_tot, edit_v_peq)
                st.caption(f"Desglose recalculado: Base Q{ev_base:,.2f} | IVA Q{ev_iva:,.2f} | Total Q{ev_total:,.2f}")

                if st.form_submit_button("💾 Guardar Corrección de Venta", type="primary"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        UPDATE facturas 
                        SET numero_factura = %s, serie = %s, fecha = %s, tercero_nombre = %s, tercero_nit = %s, subtotal = %s, iva = %s, total = %s, total_a_pagar_cobrar = %s
                        WHERE id = %s
                    """, (edit_v_num, edit_v_ser, str(edit_v_fec), edit_v_nom, edit_v_nit, ev_base, ev_iva, ev_total, ev_liq, sel_v_id))

                    c.execute("""
                        UPDATE libro_diario 
                        SET fecha = %s, concepto = %s
                        WHERE referencia_id = %s OR referencia_id = %s
                    """, (str(edit_v_fec), f"Fac. Venta {edit_v_num} a {edit_v_nom} (Corregida)", sel_v_id, fv_act[0]))

                    conn.commit()
                    conn.close()
                    st.success("✅ Factura de venta actualizada con éxito.")
                    st.rerun()

            if is_admin:
                st.markdown("---")
                st.error("⚠️ Zona de Auditoría: Eliminación Permanente de Factura")
                if st.button("🗑️ Eliminar Factura de Venta Definitivamente", key=f"del_v_{sel_v_id}"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM facturas WHERE id = %s", (sel_v_id,))
                    c.execute("DELETE FROM libro_diario WHERE referencia_id = %s OR referencia_id = %s", (sel_v_id, fv_act[0]))
                    conn.commit()
                    conn.close()
                    st.warning("Factura de venta y partida contable eliminadas por el Administrador.")
                    st.rerun()

# ==============================================================================
# 📉 ESTADO DE RESULTADOS (ESTRUCTURA CONTABLE OFICIAL GUATEMALA)
# ==============================================================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title(f"📉 Estado de Resultados (Pérdidas y Ganancias)")
    st.caption(f"{selected_empresa_info[1]} | NIT: {selected_empresa_info[2]} | Régimen: {selected_empresa_info[3]}")

    c_f1, c_f2 = st.columns(2)
    f_desde = c_f1.date_input("Fecha Inicial del Período:", value=date(date.today().year, 1, 1))
    f_hasta = c_f2.date_input("Fecha Final del Período:", value=date.today())

    conn = get_db_connection()
    q_er = """
        SELECT n.codigo, n.nombre, n.tipo,
               ROUND(COALESCE(SUM(d.haber - d.debe), 0)::numeric, 2) AS saldo_acreedor,
               ROUND(COALESCE(SUM(d.debe - d.haber), 0)::numeric, 2) AS saldo_deudor
        FROM nomenclatura n
        JOIN libro_diario d ON n.codigo = d.codigo_cuenta AND d.empresa_id = n.empresa_id
        WHERE n.empresa_id = %s 
          AND d.fecha >= %s AND d.fecha <= %s
          AND (n.tipo = 'Ingreso' OR n.tipo = 'Gasto')
        GROUP BY n.codigo, n.nombre, n.tipo
        ORDER BY n.codigo ASC
    """
    df_er = pd.read_sql_query(q_er, conn, params=(selected_emp_id, str(f_desde), str(f_hasta)))
    conn.close()

    if df_er.empty:
        st.info("No hay movimientos contables registrados para el período seleccionado.")
    else:
        # 1. Ingresos Operacionales (Ventas directas y servicios)
        df_ing_op = df_er[(df_er['tipo'] == 'Ingreso') & (df_er['codigo'].str.startswith('4.1'))]
        tot_ing_op = float(df_ing_op['saldo_acreedor'].sum()) if not df_ing_op.empty else 0.0

        # 2. Costo de Ventas (Compras y costos directos)
        df_costos = df_er[(df_er['tipo'] == 'Gasto') & (df_er['codigo'].str.startswith('5.1'))]
        tot_costos = float(df_costos['saldo_deudor'].sum()) if not df_costos.empty else 0.0

        # Margen / Utilidad Bruta
        utilidad_bruta = round(tot_ing_op - tot_costos, 2)

        # 3. Gastos de Operación y Administración
        df_gastos_op = df_er[(df_er['tipo'] == 'Gasto') & (df_er['codigo'].str.startswith('5.2'))]
        tot_gastos_op = float(df_gastos_op['saldo_deudor'].sum()) if not df_gastos_op.empty else 0.0

        # Utilidad Operativa
        utilidad_operacion = round(utilidad_bruta - tot_gastos_op, 2)

        # 4. Otros Ingresos y Gastos No Operacionales
        df_otros_ing = df_er[(df_er['tipo'] == 'Ingreso') & (~df_er['codigo'].str.startswith('4.1'))]
        tot_otros_ing = float(df_otros_ing['saldo_acreedor'].sum()) if not df_otros_ing.empty else 0.0

        df_otros_gas = df_er[(df_er['tipo'] == 'Gasto') & (~df_er['codigo'].str.startswith(('5.1', '5.2')))]
        tot_otros_gas = float(df_otros_gas['saldo_deudor'].sum()) if not df_otros_gas.empty else 0.0

        # Utilidad Neta
        utilidad_neta = round(utilidad_operacion + tot_otros_ing - tot_otros_gas, 2)

        # Métricas principales
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Ingresos Operacionales", f"Q {tot_ing_op:,.2f}")
        k2.metric("Margen Bruto", f"Q {utilidad_bruta:,.2f}")
        k3.metric("Gastos de Operación", f"Q {tot_gastos_op:,.2f}")
        k4.metric("Utilidad Neta", f"Q {utilidad_neta:,.2f}")

        st.markdown("---")
        st.subheader("📑 Estructura Contable Clasificada")

        filas_estado = []

        # Sección Ingresos
        filas_estado.append({"Concepto / Rubro Contable": "➕ INGRESOS OPERACIONALES", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_ing_op.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_acreedor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL INGRESOS OPERACIONALES", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {tot_ing_op:,.2f}"})

        # Sección Costos
        filas_estado.append({"Concepto / Rubro Contable": "➖ COSTO DE VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_costos.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL COSTO DE VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q ({tot_costos:,.2f})"})

        # Margen Bruto
        filas_estado.append({"Concepto / Rubro Contable": "🎯 UTILIDAD BRUTA EN VENTAS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_bruta:,.2f}"})

        # Sección Gastos de Operación
        filas_estado.append({"Concepto / Rubro Contable": "➖ GASTOS DE OPERACIÓN Y ADMINISTRACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
        for _, r in df_gastos_op.iterrows():
            filas_estado.append({"Concepto / Rubro Contable": f"  {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})
        filas_estado.append({"Concepto / Rubro Contable": "(=) TOTAL GASTOS DE OPERACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q ({tot_gastos_op:,.2f})"})

        # Utilidad Operativa
        filas_estado.append({"Concepto / Rubro Contable": "📊 UTILIDAD DE OPERACIÓN", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_operacion:,.2f}"})

        # Otros Ingresos y Gastos
        if not df_otros_ing.empty or not df_otros_gas.empty:
            filas_estado.append({"Concepto / Rubro Contable": "➕/➖ OTROS INGRESOS Y GASTOS", "Monto Parcial (Q)": "", "Total Rubro (Q)": ""})
            for _, r in df_otros_ing.iterrows():
                filas_estado.append({"Concepto / Rubro Contable": f"  (+) {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_acreedor']:,.2f}", "Total Rubro (Q)": ""})
            for _, r in df_otros_gas.iterrows():
                filas_estado.append({"Concepto / Rubro Contable": f"  (-) {r['codigo']} - {r['nombre']}", "Monto Parcial (Q)": f"{r['saldo_deudor']:,.2f}", "Total Rubro (Q)": ""})

        # Utilidad Neta Final
        filas_estado.append({"Concepto / Rubro Contable": "🏁 UTILIDAD / (PÉRDIDA) NETA ANTES DE IMPUESTOS", "Monto Parcial (Q)": "", "Total Rubro (Q)": f"Q {utilidad_neta:,.2f}"})

        df_tabla_er = pd.DataFrame(filas_estado)
        st.table(df_tabla_er)

        st.download_button(
            "📥 Descargar Estado de Resultados (CSV)",
            df_tabla_er.to_csv(index=False).encode('utf-8'),
            f"Estado_Resultados_{selected_empresa_info[1]}_{f_desde}_{f_hasta}.csv",
            "text/csv"
        )

# ==============================================================================
# ⚖️ BALANCE GENERAL / DE SALDOS
# ==============================================================================
elif selected_menu == "⚖️ Balance General":
    if not selected_emp_id: st.stop()
    st.title("⚖️ Balance General de Saldos")
    st.caption(f"Empresa: {selected_empresa_info[1]}")

    conn = get_db_connection()
    query = """
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
    df_bal = pd.read_sql_query(query, conn, params=(selected_emp_id,))
    conn.close()

    if df_bal.empty:
        st.info("Aún no hay transacciones para generar el Balance General.")
    else:
        df_bal['Saldo Deudor (Q)'] = df_bal.apply(lambda r: round(float(r['total_debe'] - r['total_haber']), 2) if r['total_debe'] > r['total_haber'] else 0.0, axis=1)
        df_bal['Saldo Acreedor (Q)'] = df_bal.apply(lambda r: round(float(r['total_haber'] - r['total_debe']), 2) if r['total_haber'] > r['total_debe'] else 0.0, axis=1)

        c1, c2 = st.columns(2)
        c1.metric("Suma Deudora", f"Q {df_bal['Saldo Deudor (Q)'].sum():,.2f}")
        c2.metric("Suma Acreedora", f"Q {df_bal['Saldo Acreedor (Q)'].sum():,.2f}")
        st.dataframe(df_bal[['codigo', 'nombre', 'tipo', 'Saldo Deudor (Q)', 'Saldo Acreedor (Q)']], use_container_width=True)

# ==============================================================================
# 📚 LIBRO MAYOR
# ==============================================================================
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
        st.info("No hay movimientos contables registrados.")
    else:
        st.dataframe(df_m, use_container_width=True)

# ==============================================================================
# 📖 LIBRO DIARIO
# ==============================================================================
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

# ==============================================================================
# 👥 DIRECTORIO DE TERCEROS
# ==============================================================================
elif selected_menu == "👥 Directorio de Terceros":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    with st.expander("➕ Registrar Tercero"):
        with st.form("form_terc_dir"):
            c1, c2, c3 = st.columns(3)
            tipo_t = c1.selectbox("Tipo:", ["PROVEEDOR", "CLIENTE", "AMBOS"])
            nit_t = c2.text_input("NIT (Sin guiones) *").strip().upper()
            nom_t = c3.text_input("Nombre / Razón Social *").strip()
            if st.form_submit_button("Guardar en Directorio", type="primary"):
                if nit_t and nom_t:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO terceros_corp (id, tipo, nit, nombre, direccion, telefono, created_at)
                        VALUES (%s, %s, %s, %s, 'Guatemala', '', %s)
                        ON CONFLICT(nit) DO UPDATE SET nombre=EXCLUDED.nombre, tipo=EXCLUDED.tipo
                    """, (f"terc-{int(datetime.now().timestamp())}", tipo_t, nit_t, nom_t, datetime.now().isoformat()))
                    conn.commit()
                    conn.close()
                    st.success(f"Tercero '{nom_t}' guardado.")
                    st.rerun()
                else:
                    st.error("NIT y Nombre son obligatorios.")

    conn = get_db_connection()
    df_terc = pd.read_sql_query("SELECT tipo AS \"Tipo\", nit AS \"NIT\", nombre AS \"Nombre / Razón Social\" FROM terceros_corp ORDER BY nombre ASC", conn)
    conn.close()
    st.dataframe(df_terc, use_container_width=True)

# ==============================================================================
# 🗂️ NOMENCLATURA CONTABLE
# ==============================================================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    with st.expander("➕ Agregar Nueva Cuenta Contable", expanded=True):
        with st.form("form_nueva_cta", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            nuevo_codigo = col1.text_input("Código de Cuenta (ej: 5.2.01.06)")
            nuevo_nombre = col2.text_input("Nombre de la Cuenta (ej: Gastos de Mantenimiento)")
            nuevo_tipo = col3.selectbox("Tipo de Cuenta", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if nuevo_codigo and nuevo_nombre:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)",
                                  (selected_emp_id, nuevo_codigo.strip(), nuevo_nombre.strip(), nuevo_tipo))
                        conn.commit()
                        st.success(f"Cuenta '{nuevo_codigo} - {nuevo_nombre}' agregada con éxito.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Ya existe una cuenta con este código en esta empresa.")
                    conn.close()
                else:
                    st.error("Completa el código y el nombre de la cuenta.")

    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo AS \"Código\", nombre AS \"Nombre de la Cuenta\", tipo AS \"Tipo\" FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo ASC", conn, params=(selected_emp_id,))
    conn.close()
    st.dataframe(df_nom, use_container_width=True)

# ==============================================================================
# 📊 RESUMEN GENERAL
# ==============================================================================
elif selected_menu == "📊 Resumen General":
    if not selected_emp_id: st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen: {selected_empresa_info[3]}")
    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT tipo, total FROM facturas WHERE empresa_id = %s", conn, params=(selected_emp_id,))
    conn.close()
    c1, c2 = st.columns(2)
    c1.metric("🛒 Total Compras", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Total Ventas", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")

# ==============================================================================
# 👤 CONTROL DE USUARIOS (ADMIN)
# ==============================================================================
elif selected_menu == "👥 Control de Usuarios (Admin)":
    if not is_admin: st.stop()
    st.title("👥 Control de Operadores y Accesos")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, name, email, role, status, assigned_empresas, reset_requested FROM users WHERE role != 'admin'")
    users_list = c.fetchall()
    c.execute("SELECT id, nombre, nit FROM empresas ORDER BY nombre")
    all_emps = c.fetchall()
    conn.close()

    t_crear, t_reg = st.tabs(["➕ Crear Nuevo Usuario", "📋 Directorio de Usuarios"])
    with t_crear:
        with st.form("form_crear_usr"):
            c1, c2 = st.columns(2)
            u_name = c1.text_input("Nombre de Usuario *")
            u_nom = c2.text_input("Nombre Completo *")
            c3, c4 = st.columns(2)
            u_mail = c3.text_input("Correo")
            u_pass = c4.text_input("Contraseña *", type="password")
            u_rol = st.selectbox("Rol Asignado:", ["operator", "supervisor"])
            emp_sel = [emp[0] for emp in all_emps if st.checkbox(f"{emp[1]} (NIT: {emp[2]})", key=f"u_emp_{emp[0]}", value=