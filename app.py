import streamlit as st
import pandas as pd
import psycopg2
import hashlib
import hmac
import io
import re
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape
from datetime import datetime, date, timedelta, timezone

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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

_PBKDF2_ITER = 200_000

def hash_password(password):
    """Hash con sal (PBKDF2-HMAC-SHA256). Formato: pbkdf2$iteraciones$sal$hash"""
    sal = uuid.uuid4().hex[:16]
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), sal.encode(), _PBKDF2_ITER).hex()
    return f"pbkdf2${_PBKDF2_ITER}${sal}${dk}"

def hash_legacy(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verificar_password(guardado, password):
    """Devuelve (coincide, requiere_actualizar). Acepta hashes antiguos SHA-256 y los migra al iniciar sesión."""
    guardado = str(guardado or "")
    if guardado.startswith("pbkdf2$"):
        try:
            _, it, sal, dk = guardado.split("$")
            calc = hashlib.pbkdf2_hmac("sha256", password.encode(), sal.encode(), int(it)).hex()
            return hmac.compare_digest(calc, dk), False
        except Exception:
            return False, False
    ok = hmac.compare_digest(guardado, hash_legacy(password))
    return ok, ok

def num_seguro(v):
    try:
        x = float(v)
        return 0.0 if x != x else x
    except Exception:
        return 0.0

def hoy_gt():
    """Fecha actual en Guatemala (UTC-6, sin horario de verano). Evita que el servidor en UTC adelante el día."""
    return datetime.now(timezone(timedelta(hours=-6))).date()

def sembrar_nomenclatura_inicial(empresa_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM nomenclatura WHERE empresa_id = %s", (str(empresa_id),))
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
            ("1.2.01.02", "Vehículos", "Activo"),
            ("2.1.01.01", "Proveedores / Cuentas por Pagar", "Pasivo"),
            ("2.1.01.02", "Tarjetas de Crédito Corporativas por Pagar", "Pasivo"),
            ("2.1.02.01", "IVA por Pagar (Débito Fiscal)", "Pasivo"),
            ("2.1.03.01", "Retenciones ISR por Pagar", "Pasivo"),
            ("2.1.03.02", "Retenciones IVA por Pagar", "Pasivo"),
            ("2.1.03.99", "Provisión ISR por Pagar", "Pasivo"),
            ("3.1.01.01", "Capital Social / Individual", "Capital"),
            ("4.1.01.01", "Ventas de Mercaderías", "Ingreso"),
            ("4.1.01.02", "Servicios Contables y Profesionales", "Ingreso"),
            ("4.1.01.03", "Servicios de Transporte y Fletes", "Ingreso"),
            ("5.1.01.01", "Compras de Mercadería", "Gasto"),
            ("5.1.02.01", "Sueldos y Prestaciones", "Gasto"),
            ("5.1.02.02", "Honorarios Profesionales", "Gasto"),
            ("5.1.02.03", "Alquileres", "Gasto"),
            ("5.1.02.04", "Combustibles", "Gasto"),
            ("5.1.02.99", "Gastos Generales", "Gasto")
        ]
        for cod, nom, t in base:
            c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                      (str(empresa_id), cod, nom, t))
        conn.commit()
    conn.close()

def calcular_impuesto_renta(regimen_empresa, total_ingresos, utilidad_antes_impuestos):
    impuesto = 0.0
    tasa_desc = "0%"
    if "Pequeño" in str(regimen_empresa):
        impuesto = round(total_ingresos * 0.05, 2)
        tasa_desc = "5% s/ Ingresos Brutos (Pequeño Contribuyente)"
    elif "Opcional" in str(regimen_empresa) or "Simplificado" in str(regimen_empresa):
        if total_ingresos <= 30000.0:
            impuesto = round(total_ingresos * 0.05, 2)
            tasa_desc = "5% Régimen Opcional Simplificado"
        else:
            impuesto = round(1500.0 + (total_ingresos - 30000.0) * 0.07, 2)
            tasa_desc = "5% hasta Q30k y 7% s/ Excedente"
    elif "Utilidades" in str(regimen_empresa) or "General" in str(regimen_empresa):
        if utilidad_antes_impuestos > 0:
            impuesto = round(utilidad_antes_impuestos * 0.25, 2)
            tasa_desc = "25% s/ Utilidad Neta (Actividades Lucrativas)"
        else:
            impuesto = 0.0
            tasa_desc = "25% (Pérdida - Q0.00)"
    ganancia_neta = round(utilidad_antes_impuestos - impuesto, 2)
    return impuesto, ganancia_neta, tasa_desc

# ==========================================
# CÁLCULO DE RETENCIONES (COMPRAS Y VENTAS)
# ==========================================
LIMITE_RETENCION_ISR = 2800.0   # umbral sugerido por la aplicación para activar la retención de ISR

def calc_isr(base, tipo):
    """Retención ISR sobre la base imponible. Devuelve (tasa, monto).
    '7% Excedente': 5% hasta Q30,000 y 7% sobre el excedente (nunca negativo)."""
    t = str(tipo)
    base = float(base or 0.0)
    if base <= 0 or "No Aplica" in t:
        return 0.0, 0.0
    if "10%" in t:
        return 0.10, round(base * 0.10, 2)
    if "7%" in t:
        if base <= 30000.0:
            return 0.05, round(base * 0.05, 2)
        return 0.07, round(1500.0 + (base - 30000.0) * 0.07, 2)
    if "5%" in t:
        return 0.05, round(base * 0.05, 2)
    return 0.0, 0.0

def calc_iva_retenido(iva, tipo):
    """Retención de IVA sobre el IVA de la factura. Devuelve (tasa, monto)."""
    t = str(tipo)
    iva = float(iva or 0.0)
    if iva <= 0 or "No Aplica" in t:
        return 0.0, 0.0
    if "100%" in t:
        tasa = 1.0
    elif "25%" in t:
        tasa = 0.25
    elif "15%" in t:
        tasa = 0.15
    else:
        return 0.0, 0.0
    return tasa, round(iva * tasa, 2)

def calcular_retenciones(total, subtotal, iva, aplica_isr, tipo_isr, aplica_iva, tipo_iva):
    """Devuelve dict con tasas, montos y líquido a pagar (total - retenciones)."""
    t_isr, m_isr = calc_isr(subtotal, tipo_isr) if aplica_isr else (0.0, 0.0)
    t_iva, m_iva = calc_iva_retenido(iva, tipo_iva) if aplica_iva else (0.0, 0.0)
    liq = round(float(total) - m_isr - m_iva, 2)
    return {"t_isr": t_isr, "m_isr": m_isr, "t_iva": t_iva, "m_iva": m_iva, "liq": liq}

# ---- BEGIN HELPERS DTE ----
# ==========================================
# EXTRACTOR DE DATOS FEL SAT GUATEMALA (PDF y XML)
# ==========================================
_AMT = r"(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})"
_UUID = r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

def _limpiar_nit(valor):
    v = re.sub(r"[\s\-]", "", str(valor or "")).upper()
    return "CF" if v in ("CF", "C/F") else v

def _a_float(s):
    return float(str(s).replace(",", ""))

def _parse_fecha(s):
    s = str(s or "")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except Exception:
            pass
    m = re.search(r"(\d{1,2})\s*(?:de)?[\s\-/]*([A-Za-zÁÉÍÓÚáéíóú]{3,10})\.?[\s\-/,]*(?:de)?[\s\-/]*(\d{4})", s)
    if m:
        mes_txt = (m.group(2).lower().replace("á", "a").replace("é", "e").replace("í", "i")
                   .replace("ó", "o").replace("ú", "u"))[:3]
        mes = _MESES.get(mes_txt)
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1)))
            except Exception:
                pass
    return None

def _linea_tras_etiqueta(texto, patron_etiqueta):
    """Devuelve el valor que sigue a una etiqueta: en la misma línea o, si está vacía, en la siguiente."""
    m = re.search(patron_etiqueta + r"[ \t]*[:\.\-]?[ \t]*([^\n\r]*)", texto, re.I)
    if not m:
        return ""
    val = m.group(1).strip()
    if not val:
        for linea in texto[m.end():].splitlines():
            if linea.strip():
                val = linea.strip()
                break
    return val

def _limpiar_nombre(val):
    val = re.split(
        r"\s{3,}|\s+(?:NIT|Nombre|Serie|Fecha|N[uú]mero|Direcci[oó]n|Correo|Tel[eé]fono|Firma|Certificador|Documento|Autorizaci[oó]n)\b",
        str(val or ""), flags=re.I)[0]
    val = val.strip(" :-\t")
    if not val or re.fullmatch(r"[\d\-\sKk/]+", val):
        return ""
    return val

def _extraer_total(texto):
    m = list(re.finditer(r"Gran\s+Total[^0-9]{0,60}" + _AMT, texto, re.I))
    if m:
        return _a_float(m[-1].group(1))
    m = list(re.finditer(r"Total\s+(?:General|a\s+Pagar)[^0-9]{0,40}" + _AMT, texto, re.I))
    if m:
        return _a_float(m[-1].group(1))
    m = list(re.finditer(r"\bTOTAL(?:ES)?\b(?!\s*(?:IVA|impuestos?|descuentos?|exentos?|gravados?|sin|de\s+(?:IVA|impuestos?|descuentos?)))[^0-9\n]{0,30}" + _AMT,
                         texto, re.I))
    if m:
        return max(_a_float(x.group(1)) for x in m)
    return 0.0

def _parse_texto_dte(texto):
    datos = {"numero_dte": "", "numero_dte_corto": "", "serie": "", "fecha": None,
             "emisor_nombre": "", "emisor_nit": "", "receptor_nombre": "", "receptor_nit": "",
             "total": 0.0, "iva_doc": None, "es_pequeno": False, "origen": "pdf"}

    if re.search(r"Peque[ñn]o\s+Contribuyente|no\s+genera\s+derecho\s+a\s+cr[eé]dito\s+fiscal", texto, re.I):
        datos["es_pequeno"] = True

    # Número de autorización (UUID) y número de DTE
    m = re.search(r"Autorizaci[oó]n[\s:\.\-]*(?:N[uú]mero\s*)?(" + _UUID + ")", texto, re.I) or re.search("(" + _UUID + ")", texto)
    if m:
        datos["numero_dte"] = m.group(1).upper()
    m = re.search(r"N[uú]mero\s+(?:de\s+)?DTE[\s:\.\-#]*([0-9]+)", texto, re.I) or re.search(r"\bDTE\s*(?:No\.?|N[°º])?[\s:\.\-#]*([0-9]{5,})", texto, re.I)
    if m:
        datos["numero_dte_corto"] = m.group(1).strip()
        if not datos["numero_dte"]:
            datos["numero_dte"] = datos["numero_dte_corto"]

    m = re.search(r"\bSerie\b[\s:\.\-]*([A-Fa-f0-9]{6,})", texto, re.I)
    if m:
        datos["serie"] = m.group(1).upper()

    # Fecha de emisión (primero junto a la etiqueta; si no, la primera fecha del documento)
    fecha_txt = _linea_tras_etiqueta(texto, r"Fecha(?:\s+y\s+hora)?\s+de\s+emisi[oó]n")
    datos["fecha"] = _parse_fecha(fecha_txt) or _parse_fecha(texto)

    # NITs
    m = re.search(r"N\.?\s*I\.?\s*T\.?\s*(?:del\s+)?Emisor[\s:\.\-]*([0-9]+-?[0-9Kk]?)", texto, re.I)
    if m:
        datos["emisor_nit"] = _limpiar_nit(m.group(1))
    m = re.search(r"(?:N\.?\s*I\.?\s*T\.?|ID)\s*(?:del\s+)?Receptor[\s:\.\-]*(C\s*/?\s*F\b|[0-9]+-?[0-9Kk]?)", texto, re.I)
    if m:
        datos["receptor_nit"] = _limpiar_nit(m.group(1))

    # Nombres
    for et in (r"Nombre\s+(?:del\s+)?Emisor", r"Raz[oó]n\s+Social\s+(?:del\s+)?Emisor", r"\bEmisor\b"):
        val = _limpiar_nombre(_linea_tras_etiqueta(texto, et))
        if val:
            datos["emisor_nombre"] = val
            break
    for et in (r"Nombre\s+(?:del\s+)?Receptor", r"Raz[oó]n\s+Social\s+(?:del\s+)?Receptor", r"\bReceptor\b"):
        val = _limpiar_nombre(_linea_tras_etiqueta(texto, et))
        if val:
            datos["receptor_nombre"] = val
            break
    if not datos["receptor_nombre"] and datos["receptor_nit"] == "CF":
        datos["receptor_nombre"] = "CONSUMIDOR FINAL"

    datos["total"] = _extraer_total(texto)
    datos["idp_doc"] = 0.0
    datos["idp_mencionado"] = bool(re.search(r"\bIDP\b|Distribuci[oó]n\s+de\s+Petr[oó]leo|\bPETR[OÓ]LEO\b", texto, re.I))
    datos["texto"] = texto
    datos["faltantes"] = [n for n, k in (("Número DTE", "numero_dte"), ("Fecha", "fecha"), ("NIT emisor", "emisor_nit"),
                                          ("Nombre emisor", "emisor_nombre"), ("Total", "total")) if not datos.get(k)]
    return datos

def _extraer_datos_pdf(data):
    if not PdfReader:
        return {"_error": "No está instalada la librería pypdf. Agrega 'pypdf' a requirements.txt."}
    try:
        lector = PdfReader(io.BytesIO(data))
        if getattr(lector, "is_encrypted", False):
            try:
                lector.decrypt("")
            except Exception:
                return {"_error": "El PDF está protegido con contraseña."}
        intentos = []
        for modo in (None, "layout"):
            try:
                if modo:
                    txt = "\n".join((p.extract_text(extraction_mode=modo) or "") for p in lector.pages)
                else:
                    txt = "\n".join((p.extract_text() or "") for p in lector.pages)
            except Exception:
                continue
            if txt.strip():
                intentos.append(_parse_texto_dte(txt))
        if not intentos:
            return {"_error": "El PDF no contiene texto legible (parece una imagen o escaneo). Usa el PDF/XML original que emite la SAT o ingresa los datos manualmente."}
        return min(intentos, key=lambda d: len(d["faltantes"]))
    except Exception as e:
        return {"_error": f"No se pudo leer el PDF: {e}"}

def _extraer_datos_xml(data):
    low = data[:4000].lower()
    if b"<!doctype" in low or b"<!entity" in low:
        return {"_error": "XML rechazado por seguridad (contiene DOCTYPE/ENTITY)."}
    try:
        root = ET.fromstring(data)
    except Exception as e:
        return {"_error": f"No se pudo leer el XML: {e}"}

    def local(tag):
        return tag.split("}")[-1]

    def primero(nombre):
        for el in root.iter():
            if local(el.tag) == nombre:
                return el
        return None

    datos = {"numero_dte": "", "numero_dte_corto": "", "serie": "", "fecha": None,
             "emisor_nombre": "", "emisor_nit": "", "receptor_nombre": "", "receptor_nit": "",
             "total": 0.0, "iva_doc": None, "es_pequeno": False, "origen": "xml", "texto": ""}
    gen = primero("DatosGenerales")
    if gen is not None:
        datos["fecha"] = _parse_fecha(gen.attrib.get("FechaHoraEmision", ""))
    em = primero("Emisor")
    if em is not None:
        datos["emisor_nit"] = _limpiar_nit(em.attrib.get("NITEmisor", ""))
        datos["emisor_nombre"] = (em.attrib.get("NombreEmisor") or em.attrib.get("NombreComercial") or "").strip()
        datos["es_pequeno"] = str(em.attrib.get("AfiliacionIVA", "")).upper().startswith("PEQ")
    rc = primero("Receptor")
    if rc is not None:
        datos["receptor_nit"] = _limpiar_nit(rc.attrib.get("IDReceptor", ""))
        datos["receptor_nombre"] = (rc.attrib.get("NombreReceptor") or "").strip()
    na = primero("NumeroAutorizacion")
    if na is not None:
        datos["numero_dte"] = (na.text or "").strip().upper()
        datos["numero_dte_corto"] = na.attrib.get("Numero", "")
        datos["serie"] = na.attrib.get("Serie", "")
        if not datos["numero_dte"]:
            datos["numero_dte"] = datos["numero_dte_corto"]
    gt = primero("GranTotal")
    if gt is not None and (gt.text or "").strip():
        try:
            datos["total"] = _a_float(gt.text.strip())
        except Exception:
            pass
    datos["idp_doc"] = 0.0
    for el in root.iter():
        if local(el.tag) == "TotalImpuesto":
            nombre_imp = str(el.attrib.get("NombreCorto", "")).upper()
            try:
                monto_imp = _a_float(el.attrib.get("TotalMontoImpuesto", "0"))
            except Exception:
                continue
            if nombre_imp == "IVA":
                datos["iva_doc"] = monto_imp
            elif nombre_imp.startswith("PETR") or nombre_imp.startswith("IDP"):
                datos["idp_doc"] += monto_imp
    datos["idp_mencionado"] = datos["idp_doc"] > 0
    datos["faltantes"] = [n for n, k in (("Número DTE", "numero_dte"), ("Fecha", "fecha"), ("NIT emisor", "emisor_nit"),
                                          ("Nombre emisor", "emisor_nombre"), ("Total", "total")) if not datos.get(k)]
    return datos

def extraer_datos_dte_bytes(data, nombre=""):
    inicio = data.lstrip()[:1]
    if str(nombre).lower().endswith(".xml") or inicio == b"<":
        return _extraer_datos_xml(data)
    return _extraer_datos_pdf(data)

def extraer_datos_dte_sat(archivo):
    """Compatibilidad: recibe el archivo subido en Streamlit (PDF o XML) y devuelve los datos leídos."""
    try:
        data = archivo.getvalue()
    except AttributeError:
        data = archivo.read()
    return extraer_datos_dte_bytes(data, getattr(archivo, "name", ""))
# ---- END HELPERS DTE ----

@st.cache_data(show_spinner=False)
def extraer_cache(data, nombre):
    return extraer_datos_dte_bytes(data, nombre)

def cargar_dte_ui(titulo, key):
    """Muestra el cargador de PDF/XML FEL, lee los datos y devuelve (datos, firma_del_archivo)."""
    pre, sig = {}, "manual"
    archivo = st.file_uploader(titulo, type=["pdf", "xml"], key=key)
    if archivo is not None:
        sig = f"{archivo.name}_{archivo.size}"
        pre = extraer_cache(archivo.getvalue(), archivo.name)
        if pre.get("_error"):
            st.error(f"❌ {pre['_error']}")
            pre = {}
        else:
            if pre.get("numero_dte") or pre.get("total"):
                st.success(f"✅ Documento leído ({pre.get('origen', 'pdf').upper()}): DTE {pre.get('numero_dte') or 's/n'} — Total Q {pre.get('total', 0.0):,.2f}")
            if pre.get("faltantes"):
                st.warning("No se pudo leer: " + ", ".join(pre["faltantes"]) + ". Complétalos manualmente en el formulario.")
            with st.expander("🔍 Diagnóstico de lectura del documento"):
                st.json({k: str(v) for k, v in pre.items() if k not in ("texto", "faltantes")})
                if pre.get("texto"):
                    st.text_area("Texto leído del PDF", pre["texto"][:5000], height=220, key=f"diag_{key}_{sig}")
    return pre, sig

def leer_productos():
    cols = ["id", "codigo", "descripcion", "existencia", "costo_promedio"]
    conn = None
    try:
        conn = get_db_connection()
        df = pd.read_sql_query("SELECT id, codigo, descripcion, existencia, costo_promedio FROM productos ORDER BY codigo ASC", conn)
        conn.close()
        return df
    except Exception:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        return pd.DataFrame(columns=cols)

def cargar_terceros(tipos):
    """Lee clientes/proveedores del directorio (terceros_corporativos y, si existe, terceros_corp)."""
    frames = []
    for tabla in ("terceros_corporativos", "terceros_corp"):
        conn = None
        try:
            conn = get_db_connection()
            df = pd.read_sql_query(f"SELECT nit, nombre FROM {tabla} WHERE tipo = ANY(%s)", conn, params=(list(tipos),))
            conn.close()
            frames.append(df)
        except Exception:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
    if not frames:
        return pd.DataFrame(columns=["nit", "nombre"])
    df = pd.concat(frames, ignore_index=True)
    df["nit"] = df["nit"].astype(str).str.strip().str.upper()
    df = df[df["nit"] != ""].drop_duplicates(subset="nit")
    return df.sort_values("nombre").reset_index(drop=True)

def listar_proveedores(empresa_id):
    """Proveedores del directorio + proveedores que ya tienen facturas de compra en la empresa."""
    base = cargar_terceros(("PROVEEDOR", "AMBOS"))
    conn = None
    try:
        conn = get_db_connection()
        df_f = pd.read_sql_query(
            "SELECT DISTINCT tercero_nit AS nit, tercero_nombre AS nombre FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA'",
            conn, params=(str(empresa_id),))
        conn.close()
        df_f["nit"] = df_f["nit"].astype(str).str.strip().str.upper()
        base = pd.concat([base, df_f], ignore_index=True)
    except Exception:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    if base.empty:
        return pd.DataFrame(columns=["nit", "nombre"])
    base["nit_key"] = base["nit"].apply(_limpiar_nit)
    base = base[base["nit_key"] != ""].drop_duplicates(subset="nit_key")
    return base.sort_values("nombre").reset_index(drop=True)

def registrar_partida_compra(c, empresa_id, fecha, cod_d, nom_d, cod_h, nom_h, sub, iva, m_isr, m_iva, liq, num_fac, prov_nombre, now_str,
                             factura_id=None, idp=0.0, cod_idp=None, nom_idp=None):
    """Asienta la compra en el Libro Diario usando el mismo cursor/transacción de la factura (incluye IDP si existe)."""
    idp = float(idp or 0.0)
    debe_total = round(float(sub) + float(iva) + idp, 2)
    haber_total = round(float(m_isr) + float(m_iva) + float(liq), 2)
    if abs(debe_total - haber_total) > 0.01:
        raise ValueError(f"La partida no cuadra: Debe Q{debe_total:,.2f} vs Haber Q{haber_total:,.2f}")
    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(empresa_id),))
    p_no = int(c.fetchone()[0])
    glosa = f"Factura Compra {num_fac} de {prov_nombre}"
    q_ld = """
        INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    f = str(fecha)
    c.execute(q_ld, (str(empresa_id), p_no, f, str(cod_d), str(nom_d), float(sub), 0.0, glosa, str(num_fac), now_str))
    if iva > 0:
        c.execute(q_ld, (str(empresa_id), p_no, f, "1.1.03.01", "IVA por Cobrar (Crédito Fiscal)", float(iva), 0.0, glosa, str(num_fac), now_str))
    if idp > 0:
        c.execute(q_ld, (str(empresa_id), p_no, f, str(cod_idp or cod_d), str(nom_idp or nom_d), idp, 0.0, f"{glosa} (IDP)", str(num_fac), now_str))
    if m_isr > 0:
        c.execute(q_ld, (str(empresa_id), p_no, f, "2.1.03.01", "Retenciones ISR por Pagar", 0.0, float(m_isr), glosa, str(num_fac), now_str))
    if m_iva > 0:
        c.execute(q_ld, (str(empresa_id), p_no, f, "2.1.03.02", "Retenciones IVA por Pagar", 0.0, float(m_iva), glosa, str(num_fac), now_str))
    c.execute(q_ld, (str(empresa_id), p_no, f, str(cod_h), str(nom_h), 0.0, float(liq), glosa, str(num_fac), now_str))
    return p_no

# ==========================================
# INVENTARIO RÁPIDO DESDE COMPRAS
# ==========================================
def asegurar_esquema():
    """Agrega (una sola vez) la columna IDP y la tabla de líneas de inventario. No altera datos existentes."""
    if "schema_idp" in st.session_state and "schema_items" in st.session_state:
        return
    for flag, sql in (
        ("schema_idp", "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS idp NUMERIC DEFAULT 0"),
        ("schema_items", """CREATE TABLE IF NOT EXISTS factura_items (
                id TEXT PRIMARY KEY, factura_id TEXT NOT NULL, empresa_id TEXT NOT NULL, producto_id TEXT,
                descripcion TEXT, cantidad NUMERIC DEFAULT 0, costo_total NUMERIC DEFAULT 0, created_at TEXT)"""),
    ):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            conn.close()
            st.session_state[flag] = True
        except Exception:
            try:
                if conn:
                    conn.rollback()
                    conn.close()
            except Exception:
                pass
            st.session_state[flag] = False

def repartir_costos(totales, base):
    """Reparte la base de la factura entre las líneas de inventario según el peso de cada línea."""
    totales = [max(float(t or 0.0), 0.0) for t in totales]
    n = len(totales)
    if n == 0:
        return []
    s = sum(totales)
    pesos = [t / s for t in totales] if s > 0 else [1.0 / n] * n
    costos = [round(float(base) * w, 2) for w in pesos]
    costos[-1] = round(float(base) - sum(costos[:-1]), 2)
    return costos

def siguiente_codigo_producto(df_productos):
    try:
        nums = [int(re.sub(r"\D", "", str(x))) for x in df_productos["codigo"] if re.sub(r"\D", "", str(x))]
        return str(max(nums) + 1) if nums else "1"
    except Exception:
        return str(len(df_productos) + 1)

def crear_producto_db(empresa_id, codigo, descripcion, precio_venta=0.0):
    """Crea un producto en el catálogo del Kárdex (misma lógica del módulo Kárdex). Devuelve (id, error)."""
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS productos (
                id TEXT PRIMARY KEY, empresa_id TEXT, codigo TEXT NOT NULL, descripcion TEXT NOT NULL,
                existencia NUMERIC DEFAULT 0, costo_promedio NUMERIC DEFAULT 0, precio_venta NUMERIC DEFAULT 0, created_at TEXT
            );
        """)
        c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'productos'")
        info = c.fetchall()
        nombres = [r[0].lower() for r in info]
        tipos = {r[0].lower(): r[1].lower() for r in info}
        c.execute("SELECT 1 FROM productos WHERE codigo = %s", (str(codigo).strip(),))
        if c.fetchone():
            conn.rollback(); conn.close()
            return None, f"Ya existe un producto con el código {codigo}."
        ts = int(datetime.now().timestamp())
        campos, valores = [], []
        pid = None
        if "id" in nombres:
            t_id = tipos.get("id", "")
            pid = (ts % 2147483647) if ("int" in t_id or "serial" in t_id) else f"prod-{ts}-{uuid.uuid4().hex[:4]}"
            campos.append("id"); valores.append(pid)
        if "empresa_id" in nombres:
            campos.append("empresa_id"); valores.append(str(empresa_id) if empresa_id else "global")
        campos += ["codigo", "descripcion", "existencia", "costo_promedio", "precio_venta"]
        valores += [str(codigo).strip(), str(descripcion).strip(), 0.0, 0.0, float(precio_venta or 0.0)]
        if "created_at" in nombres:
            campos.append("created_at"); valores.append(datetime.now().isoformat())
        c.execute(f"INSERT INTO productos ({', '.join(campos)}) VALUES ({', '.join(['%s'] * len(campos))})", tuple(valores))
        conn.commit(); conn.close()
        return pid, None
    except Exception as e:
        try:
            if conn:
                conn.rollback(); conn.close()
        except Exception:
            pass
        return None, str(e)

# ==========================================
# EXPORTACIÓN PDF FISCAL Y REPORTES
# ==========================================
def _celda_pdf(val):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"Q {val:,.2f}"
    return _xml_escape(str(val if val is not None else ""))

def exportar_libro_pdf(df_datos, titulo_libro, empresa_nom, empresa_nit, resolucion_sat, folio_inicio=1):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=35)
    elementos = []
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)

    elementos.append(Paragraph(f"<b>{_xml_escape(empresa_nom.upper())}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {_xml_escape(str(empresa_nit))} | <b>{_xml_escape(titulo_libro.upper())}</b>", estilo_sub))
    elementos.append(Paragraph(f"Resolución SAT No.: {_xml_escape(str(resolucion_sat))}", estilo_sub))
    elementos.append(Spacer(1, 10))

    datos_tabla = [[Paragraph(f"<b>{_xml_escape(str(c))}</b>", estilos['Normal']) for c in df_datos.columns]]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            fila_txt.append(Paragraph(_celda_pdf(val), ParagraphStyle('Celda', parent=estilos['Normal'], fontSize=7)))
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
        canvas.drawRightString(760, 15, pie_txt)
        canvas.restoreState()

    doc.build(elementos, onFirstPage=agregar_pie_sat, onLaterPages=agregar_pie_sat)
    buffer.seek(0)
    return buffer

def exportar_reporte_pdf(df_datos, titulo, empresa_nom, empresa_nit, periodo_txt=""):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter) if len(df_datos.columns) > 5 else letter, leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=30)
    elementos = []
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle('T1', parent=estilos['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=1, textColor=colors.HexColor("#1E3A8A"))
    estilo_sub = ParagraphStyle('T2', parent=estilos['Normal'], fontName='Helvetica', fontSize=8, alignment=1)

    elementos.append(Paragraph(f"<b>{_xml_escape(empresa_nom.upper())}</b>", estilo_titulo))
    elementos.append(Paragraph(f"NIT: {_xml_escape(str(empresa_nit))} | <b>{_xml_escape(titulo.upper())}</b>", estilo_sub))
    if periodo_txt:
        elementos.append(Paragraph(f"Período: {_xml_escape(periodo_txt)}", estilo_sub))
    elementos.append(Spacer(1, 10))

    cols = [Paragraph(f"<b>{_xml_escape(str(c))}</b>", ParagraphStyle('H', parent=estilos['Normal'], fontSize=7, fontName='Helvetica-Bold')) for c in df_datos.columns]
    datos_tabla = [cols]
    for _, fila in df_datos.iterrows():
        fila_txt = []
        for val in fila:
            fila_txt.append(Paragraph(_celda_pdf(val), ParagraphStyle('C', parent=estilos['Normal'], fontSize=7)))
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

# ==========================================
# GESTIÓN DE SESIÓN Y LOGIN
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

STATUS_BLOQUEADOS = {"inactive", "inactivo", "disabled", "suspended", "suspendido", "bloqueado", "blocked"}

def _master_passwords():
    """Claves maestras SOLO para el usuario 'admin'. Recomendado: definirlas en st.secrets['MASTER_PASSWORDS']."""
    try:
        mp = st.secrets.get("MASTER_PASSWORDS")
        if mp:
            return [mp] if isinstance(mp, str) else list(mp)
    except Exception:
        pass
    return ["admin123", "Matgozmaster2026#", "Matgozmaster2026JGK"]

def show_login():
    st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>💼 Servicios Contables Matgoz</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #64748B;'>Sistema ERP Contable y Financiero Multiempresa</p>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1.5, 1])
    with c2:
        with st.form("login_form"):
            u = st.text_input("Usuario", placeholder="admin")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión", type="primary", use_container_width=True):
                if u and p:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("SELECT id, username, name, email, role, status, assigned_empresas, password_hash FROM users WHERE LOWER(username) = LOWER(%s)", (u.strip(),))
                    row = c.fetchone()
                    master_passwords = _master_passwords()
                    master_ok = (u.strip().lower() == "admin") and (p in master_passwords)
                    pw_ok, pw_upgrade = verificar_password(row[7], p) if row else (False, False)
                    if row:
                        if str(row[5] or "").strip().lower() in STATUS_BLOQUEADOS:
                            conn.close()
                            st.error("Este usuario está inactivo. Contacta al administrador.")
                        elif master_ok or pw_ok:
                            if pw_ok and pw_upgrade and not master_ok:
                                try:
                                    c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(p), row[0]))
                                    conn.commit()
                                except Exception:
                                    conn.rollback()
                            st.session_state.user = {
                                "id": row[0], "username": row[1], "name": row[2],
                                "email": row[3], "role": row[4], "status": row[5],
                                "assigned_empresas": row[6]
                            }
                            conn.close()
                            st.success("Acceso concedido.")
                            st.rerun()
                        else:
                            conn.close()
                            st.error("Contraseña incorrecta.")
                    else:
                        if master_ok:
                            c.execute("""
                                INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
                                VALUES ('usr-admin', 'admin', %s, 'Administrador Principal', 'admin@matgoz.com', 'admin', 'active', 'all', %s)
                                ON CONFLICT (username) DO NOTHING
                            """, (hash_password(p), datetime.now().isoformat()))
                            conn.commit()
                            st.session_state.user = {
                                "id": "usr-admin", "username": "admin", "name": "Administrador Principal",
                                "email": "admin@matgoz.com", "role": "admin", "status": "active",
                                "assigned_empresas": "all"
                            }
                            conn.close()
                            st.success("Administrador iniciado.")
                            st.rerun()
                        else:
                            conn.close()
                            st.error("Usuario no encontrado.")
                else:
                    st.warning("Ingresa usuario y contraseña.")

if not st.session_state.user:
    show_login()
    st.stop()

def refrescar_usuario_sesion():
    """Sincroniza rol, estado y empresas asignadas desde la BD en cada recarga (los cambios del admin aplican sin volver a entrar)."""
    u = st.session_state.get("user")
    if not u:
        return
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username, name, email, role, status, assigned_empresas FROM users WHERE LOWER(username) = LOWER(%s)", (u["username"],))
        row = c.fetchone()
        conn.close()
    except Exception:
        return
    if (not row) or str(row[5] or "").strip().lower() in STATUS_BLOQUEADOS:
        st.session_state.user = None
        st.warning("Tu sesión fue cerrada: el usuario ya no está activo.")
        st.rerun()
    u.update({"id": row[0], "username": row[1], "name": row[2], "email": row[3],
              "role": row[4], "status": row[5], "assigned_empresas": row[6]})

refrescar_usuario_sesion()
asegurar_esquema()
curr_u = st.session_state.user
is_admin = curr_u["role"] == "admin"

# ==========================================
# SIDEBAR
# ==========================================
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
    st.caption(f"Cuenta: @{curr_u['username']} | {'🛡️ Administrador' if is_admin else '👤 Operador'}")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.user = None
        st.rerun()
    st.divider()

    if empresas_list:
        emp_dict = {f"{e[1]} (NIT: {e[2]})": e[0] for e in empresas_list}
        sel_label = st.selectbox("🏢 Empresa Activa:", list(emp_dict.keys()), key="sb_emp_activa")
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
        menu_options.append("👤 Control de Usuarios (Admin)")
        menu_options.append("🏢 Gestión de Empresas (Admin)")

    selected_menu = st.radio("Navegación:", menu_options, key="main_nav_radio")

# ==========================================
# 1. RESUMEN GENERAL
# ==========================================
if selected_menu == "📊 Resumen General":
    if not selected_emp_id: st.stop()
    st.title(f"📊 Panel Principal: {selected_empresa_info[1]}")
    st.caption(f"NIT: {selected_empresa_info[2]} | Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_f = pd.read_sql_query("SELECT tipo, total, saldo_pendiente, estado_pago FROM facturas WHERE empresa_id = %s", conn, params=(str(selected_emp_id),))
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🛒 Compras Acumuladas", f"Q {df_f[df_f['tipo']=='COMPRA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c2.metric("📈 Ventas Acumuladas", f"Q {df_f[df_f['tipo']=='VENTA']['total'].sum() if not df_f.empty else 0:,.2f}")
    c3.metric("💳 Cuentas por Pagar (CXP)", f"Q {df_f[(df_f['tipo']=='COMPRA') & (df_f['estado_pago']=='PENDIENTE')]['saldo_pendiente'].sum() if not df_f.empty else 0:,.2f}")
    c4.metric("💰 Cuentas por Cobrar (CXC)", f"Q {df_f[(df_f['tipo']=='VENTA') & (df_f['estado_pago']=='PENDIENTE')]['saldo_pendiente'].sum() if not df_f.empty else 0:,.2f}")

# ==========================================
# 2. FACTURA DE COMPRAS
# ==========================================
elif selected_menu in ["🛒 Factura de Compras", "🛒 Compras y Gastos"]:
    if not selected_emp_id:
        st.warning("Selecciona una empresa primero.")
        st.stop()

    st.title("🛒 Facturas de Compras, Gastos e Inventario")
    st.caption(f"Empresa activa: {selected_empresa_info[1]}")

    if "c_nonce" not in st.session_state:
        st.session_state.c_nonce = 0
    if "c_lineas" not in st.session_state:
        st.session_state.c_lineas = []
    if "c_line_nonce" not in st.session_state:
        st.session_state.c_line_nonce = 0

    t_fc_reg, t_fc_hist = st.tabs(["➕ Registrar Compra / Gasto", "📋 Historial de Compras"])

    with t_fc_reg:
        if st.session_state.get("c_flash"):
            st.success(st.session_state.pop("c_flash"))
        conn = get_db_connection()
        cuentas_gasto = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Gasto' OR tipo = 'Activo') ORDER BY codigo", conn, params=(str(selected_emp_id),))
        cuentas_pasivo_pago = pd.read_sql_query("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Pasivo' OR tipo = 'Activo') ORDER BY codigo", conn, params=(str(selected_emp_id),))
        conn.close()
        productos_bd = leer_productos()
        prov_corp = cargar_terceros(("PROVEEDOR", "AMBOS"))

        opciones_debe = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_gasto.iterrows()]
        opciones_haber = [f"{r['codigo']} - {r['nombre']}" for _, r in cuentas_pasivo_pago.iterrows()]
        lista_cuentas_debe = opciones_debe if opciones_debe else ["5.1.01.01 - Compras de Mercadería"]

        # Selector de modalidad: Documento Fiscal (PDF/Manual) o No Fiscal (Vales/Recibos)
        tipo_doc = st.radio("Tipo de Documento:", ["📄 Factura DTE / Fiscal SAT", "🧾 Documento No Fiscal (Vale / Recibo)"], horizontal=True, key="c_tipodoc")
        es_no_fiscal = "No Fiscal" in tipo_doc

        pre_datos = {}
        sig_c = "manual"
        if not es_no_fiscal:
            modo_ingreso = st.radio("Método de Ingreso:", ["📄 Carga desde PDF (FEL SAT)", "✍️ Ingreso Manual"], horizontal=True, key="c_modoing")
            if modo_ingreso == "📄 Carga desde PDF (FEL SAT)":
                pre_datos, sig_c = cargar_dte_ui("Sube el PDF o XML emitido por SAT (FEL)", "pdf_compra_fel")

        # La clave dinámica fuerza a Streamlit a rellenar los campos con los datos del archivo cargado
        base = f"{st.session_state.c_nonce}_{sig_c}"

        with st.expander("➕ Formulario de Registro de Compra / Gasto", expanded=True):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / Documento *", value=pre_datos.get("numero_dte", ""), key=f"fc_num_{base}")
            serie = c2.text_input("Serie", value=pre_datos.get("serie", ""), key=f"fc_ser_{base}")
            fecha_fac = c3.date_input("Fecha de Emisión", value=pre_datos.get("fecha") or hoy_gt(), key=f"fc_fec_{base}")

            c4, c5 = st.columns(2)
            SENT_PROV = "-- Ingresar Manual / Desde PDF --"
            dict_provs = {f"{r['nombre']} (NIT: {r['nit']})": (r['nombre'], r['nit']) for _, r in prov_corp.iterrows()}
            lista_prov = [SENT_PROV] + list(dict_provs.keys())
            idx_prov = 0
            nit_pdf = _limpiar_nit(pre_datos.get("emisor_nit", ""))
            if nit_pdf:
                for i_p, (lbl_p, (nm_p, nt_p)) in enumerate(dict_provs.items(), start=1):
                    if _limpiar_nit(nt_p) == nit_pdf:
                        idx_prov = i_p
                        break
            prov_seleccionado = c4.selectbox("Buscar Proveedor en Directorio Corporativo (Opcional):", lista_prov, index=idx_prov, key=f"fc_sel_prov_{base}")
            psig = hashlib.md5(prov_seleccionado.encode()).hexdigest()[:6]

            if prov_seleccionado != SENT_PROV:
                val_nom, val_nit = dict_provs[prov_seleccionado]
            else:
                val_nom = pre_datos.get("emisor_nombre", "")
                val_nit = pre_datos.get("emisor_nit", "")

            prov_nombre = c4.text_input("Nombre o Razón Social del Proveedor *", value=val_nom, key=f"fc_prv_{base}_{psig}")
            prov_nit = c5.text_input("NIT del Proveedor *", value=val_nit, key=f"fc_nit_{base}_{psig}")

            # SELECTOR OBLIGATORIO: BIEN O SERVICIO
            st.markdown("---")
            tipo_adquisicion = st.radio("Tipo de Adquisición Contable:", ["🛍️ Bien (Mercadería / Activo Fijo)", "🛠️ Servicio"], horizontal=True, key="fc_tipoadq")

            es_inventario = False
            lineas = st.session_state.c_lineas

            if tipo_adquisicion == "🛍️ Bien (Mercadería / Activo Fijo)":
                destino_bien = st.selectbox("Destino del Bien:", ["Mercadería para Inventario (Kárdex)", "Activo Fijo / Uso Operativo"], key="fc_dest_bien")

                if destino_bien == "Mercadería para Inventario (Kárdex)":
                    es_inventario = True
                    ln = st.session_state.c_line_nonce

                    st.markdown("#### 📦 Productos que ingresan al Kárdex")
                    st.caption("Agrega una línea por cada producto de la factura. Puedes agregar todas las que necesites.")

                    # ---- Buscador de producto ----
                    buscar_prod = st.text_input("🔎 Buscar producto (nombre o código)", key=f"fc_busq_{ln}", placeholder="Escribe parte del nombre...")
                    q_busq = buscar_prod.strip()
                    if q_busq and not productos_bd.empty:
                        patron_b = re.escape(q_busq)
                        df_filtrado = productos_bd[productos_bd["descripcion"].astype(str).str.contains(patron_b, case=False, na=False)
                                                   | productos_bd["codigo"].astype(str).str.contains(patron_b, case=False, na=False)]
                    else:
                        df_filtrado = productos_bd

                    dict_prods = {f"[{r['codigo']}] {r['descripcion']} (Stock: {r['existencia']})": r['id'] for _, r in df_filtrado.iterrows()}
                    etiquetas_prod = list(dict_prods.keys())
                    SENT_PROD = "-- Seleccionar Producto --"
                    opciones_k = [SENT_PROD] + etiquetas_prod

                    # Selección automática: producto recién creado o única coincidencia de la búsqueda
                    idx_prod = 0
                    prod_pre = st.session_state.get("c_prod_sel")
                    if prod_pre is not None:
                        for i_k, lbl_k in enumerate(etiquetas_prod, start=1):
                            if str(dict_prods[lbl_k]) == str(prod_pre):
                                idx_prod = i_k
                                break
                    elif q_busq and len(etiquetas_prod) == 1:
                        idx_prod = 1

                    if q_busq and not etiquetas_prod:
                        st.warning(f"No hay productos que coincidan con «{q_busq}». Créalo aquí abajo en «Crear producto nuevo».")

                    qsig = hashlib.md5(q_busq.encode()).hexdigest()[:6]
                    sel_p = st.selectbox("Producto:", opciones_k, index=idx_prod, key=f"fc_prod_k_{ln}_{qsig}_{len(etiquetas_prod)}")

                    # ---- Crear producto nuevo sin salir del módulo ----
                    with st.expander("➕ ¿El producto no existe? Créalo aquí mismo (se agrega al Kárdex)"):
                        np1, np2, np3 = st.columns([1, 3, 1.5])
                        np_cod = np1.text_input("Código", value=siguiente_codigo_producto(productos_bd), key=f"fc_np_cod_{ln}")
                        np_desc = np2.text_input("Descripción *", value=q_busq, key=f"fc_np_desc_{ln}_{qsig}")
                        np_prec = np3.number_input("Precio venta Q (opcional)", min_value=0.0, step=10.0, format="%.2f", key=f"fc_np_prec_{ln}")
                        if st.button("💾 Crear producto y seleccionarlo", key=f"fc_np_btn_{ln}"):
                            if not np_desc.strip() or not np_cod.strip():
                                st.error("Código y descripción son obligatorios.")
                            else:
                                nuevo_id, err_np = crear_producto_db(selected_emp_id, np_cod.strip(), np_desc.strip(), np_prec)
                                if err_np:
                                    st.error(f"❌ {err_np}")
                                else:
                                    st.session_state["c_prod_sel"] = nuevo_id
                                    st.session_state.c_line_nonce += 1
                                    st.session_state["c_flash_prod"] = f"✅ Producto «{np_desc.strip()}» creado. Ya está seleccionado: indica cantidad y total de la línea."
                                    st.rerun()

                    if st.session_state.get("c_flash_prod"):
                        st.success(st.session_state.pop("c_flash_prod"))

                    # ---- Cantidad, total de la línea y botón agregar ----
                    l1, l2, l3 = st.columns([1.2, 1.6, 1])
                    cant_linea = l1.number_input("Cantidad *", min_value=0.0, step=1.0, value=1.0, format="%.2f", key=f"fc_lcant_{ln}")
                    total_linea = l2.number_input("Total de la línea Q (como aparece en la factura)", min_value=0.0, step=10.0, format="%.2f", key=f"fc_ltot_{ln}",
                                                  help="Déjalo en 0 si la factura tiene un solo producto: se asigna toda la base de la factura.")
                    l3.markdown("&nbsp;", unsafe_allow_html=True)
                    if l3.button("➕ Agregar línea", key=f"fc_ladd_{ln}"):
                        if sel_p == SENT_PROD:
                            st.error("Selecciona un producto (o créalo) antes de agregar la línea.")
                        elif cant_linea <= 0:
                            st.error("La cantidad debe ser mayor a 0.")
                        else:
                            st.session_state.c_lineas.append({
                                "pid": dict_prods[sel_p], "label": sel_p.split(" (Stock:")[0],
                                "cant": float(cant_linea), "total": float(total_linea)
                            })
                            st.session_state.pop("c_prod_sel", None)
                            st.session_state.c_line_nonce += 1
                            st.rerun()

                    # ---- Líneas agregadas ----
                    if lineas:
                        st.markdown("**Líneas agregadas a esta factura:**")
                        for i_l, lin in enumerate(lineas):
                            cl = st.columns([4, 1.3, 1.6, 0.8])
                            cl[0].write(f"{i_l + 1}. {lin['label']}")
                            cl[1].write(f"Cant: {lin['cant']:,.2f}")
                            cl[2].write(f"Q {lin['total']:,.2f}" if lin['total'] > 0 else "Total: base de la factura")
                            if cl[3].button("❌", key=f"fc_ldel_{i_l}_{ln}"):
                                st.session_state.c_lineas.pop(i_l)
                                st.rerun()
                    else:
                        st.info("Aún no hay líneas. Agrega al menos un producto para que se actualice el Kárdex.")

            st.markdown("---")
            col_fp1, col_fp2 = st.columns(2)
            tipo_pago = col_fp1.selectbox("Condición de Pago:", ["Contado", "Crédito"], key="fc_tpago")
            metodo_det = ""
            if tipo_pago == "Contado":
                metodo_det = col_fp2.selectbox("Método de Pago:", ["Efectivo", "Transferencia / Cheque", "Tarjeta de Crédito / Débito"], key="fc_met")
                cod_h_def = "1.1.01.01" if metodo_det == "Efectivo" else "1.1.01.02"
            else:
                col_fp2.info("Se asentará a Cuentas por Pagar (Proveedores).")
                cod_h_def = "2.1.01.01"
            idx_h = next((i for i, x in enumerate(opciones_haber) if cod_h_def in x), 0)

            c6, c7 = st.columns(2)
            cod_d_def = "1.1.04.01" if es_inventario else "5.1.01.01"
            idx_def_debe = next((i for i, x in enumerate(lista_cuentas_debe) if cod_d_def in x), 0)
            cuenta_debe = c6.selectbox("Cuenta de Cargo / DEBE *", lista_cuentas_debe, index=idx_def_debe, key=f"fc_cdebe_{cod_d_def}")
            cuenta_haber = c7.selectbox("Cuenta de Abono / HABER *", opciones_haber if opciones_haber else ["2.1.01.01 - Proveedores / Cuentas por Pagar"], index=idx_h, key=f"fc_chaber_{cod_h_def}")

            # Ingreso del Total Facturado
            total_ingresado = st.number_input("Total Facturado Q *", min_value=0.0, step=50.0, format="%.2f", value=float(pre_datos.get("total", 0.0) or 0.0), key=f"fc_tot_{base}")
            total_ingresado = float(total_ingresado)

            es_pequeno_prov = False
            if not es_no_fiscal:
                es_pequeno_prov = st.checkbox("¿Proveedor es Pequeño Contribuyente? (Sin Crédito Fiscal IVA)", value=bool(pre_datos.get("es_pequeno", False)), key=f"fc_peq_{base}")

            # ---- IDP (Impuesto a la Distribución de Petróleo) ----
            monto_idp = 0.0
            cuenta_idp = cuenta_debe
            if not es_no_fiscal:
                idp_doc_val = float(pre_datos.get("idp_doc", 0.0) or 0.0)
                if pre_datos.get("idp_mencionado") and idp_doc_val <= 0:
                    st.info("⛽ El documento menciona IDP. Activa la casilla de IDP e ingresa el monto.")
                tiene_idp = st.checkbox("⛽ La factura incluye IDP (Impuesto a la Distribución de Petróleo / combustibles)", value=idp_doc_val > 0, key=f"fc_idpchk_{base}")
                if tiene_idp:
                    i1, i2, i3 = st.columns(3)
                    galones_idp = i1.number_input("Galones (opcional)", min_value=0.0, step=1.0, format="%.2f", key=f"fc_gal_{base}")
                    tasa_gal_idp = i2.number_input("IDP por galón Q (opcional)", min_value=0.0, step=0.01, format="%.4f", key=f"fc_tgal_{base}")
                    idp_manual = i3.number_input("Monto total de IDP Q", min_value=0.0, step=1.0, format="%.2f", value=idp_doc_val, key=f"fc_idpm_{base}")
                    monto_idp = float(idp_manual) if idp_manual > 0 else round(float(galones_idp) * float(tasa_gal_idp), 2)
                    idx_cidp = lista_cuentas_debe.index(cuenta_debe) if cuenta_debe in lista_cuentas_debe else 0
                    csig = hashlib.md5(cuenta_debe.encode()).hexdigest()[:6]
                    cuenta_idp = st.selectbox("Cuenta contable de cargo del IDP", lista_cuentas_debe, index=idx_cidp, key=f"fc_cidp_{base}_{csig}")
                    st.caption(f"IDP registrado: Q {monto_idp:,.2f}. El IDP no forma parte de la base del IVA ni genera crédito fiscal; se registra como costo/gasto en la cuenta elegida.")
                    if monto_idp >= total_ingresado > 0:
                        st.error("El IDP no puede ser igual o mayor al total facturado.")
                        monto_idp = 0.0

            # ---- Desglose de impuestos ----
            base_con_iva = max(total_ingresado - monto_idp, 0.0)
            if es_no_fiscal or es_pequeno_prov:
                subtotal = round(base_con_iva, 2)
                iva = 0.0
            else:
                iva_doc = pre_datos.get("iva_doc")
                if iva_doc is not None and abs(total_ingresado - float(pre_datos.get("total", 0.0) or 0.0)) < 0.01:
                    iva = round(float(iva_doc), 2)          # IVA real tomado del XML de la SAT
                    subtotal = round(base_con_iva - iva, 2)
                else:
                    subtotal = round(base_con_iva / 1.12, 2)
                    iva = round(base_con_iva - subtotal, 2)

            # ---- Resumen de líneas de inventario (reparto del costo) ----
            costos_lineas = []
            prorratear_ok = True
            idp_a_inventario = bool(es_inventario and monto_idp > 0 and str(cuenta_idp).startswith("1.1.04"))
            base_inventario = round(subtotal + (monto_idp if idp_a_inventario else 0.0), 2)
            if es_inventario and lineas:
                costos_lineas = repartir_costos([l["total"] for l in lineas], base_inventario)
                suma_lineas = round(sum(l["total"] for l in lineas), 2)
                st.markdown("**Costo que ingresará al Kárdex (sin IVA):**")
                df_costos = pd.DataFrame([{
                    "Producto": l["label"], "Cantidad": l["cant"], "Total línea (Q)": l["total"],
                    "Costo Kárdex (Q)": cst, "Costo unitario (Q)": round(cst / l["cant"], 4) if l["cant"] > 0 else 0.0
                } for l, cst in zip(lineas, costos_lineas)])
                st.dataframe(df_costos, use_container_width=True)
                if len(lineas) > 1 and any(l["total"] <= 0 for l in lineas):
                    st.error("Con más de una línea, cada línea debe tener su total (> 0) para repartir el costo correctamente.")
                    prorratear_ok = False
                elif suma_lineas > 0 and abs(suma_lineas - base_con_iva) > 0.05:
                    st.warning(f"Las líneas suman Q {suma_lineas:,.2f} y la factura (sin IDP) es Q {base_con_iva:,.2f}. Diferencia: Q {abs(suma_lineas - base_con_iva):,.2f}.")
                    prorratear_ok = st.checkbox("Prorratear la diferencia entre las líneas (confirmo)", value=False, key=f"fc_prorr_{base}")

            aplica_isr = False
            aplica_iva = False
            tipo_isr = "No Aplica"
            tipo_iva = "No Aplica / No es Agente Retenedor"

            if es_no_fiscal:
                st.info(f"Comprobante No Fiscal: Monto Q{total_ingresado:,.2f} (Sin Crédito Fiscal IVA ni retenciones).")
            else:
                sobre_limite = subtotal >= LIMITE_RETENCION_ISR
                r1, r2 = st.columns(2)
                aplica_isr = r1.checkbox("Practicar Retención ISR", value=sobre_limite, key=f"fc_chkisr_{base}_{int(sobre_limite)}")
                tipo_isr = r1.selectbox("Tasa ISR", ["5% General", "7% Excedente (Sobre Q30k)", "10% Servicios Técnicos/Honorarios", "No Aplica"],
                                        index=0 if sobre_limite else 3, key=f"fc_tisr_{base}_{int(sobre_limite)}")

                aplica_iva = r2.checkbox("Practicar Retención IVA", value=False, key=f"fc_chkiva_{base}")
                tipo_iva = r2.selectbox("Tasa IVA", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "25% Exportadores", "100% Retención Total IVA"], key=f"fc_tiva_{base}")

            ret = calcular_retenciones(total_ingresado, subtotal, iva, aplica_isr, tipo_isr, aplica_iva, tipo_iva)
            t_isr, m_isr, t_iva, m_iva, liq = ret["t_isr"], ret["m_isr"], ret["t_iva"], ret["m_iva"], ret["liq"]

            if not es_no_fiscal:
                txt_idp = f" | IDP: Q{monto_idp:,.2f}" if monto_idp > 0 else ""
                st.info(f"Subtotal: Q{subtotal:,.2f} | IVA: Q{iva:,.2f}{txt_idp} | **Total:** Q{total_ingresado:,.2f} | Ret. ISR: Q{m_isr:,.2f} | Ret. IVA: Q{m_iva:,.2f} | **Líquido a Pagar:** Q{liq:,.2f}")

            # Vista previa del asiento
            asiento_prev = [{"Cuenta": cuenta_debe, "DEBE": f"Q {subtotal:,.2f}", "HABER": "Q 0.00"}]
            if iva > 0:
                asiento_prev.append({"Cuenta": "1.1.03.01 - IVA por Cobrar (Crédito Fiscal)", "DEBE": f"Q {iva:,.2f}", "HABER": "Q 0.00"})
            if monto_idp > 0:
                asiento_prev.append({"Cuenta": f"{cuenta_idp} (IDP)", "DEBE": f"Q {monto_idp:,.2f}", "HABER": "Q 0.00"})
            if m_isr > 0:
                asiento_prev.append({"Cuenta": "2.1.03.01 - Retenciones ISR por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_isr:,.2f}"})
            if m_iva > 0:
                asiento_prev.append({"Cuenta": "2.1.03.02 - Retenciones IVA por Pagar", "DEBE": "Q 0.00", "HABER": f"Q {m_iva:,.2f}"})
            asiento_prev.append({"Cuenta": cuenta_haber, "DEBE": "Q 0.00", "HABER": f"Q {liq:,.2f}"})
            st.table(pd.DataFrame(asiento_prev))

            # Control de duplicados
            permitir_dup = True
            if num_fac.strip():
                try:
                    conn = get_db_connection()
                    cdup = conn.cursor()
                    cdup.execute("SELECT COUNT(*) FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND numero_factura = %s AND COALESCE(serie, '') = %s",
                                 (str(selected_emp_id), num_fac.strip(), serie.strip()))
                    n_dup = cdup.fetchone()[0]
                    conn.close()
                except Exception:
                    n_dup = 0
                if n_dup > 0:
                    st.warning(f"⚠️ Ya existe una compra registrada con el documento {num_fac.strip()} (serie '{serie.strip()}') en esta empresa.")
                    permitir_dup = st.checkbox("Registrar de todos modos (duplicado)", value=False, key=f"fc_dup_{base}")

            if es_inventario and not lineas:
                st.warning("⚠️ No agregaste líneas de producto: la compra se asentará en Inventario pero NO se actualizarán las existencias del Kárdex.")

            if st.button("Guardar Factura y Asentar en Diario", type="primary", key="btn_save_c"):
                if not (num_fac.strip() and prov_nombre.strip() and prov_nit.strip() and total_ingresado > 0):
                    st.error("Completa todos los campos obligatorios y asegúrate de que el total sea mayor a 0.")
                elif not permitir_dup:
                    st.error("Documento duplicado: marca la casilla para registrarlo de todos modos.")
                elif es_inventario and lineas and not prorratear_ok:
                    st.error("Revisa las líneas de inventario: falta el total de alguna línea o confirmar el prorrateo de la diferencia.")
                else:
                    conn = None
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        now_str = datetime.now().isoformat()
                        fac_id = f"fac-c-{int(datetime.now().timestamp())}-{uuid.uuid4().hex[:6]}"
                        es_credito = (tipo_pago == "Crédito")
                        fp_desc = "Crédito" if es_credito else f"Contado ({metodo_det})"
                        con_columna_idp = bool(st.session_state.get("schema_idp"))
                        desc_fac = tipo_adquisicion if (con_columna_idp or monto_idp <= 0) else f"{tipo_adquisicion} | IDP Q{monto_idp:,.2f}"

                        cols_f = ["id", "empresa_id", "tipo", "numero_factura", "serie", "fecha", "tercero_nombre", "tercero_nit",
                                  "descripcion", "tipo_gasto_ingreso", "cuenta_nomenclatura", "cuenta_pago", "forma_pago",
                                  "subtotal", "iva", "total", "aplica_ret_isr", "tasa_ret_isr", "monto_ret_isr",
                                  "aplica_ret_iva", "tasa_ret_iva", "monto_ret_iva", "total_a_pagar_cobrar",
                                  "saldo_pendiente", "estado_pago", "created_by", "created_at"]
                        vals_f = [fac_id, str(selected_emp_id), "COMPRA", num_fac.strip(), serie.strip(), str(fecha_fac),
                                  prov_nombre.strip(), prov_nit.strip().upper(), desc_fac, "Gasto",
                                  cuenta_debe, cuenta_haber, fp_desc,
                                  float(subtotal), float(iva), float(total_ingresado),
                                  1 if m_isr > 0 else 0, float(t_isr), float(m_isr),
                                  1 if m_iva > 0 else 0, float(t_iva), float(m_iva), float(liq),
                                  float(liq if es_credito else 0.0), "PENDIENTE" if es_credito else "PAGADO",
                                  str(curr_u['username']), now_str]
                        if con_columna_idp:
                            cols_f.append("idp")
                            vals_f.append(float(monto_idp))
                        c.execute(f"INSERT INTO facturas ({', '.join(cols_f)}) VALUES ({', '.join(['%s'] * len(cols_f))})", tuple(vals_f))

                        # Directorio de terceros (con savepoint para no abortar la transacción si falla)
                        c.execute("SAVEPOINT sp_terc")
                        try:
                            c.execute("""
                                INSERT INTO terceros_corporativos (id, nit, nombre, tipo, direccion, telefono, email, created_at)
                                VALUES (%s, %s, %s, 'PROVEEDOR', 'Guatemala', '', '', %s)
                                ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre,
                                    tipo = CASE WHEN terceros_corporativos.tipo = 'CLIENTE' THEN 'AMBOS' ELSE terceros_corporativos.tipo END
                            """, (f"terc-{int(datetime.now().timestamp())}-{uuid.uuid4().hex[:4]}", prov_nit.strip().upper(), prov_nombre.strip(), now_str))
                            c.execute("RELEASE SAVEPOINT sp_terc")
                        except Exception:
                            c.execute("ROLLBACK TO SAVEPOINT sp_terc")

                        # Entradas de inventario: una por cada línea (costo promedio ponderado)
                        n_lineas_guardadas = 0
                        if es_inventario and lineas:
                            costos_final = repartir_costos([l["total"] for l in lineas], base_inventario)
                            for lin, costo_lin in zip(lineas, costos_final):
                                c.execute("SELECT existencia, costo_promedio FROM productos WHERE id = %s", (lin["pid"],))
                                p_row = c.fetchone()
                                if not p_row:
                                    raise ValueError(f"El producto «{lin['label']}» ya no existe en el catálogo.")
                                stock_actual = float(p_row[0] or 0)
                                costo_actual = float(p_row[1] or 0)
                                nuevo_stock = stock_actual + lin["cant"]
                                nuevo_costo_prom = round(((stock_actual * costo_actual) + costo_lin) / nuevo_stock, 2) if nuevo_stock > 0 else round(costo_lin / lin["cant"], 2)
                                c.execute("UPDATE productos SET existencia = %s, costo_promedio = %s WHERE id = %s", (nuevo_stock, nuevo_costo_prom, lin["pid"]))
                                if st.session_state.get("schema_items"):
                                    c.execute("""INSERT INTO factura_items (id, factura_id, empresa_id, producto_id, descripcion, cantidad, costo_total, created_at)
                                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                              (f"fi-{uuid.uuid4().hex[:12]}", fac_id, str(selected_emp_id), str(lin["pid"]), lin["label"], float(lin["cant"]), float(costo_lin), now_str))
                                n_lineas_guardadas += 1

                        # Asiento contable en la misma transacción
                        cod_d, nom_d = cuenta_debe.split(" - ", 1) if " - " in cuenta_debe else (cuenta_debe, cuenta_debe)
                        cod_h, nom_h = cuenta_haber.split(" - ", 1) if " - " in cuenta_haber else (cuenta_haber, cuenta_haber)
                        cod_i, nom_i = cuenta_idp.split(" - ", 1) if " - " in cuenta_idp else (cuenta_idp, cuenta_idp)
                        registrar_partida_compra(c, selected_emp_id, fecha_fac, cod_d, nom_d, cod_h, nom_h,
                                                 subtotal, iva, m_isr, m_iva, liq, num_fac.strip(), prov_nombre.strip(), now_str,
                                                 idp=monto_idp, cod_idp=cod_i, nom_idp=nom_i)

                        conn.commit()
                        conn.close()
                        st.session_state.c_nonce += 1
                        st.session_state.c_lineas = []
                        st.session_state.c_line_nonce += 1
                        st.session_state.pop("c_prod_sel", None)
                        st.session_state.c_flash = ("✅ Factura registrada y asentada en el Libro Diario"
                                                    + (f" · Kárdex actualizado ({n_lineas_guardadas} línea(s))" if n_lineas_guardadas else "")
                                                    + (f" · IDP Q{monto_idp:,.2f} registrado." if monto_idp > 0 else "."))
                        st.rerun()
                    except Exception as err:
                        try:
                            if conn:
                                conn.rollback()
                                conn.close()
                        except Exception:
                            pass
                        st.error(f"❌ Error al guardar la factura (no se guardó nada): {err}")

    with t_fc_hist:
        col_idp_sql = ', COALESCE(idp, 0) AS "IDP (Q)"' if st.session_state.get("schema_idp") else ""
        conn = get_db_connection()
        df_c_hist = pd.read_sql_query(f"""
            SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
                   tercero_nombre AS "Proveedor", subtotal AS "Base (Q)", iva AS "IVA (Q)"{col_idp_sql},
                   total AS "Total (Q)", monto_ret_isr AS "Ret. ISR (Q)", monto_ret_iva AS "Ret. IVA (Q)",
                   total_a_pagar_cobrar AS "Líquido (Q)",
                   forma_pago AS "Forma Pago", estado_pago AS "Estado"
            FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA'
            ORDER BY fecha DESC, created_at DESC
        """, conn, params=(str(selected_emp_id),))
        conn.close()

        if not df_c_hist.empty:
            st.dataframe(df_c_hist.drop(columns=["id"]), use_container_width=True)
            if is_admin:
                with st.expander("🗑️ Eliminar Factura de Compra (Solo Administrador)"):
                    st.caption("Elimina la factura y su partida del Libro Diario. Si la compra se registró con líneas de inventario, también revierte las existencias del Kárdex.")
                    del_c_id = st.selectbox("Seleccionar factura a eliminar:", df_c_hist['id'].tolist(),
                                            format_func=lambda x: f"Fac {df_c_hist[df_c_hist['id']==x]['No. Factura'].values[0]} - {df_c_hist[df_c_hist['id']==x]['Proveedor'].values[0]}")
                    if st.button("Confirmar Eliminación Definitiva Compra", type="secondary"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("SELECT numero_factura, tercero_nombre FROM facturas WHERE id = %s AND empresa_id = %s", (del_c_id, str(selected_emp_id)))
                        row_f = c.fetchone()
                        if row_f:
                            glosa_c = f"Factura Compra {row_f[0]} de {row_f[1]}"
                            glosa_ab = f"Abono Factura {row_f[0]} a {row_f[1]}"
                            c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND referencia_id = %s AND concepto IN (%s, %s, %s)",
                                      (str(selected_emp_id), str(row_f[0]), glosa_c, f"{glosa_c} (IDP)", glosa_ab))
                        # Revertir existencias de las líneas de inventario de esta compra
                        if st.session_state.get("schema_items"):
                            c.execute("SELECT producto_id, cantidad, costo_total FROM factura_items WHERE factura_id = %s AND empresa_id = %s", (del_c_id, str(selected_emp_id)))
                            for pid_i, cant_i, costo_i in c.fetchall():
                                c.execute("SELECT existencia, costo_promedio FROM productos WHERE id = %s", (pid_i,))
                                pr = c.fetchone()
                                if pr:
                                    ex_a, co_a = float(pr[0] or 0), float(pr[1] or 0)
                                    ex_n = max(ex_a - float(cant_i or 0), 0.0)
                                    co_n = round(max((ex_a * co_a - float(costo_i or 0)) / ex_n, 0.0), 2) if ex_n > 0 else co_a
                                    c.execute("UPDATE productos SET existencia = %s, costo_promedio = %s WHERE id = %s", (ex_n, co_n, pid_i))
                            c.execute("DELETE FROM factura_items WHERE factura_id = %s AND empresa_id = %s", (del_c_id, str(selected_emp_id)))
                        c.execute("DELETE FROM facturas WHERE id = %s AND empresa_id = %s", (del_c_id, str(selected_emp_id)))
                        conn.commit()
                        conn.close()
                        st.success("Compra y partida eliminadas.")
                        st.rerun()
        else:
            st.info("No hay facturas de compras registradas.")

# ==========================================
# 3. FACTURA DE VENTAS
# ==========================================
elif selected_menu in ["📈 Factura de Ventas", "📈 Facturación de Ventas"]:
    if not selected_emp_id: st.stop()
    st.title(f"📈 Facturación de Ventas Emitidas — {selected_empresa_info[1]}")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND tipo = 'Ingreso' ORDER BY codigo", (str(selected_emp_id),))
    cuentas_ing = c.fetchall()
    c.execute("SELECT codigo, nombre FROM nomenclatura WHERE empresa_id = %s AND (tipo = 'Activo' OR tipo = 'Pasivo') ORDER BY codigo", (str(selected_emp_id),))
    cuentas_cobro = c.fetchall()
    c.execute("SELECT nit, nombre FROM terceros_corporativos WHERE tipo IN ('CLIENTE', 'AMBOS') ORDER BY nombre")
    cli_corp = c.fetchall()
    conn.close()

    opciones_haber = [f"{r[0]} - {r[1]}" for r in cuentas_ing]
    opciones_debe = [f"{r[0]} - {r[1]}" for r in cuentas_cobro]

    t_emitir, t_historial = st.tabs(["➕ Emitir Factura de Venta", "📋 Modificar / Corregir Factura"])

    with t_emitir:
        modo_ingreso_v = st.radio("Método de Ingreso de Venta:", ["✍️ Ingreso Manual", "📄 Carga Inteligente PDF (FEL SAT)"], horizontal=True, key="modo_v_rad")

        pre_v = {}
        dyn_v_key = "v_init"
        if modo_ingreso_v == "📄 Carga Inteligente PDF (FEL SAT)":
            pre_v, sig_v = cargar_dte_ui("Sube la Factura FEL de Venta (PDF o XML):", "pdf_venta_sat")
            if sig_v != "manual":
                dyn_v_key = f"v_{sig_v}"

        dict_clis = {f"{r[1]} (NIT: {r[0]})": (r[1], r[0]) for r in cli_corp}
        cli_sel = st.selectbox("Seleccionar Cliente Corporativo (Opcional):", ["-- Ingresar Manualmente / Usar PDF --"] + list(dict_clis.keys()), key=f"sel_cli_{dyn_v_key}")

        val_nom_def = dict_clis[cli_sel][0] if cli_sel != "-- Ingresar Manualmente / Usar PDF --" else (pre_v.get("receptor_nombre") or "CONSUMIDOR FINAL")
        val_nit_def = dict_clis[cli_sel][1] if cli_sel != "-- Ingresar Manualmente / Usar PDF --" else (pre_v.get("receptor_nit") or "CF")

        with st.form("form_venta_oficial"):
            c1, c2, c3 = st.columns(3)
            num_fac = c1.text_input("Número Factura / DTE Emitido *", value=pre_v.get("numero_dte", ""), key=f"fv_num_{dyn_v_key}")
            serie = c2.text_input("Serie", value=pre_v.get("serie", ""), key=f"fv_ser_{dyn_v_key}")
            fecha_fac = c3.date_input("Fecha de Emisión", value=pre_v.get("fecha") or hoy_gt(), key=f"fv_fec_{dyn_v_key}")

            c4, c5 = st.columns(2)
            cli_nombre = c4.text_input("Nombre / Razón Social del Cliente *", value=val_nom_def, key=f"fv_cli_nom_{dyn_v_key}")
            cli_nit = c5.text_input("NIT del Cliente *", value=val_nit_def, key=f"fv_cli_nit_{dyn_v_key}")

            st.markdown("---")
            c6, c7 = st.columns(2)

            # Selector de Tipo de Cobro / Forma de Pago
            forma_cobro_v = c6.selectbox("Tipo de Pago / Cobro:", [
                "Contado - Efectivo (Caja General)",
                "Contado - Transferencia / Depósito (Bancos)",
                "Contado - Tarjeta / POS (Tarjetas por Cobrar)",
                "Crédito (Cuentas por Cobrar - Clientes)"
            ], key=f"fv_fcobro_{dyn_v_key}")

            # Asignación automática o selección de cuenta Debe
            if "Bancos" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.02" in x), 0)
            elif "Tarjeta" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.03" in x), 0)
            elif "Crédito" in forma_cobro_v:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.02.01" in x), 0)
            else:
                idx_d = next((i for i, x in enumerate(opciones_debe) if "1.1.01.01" in x), 0)

            cuenta_debe_cobro = c6.selectbox("Cuenta de Cobro / DEBE (Destino) *", opciones_debe if opciones_debe else ["1.1.01.01 - Caja General (Efectivo)"], index=idx_d, key=f"fv_cdebe_{dyn_v_key}")

            idx_haber_def = 0
            for idx, item in enumerate(opciones_haber):
                if "4.1.01.03" in item or "Transporte" in item:
                    idx_haber_def = idx
                    break
                elif "4.1.01.01" in item:
                    idx_haber_def = idx
            cuenta_haber_ingreso = c7.selectbox("Cuenta de Ingreso / HABER *", opciones_haber if opciones_haber else ["4.1.01.03 - Servicios de Transporte y Fletes"], index=idx_haber_def, key=f"fv_chaber_{dyn_v_key}")

            # Monto total ingresado (se desglosa automáticamente)
            val_total_v = float(pre_v.get("total", 0.0) or 300.0)
            monto_total_fac = st.number_input("Monto Total Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=val_total_v, key=f"fv_tot_{dyn_v_key}")

            es_pequeno = st.checkbox("¿Régimen Pequeño Contribuyente? (No genera débito fiscal IVA)", value=pre_v.get("es_pequeno", True), key=f"fv_espeq_{dyn_v_key}")

            r1, r2 = st.columns(2)
            aplica_isr = r1.checkbox("Cliente practicó Retención de ISR", value=False, key=f"fv_chkisr_{dyn_v_key}")
            tipo_isr = r1.selectbox("Tasa ISR Retenida", ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"], key=f"fv_tisr_{dyn_v_key}")

            aplica_iva = r2.checkbox("Cliente practicó Retención de IVA", value=False, key=f"fv_chkiva_{dyn_v_key}")
            tipo_iva = r2.selectbox("Tasa IVA Retenida", ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"], key=f"fv_tiva_{dyn_v_key}")

            tot_v = float(monto_total_fac)
            if es_pequeno:
                sub_v = tot_v
                iva_v = 0.0
            else:
                sub_v = round(tot_v / 1.12, 2)
                iva_v = round(tot_v - sub_v, 2)

            m_isr_v = 0.0
            t_isr_v = 0.0
            if aplica_isr and sub_v >= LIMITE_RETENCION_ISR and tipo_isr != "No Aplica":
                t_isr_v, m_isr_v = calc_isr(sub_v, tipo_isr)

            m_iva_v = 0.0
            t_iva_v = 0.0
            if aplica_iva and iva_v > 0 and "No Aplica" not in tipo_iva:
                t_iva_v, m_iva_v = calc_iva_retenido(iva_v, tipo_iva)

            liq_v = round(tot_v - m_isr_v - m_iva_v, 2)

            st.markdown(f"**Base Imponible (Sin IVA):** Q {sub_v:,.2f} | **IVA Débito Fiscal:** Q {iva_v:,.2f} | **Total:** Q {tot_v:,.2f} | **Líquido a Recibir:** Q {liq_v:,.2f}")

            asiento_preview = [
                {"Cuenta": cuenta_debe_cobro, "DEBE": f"Q {liq_v:,.2f}", "HABER": "Q 0.00"},
                {"Cuenta": cuenta_haber_ingreso, "DEBE": "Q 0.00", "HABER": f"Q {sub_v:,.2f}"}
            ]
            if m_isr_v > 0:
                asiento_preview.append({"Cuenta": "1.1.03.02 - ISR Retenido por Acreditar", "DEBE": f"Q {m_isr_v:,.2f}", "HABER": "Q 0.00"})
            if m_iva_v > 0:
                asiento_preview.append({"Cuenta": "1.1.03.03 - Constancias Retención IVA", "DEBE": f"Q {m_iva_v:,.2f}", "HABER": "Q 0.00"})
            if iva_v > 0:
                asiento_preview.append({"Cuenta": "2.1.02.01 - IVA por Pagar (Débito Fiscal)", "DEBE": "Q 0.00", "HABER": f"Q {iva_v:,.2f}"})
            st.table(pd.DataFrame(asiento_preview))

            btn_guardar_v = st.form_submit_button("Guardar Factura de Venta y Asentar", type="primary")

            if btn_guardar_v:
                if not num_fac.strip() or not cli_nombre.strip() or not cli_nit.strip() or tot_v <= 0:
                    st.error("⚠️ Completa los campos obligatorios: Número DTE, Cliente, NIT y Monto mayor a 0.")
                else:
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        now_str = datetime.now().isoformat()
                        fac_id = f"fac-v-{int(datetime.now().timestamp())}"
                        es_cred_v = forma_cobro_v.startswith("Crédito")

                        c.execute("""
                            INSERT INTO facturas (
                                id, empresa_id, tipo, numero_factura, serie, fecha,
                                tercero_nombre, tercero_nit, descripcion, tipo_gasto_ingreso,
                                subtotal, iva, total, aplica_ret_isr, tasa_ret_isr, monto_ret_isr,
                                aplica_ret_iva, tasa_ret_iva, monto_ret_iva, total_a_pagar_cobrar,
                                saldo_pendiente, estado_pago, created_by, created_at,
                                cuenta_nomenclatura, cuenta_pago, forma_pago
                            ) VALUES (%s, %s, 'VENTA', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            fac_id, str(selected_emp_id), str(num_fac).strip(), str(serie).strip(), str(fecha_fac),
                            str(cli_nombre).strip(), str(cli_nit).strip().upper(), "Venta / Transporte", "Ingreso",
                            float(sub_v), float(iva_v), float(tot_v),
                            1 if m_isr_v > 0 else 0, float(t_isr_v), float(m_isr_v),
                            1 if m_iva_v > 0 else 0, float(t_iva_v), float(m_iva_v),
                            float(liq_v), float(liq_v if es_cred_v else 0.0), "PENDIENTE" if es_cred_v else "PAGADO",
                            str(curr_u['username']), str(now_str),
                            str(cuenta_haber_ingreso), str(cuenta_debe_cobro), str(forma_cobro_v)
                        ))

                        c.execute("SAVEPOINT sp_terc_v")
                        try:
                            c.execute("""
                                INSERT INTO terceros_corporativos (id, nit, nombre, tipo, direccion, telefono, email, created_at)
                                VALUES (%s, %s, %s, 'CLIENTE', 'Ciudad', '', '', %s)
                                ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre
                            """, (f"terc-{int(datetime.now().timestamp())}", str(cli_nit).strip().upper(), str(cli_nombre).strip(), str(now_str)))
                            c.execute("RELEASE SAVEPOINT sp_terc_v")
                        except Exception:
                            c.execute("ROLLBACK TO SAVEPOINT sp_terc_v")

                        c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                        p_no = int(c.fetchone()[0])
                        glosa = f"Factura Venta {str(num_fac).strip()} a {str(cli_nombre).strip()}"

                        cod_d, nom_d = cuenta_debe_cobro.split(" - ", 1) if " - " in cuenta_debe_cobro else (cuenta_debe_cobro, cuenta_debe_cobro)
                        cod_h, nom_h = cuenta_haber_ingreso.split(" - ", 1) if " - " in cuenta_haber_ingreso else (cuenta_haber_ingreso, cuenta_haber_ingreso)

                        q_diario = """
                            INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                        c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_d), str(nom_d), float(liq_v), 0.0, glosa, str(num_fac).strip(), str(now_str)))
                        if m_isr_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.02', 'ISR Retenido por Acreditar', float(m_isr_v), 0.0, glosa, str(num_fac).strip(), str(now_str)))
                        if m_iva_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '1.1.03.03', 'Constancias Retención IVA', float(m_iva_v), 0.0, glosa, str(num_fac).strip(), str(now_str)))

                        c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), str(cod_h), str(nom_h), 0.0, float(sub_v), glosa, str(num_fac).strip(), str(now_str)))
                        if iva_v > 0:
                            c.execute(q_diario, (str(selected_emp_id), p_no, str(fecha_fac), '2.1.02.01', 'IVA por Pagar (Débito Fiscal)', 0.0, float(iva_v), glosa, str(num_fac).strip(), str(now_str)))

                        conn.commit()
                        conn.close()
                        st.success("✅ Factura guardada y asentada exitosamente en todos los módulos.")
                        st.rerun()
                    except Exception as err:
                        try:
                            conn.rollback()
                            conn.close()
                        except Exception:
                            pass
                        st.error(f"❌ Error al guardar en base de datos: {err}")

    with t_historial:
        conn = get_db_connection()
        df_ventas = pd.read_sql_query("""
            SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
                   tercero_nombre AS "Cliente", tercero_nit AS "NIT", subtotal AS "Base (Q)", iva AS "IVA (Q)",
                   total AS "Total (Q)", total_a_pagar_cobrar AS "Líquido (Q)",
                   cuenta_nomenclatura AS "Cuenta Ingreso", cuenta_pago AS "Cuenta Cobro",
                   forma_pago AS "Tipo Pago", estado_pago AS "Estado",
                   aplica_ret_isr AS _aisr, tasa_ret_isr AS _tisr, monto_ret_isr AS _misr,
                   aplica_ret_iva AS _aiva, tasa_ret_iva AS _tiva, monto_ret_iva AS _miva,
                   saldo_pendiente AS _saldo
            FROM facturas 
            WHERE empresa_id = %s AND tipo = 'VENTA' 
            ORDER BY fecha DESC, created_at DESC
        """, conn, params=(str(selected_emp_id),))
        conn.close()

        if not df_ventas.empty:
            st.dataframe(df_ventas.drop(columns=[x for x in df_ventas.columns if str(x).startswith("_")]), use_container_width=True)

            st.markdown("---")
            st.subheader("✏️ Modificar / Corregir Factura de Venta")

            fac_edit_id = st.selectbox(
                "Selecciona la factura de venta a editar:",
                df_ventas['id'].tolist(),
                format_func=lambda x: f"DTE: {df_ventas[df_ventas['id']==x]['No. DTE'].values[0]} - {df_ventas[df_ventas['id']==x]['Cliente'].values[0]} (Fecha: {df_ventas[df_ventas['id']==x]['Fecha'].values[0]} - Q{float(df_ventas[df_ventas['id']==x]['Total (Q)'].values[0]):,.2f})",
                key="sel_fac_v_edit"
            )

            row_e = df_ventas[df_ventas['id'] == fac_edit_id].iloc[0]
            kx = str(fac_edit_id)   # las claves llevan el id para que cada factura muestre SUS propios datos

            tipos_isr_ed = ["No Aplica", "5% General", "7% Excedente (Sobre Q30k)"]
            tipos_iva_ed = ["No Aplica / No es Agente Retenedor", "15% Retención IVA (Decreto 20-2006)", "100% Retención Total IVA"]
            idx_isr_ed = 0
            if num_seguro(row_e['_misr']) > 0:
                idx_isr_ed = 2 if abs(num_seguro(row_e['_tisr']) - 0.07) < 0.0001 else 1
            idx_iva_ed = 0
            if num_seguro(row_e['_miva']) > 0:
                idx_iva_ed = 2 if num_seguro(row_e['_tiva']) >= 0.99 else 1

            with st.form(f"form_editar_factura_venta_{kx}"):
                ce1, ce2, ce3 = st.columns(3)
                try:
                    f_actual = datetime.strptime(str(row_e['Fecha']), "%Y-%m-%d").date()
                except Exception:
                    f_actual = hoy_gt()
                edit_fecha = ce1.date_input("Fecha Correcta de Emisión *", value=f_actual, key=f"edit_v_fec_{kx}")
                edit_num = ce2.text_input("Número Factura / No. DTE *", value=str(row_e['No. DTE']), key=f"edit_v_num_{kx}")
                edit_serie = ce3.text_input("Serie", value=str(row_e['Serie'] or ""), key=f"edit_v_ser_{kx}")

                ce4, ce5 = st.columns(2)
                edit_cli = ce4.text_input("Cliente *", value=str(row_e['Cliente']), key=f"edit_v_cli_{kx}")
                edit_nit = ce5.text_input("NIT Cliente *", value=str(row_e['NIT']), key=f"edit_v_nit_{kx}")

                st.markdown("---")
                ce6, ce7 = st.columns(2)
                # Opciones de cuentas
                idx_e_debe = 0
                for i_d, it_d in enumerate(opciones_debe):
                    if str(row_e['Cuenta Cobro']) in it_d:
                        idx_e_debe = i_d; break
                edit_cdebe = ce6.selectbox("Cuenta de Cobro / DEBE *", opciones_debe if opciones_debe else ["1.1.01.01 - Caja General (Efectivo)"], index=idx_e_debe, key=f"edit_v_cdebe_{kx}")

                idx_e_haber = 0
                for i_h, it_h in enumerate(opciones_haber):
                    if str(row_e['Cuenta Ingreso']) in it_h or "Transporte" in it_h:
                        idx_e_haber = i_h; break
                edit_chaber = ce7.selectbox("Cuenta de Ingreso / HABER *", opciones_haber if opciones_haber else ["4.1.01.03 - Servicios de Transporte y Fletes"], index=idx_e_haber, key=f"edit_v_chaber_{kx}")

                edit_tot = st.number_input("Monto Total Factura (Q) *", min_value=0.0, step=50.0, format="%.2f", value=float(row_e['Total (Q)']), key=f"edit_v_tot_{kx}")
                es_peq_actual = float(row_e['IVA (Q)']) == 0.0
                edit_es_peq = st.checkbox("¿Régimen Pequeño Contribuyente? (Sin IVA)", value=es_peq_actual, key=f"edit_v_peq_{kx}")

                er1, er2 = st.columns(2)
                edit_tipo_isr = er1.selectbox("Retención ISR practicada por el cliente", tipos_isr_ed, index=idx_isr_ed, key=f"edit_v_tisr_{kx}")
                edit_tipo_iva = er2.selectbox("Retención IVA practicada por el cliente", tipos_iva_ed, index=idx_iva_ed, key=f"edit_v_tiva_{kx}")

                tot_mod = float(edit_tot)
                if edit_es_peq:
                    sub_mod = tot_mod
                    iva_mod = 0.0
                else:
                    sub_mod = round(tot_mod / 1.12, 2)
                    iva_mod = round(tot_mod - sub_mod, 2)

                t_isr_mod, m_isr_mod = (0.0, 0.0)
                if edit_tipo_isr != "No Aplica" and sub_mod >= LIMITE_RETENCION_ISR:
                    t_isr_mod, m_isr_mod = calc_isr(sub_mod, edit_tipo_isr)
                t_iva_mod, m_iva_mod = calc_iva_retenido(iva_mod, edit_tipo_iva)
                liq_mod = round(tot_mod - m_isr_mod - m_iva_mod, 2)

                st.markdown(f"**Nuevo desglose:** Base: Q {sub_mod:,.2f} | IVA Débito: Q {iva_mod:,.2f} | Total: Q {tot_mod:,.2f} | Ret. ISR: Q {m_isr_mod:,.2f} | Ret. IVA: Q {m_iva_mod:,.2f} | **Líquido: Q {liq_mod:,.2f}**")

                if st.form_submit_button("💾 Guardar Corrección de Factura de Venta", type="primary"):
                    if not edit_num.strip() or not edit_cli.strip() or not edit_nit.strip() or tot_mod <= 0:
                        st.error("Completa los campos obligatorios.")
                    else:
                        conn = None
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            now_mod = datetime.now().isoformat()

                            # 1. Saldo y estado de cobro: se conserva lo ya cobrado
                            estado_old = str(row_e['Estado'] or "")
                            liq_old = num_seguro(row_e['Líquido (Q)'])
                            saldo_old = num_seguro(row_e['_saldo'])
                            if estado_old == "PENDIENTE":
                                cobrado = max(round(liq_old - saldo_old, 2), 0.0)
                                nuevo_saldo = round(max(liq_mod - cobrado, 0.0), 2)
                                nuevo_estado = "PENDIENTE" if nuevo_saldo > 0 else "PAGADO"
                            else:
                                nuevo_saldo, nuevo_estado = saldo_old, estado_old

                            # 2. Actualizar la factura (incluye retenciones y líquido)
                            c.execute("""
                                UPDATE facturas
                                SET fecha = %s, numero_factura = %s, serie = %s,
                                    tercero_nombre = %s, tercero_nit = %s,
                                    subtotal = %s, iva = %s, total = %s,
                                    aplica_ret_isr = %s, tasa_ret_isr = %s, monto_ret_isr = %s,
                                    aplica_ret_iva = %s, tasa_ret_iva = %s, monto_ret_iva = %s,
                                    total_a_pagar_cobrar = %s, saldo_pendiente = %s, estado_pago = %s,
                                    cuenta_nomenclatura = %s, cuenta_pago = %s
                                WHERE id = %s AND empresa_id = %s
                            """, (
                                str(edit_fecha), edit_num.strip(), edit_serie.strip(),
                                edit_cli.strip(), edit_nit.strip().upper(),
                                float(sub_mod), float(iva_mod), float(tot_mod),
                                1 if m_isr_mod > 0 else 0, float(t_isr_mod), float(m_isr_mod),
                                1 if m_iva_mod > 0 else 0, float(t_iva_mod), float(m_iva_mod),
                                float(liq_mod), float(nuevo_saldo), nuevo_estado,
                                edit_chaber, edit_cdebe, fac_edit_id, str(selected_emp_id)
                            ))

                            # 3. Reemplazar SOLO el asiento de esta factura (se conservan los cobros ya registrados)
                            antiguo_num = str(row_e['No. DTE'])
                            glosa_vieja = f"Factura Venta {antiguo_num} a {row_e['Cliente']}"
                            c.execute("SELECT MIN(partida_no) FROM libro_diario WHERE empresa_id = %s AND referencia_id = %s AND concepto = %s",
                                      (str(selected_emp_id), antiguo_num, glosa_vieja))
                            p_old = c.fetchone()[0]
                            c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND referencia_id = %s AND concepto = %s",
                                      (str(selected_emp_id), antiguo_num, glosa_vieja))
                            borradas = c.rowcount
                            if p_old is None:
                                c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                                p_no_corregida = int(c.fetchone()[0])
                            else:
                                p_no_corregida = int(p_old)
                            glosa_mod = f"Factura Venta {edit_num.strip()} a {edit_cli.strip()}"

                            cod_ed, nom_ed = edit_cdebe.split(" - ", 1) if " - " in edit_cdebe else (edit_cdebe, edit_cdebe)
                            cod_eh, nom_eh = edit_chaber.split(" - ", 1) if " - " in edit_chaber else (edit_chaber, edit_chaber)

                            q_ins_corregido = """
                                INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """
                            ref_mod = edit_num.strip()
                            f_mod = str(edit_fecha)
                            # Debe: cobro (líquido) + retenciones soportadas
                            c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, f_mod, str(cod_ed), str(nom_ed), float(liq_mod), 0.0, glosa_mod, ref_mod, now_mod))
                            if m_isr_mod > 0:
                                c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, f_mod, '1.1.03.02', 'ISR Retenido por Acreditar', float(m_isr_mod), 0.0, glosa_mod, ref_mod, now_mod))
                            if m_iva_mod > 0:
                                c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, f_mod, '1.1.03.03', 'Constancias Retención IVA', float(m_iva_mod), 0.0, glosa_mod, ref_mod, now_mod))
                            # Haber: ingreso + IVA débito
                            c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, f_mod, str(cod_eh), str(nom_eh), 0.0, float(sub_mod), glosa_mod, ref_mod, now_mod))
                            if iva_mod > 0:
                                c.execute(q_ins_corregido, (str(selected_emp_id), p_no_corregida, f_mod, '2.1.02.01', 'IVA por Pagar (Débito Fiscal)', 0.0, float(iva_mod), glosa_mod, ref_mod, now_mod))

                            conn.commit()
                            conn.close()
                            if borradas == 0:
                                st.session_state["v_flash_warn"] = "No se encontró el asiento anterior de esta factura (posiblemente creada con una versión previa); se generó una partida nueva. Revisa el Libro Diario por si quedó duplicada."
                            st.session_state["v_flash_ok"] = "✅ Factura de venta y asiento contable actualizados con éxito."
                            st.rerun()
                        except Exception as err:
                            try:
                                if conn:
                                    conn.rollback(); conn.close()
                            except Exception:
                                pass
                            st.error(f"Error al guardar corrección (no se modificó nada): {err}")

            if st.session_state.get("v_flash_ok"):
                st.success(st.session_state.pop("v_flash_ok"))
            if st.session_state.get("v_flash_warn"):
                st.warning(st.session_state.pop("v_flash_warn"))

            if is_admin:
                st.markdown("---")
                with st.expander("🗑️ Eliminar Factura (Solo Administrador)"):
                    st.caption("Elimina la factura, su partida y los cobros registrados en el Libro Diario.")
                    fac_eliminar = st.selectbox("Selecciona factura a eliminar:", df_ventas['id'].tolist(),
                                                format_func=lambda x: f"DTE: {df_ventas[df_ventas['id']==x]['No. DTE'].values[0]} - {df_ventas[df_ventas['id']==x]['Cliente'].values[0]}",
                                                key="sel_fac_v_del")
                    if st.button("Confirmar Eliminación Definitiva Venta", type="secondary"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("SELECT numero_factura, tercero_nombre FROM facturas WHERE id = %s AND empresa_id = %s", (fac_eliminar, str(selected_emp_id)))
                        row_del = c.fetchone()
                        if row_del:
                            glosa_v = f"Factura Venta {row_del[0]} a {row_del[1]}"
                            glosa_cb = f"Cobro Factura {row_del[0]} de {row_del[1]}"
                            c.execute("DELETE FROM libro_diario WHERE empresa_id = %s AND referencia_id = %s AND concepto IN (%s, %s)",
                                      (str(selected_emp_id), str(row_del[0]), glosa_v, glosa_cb))
                        c.execute("DELETE FROM facturas WHERE id = %s AND empresa_id = %s", (fac_eliminar, str(selected_emp_id)))
                        conn.commit()
                        conn.close()
                        st.success("Factura y asientos eliminados exitosamente.")
                        st.rerun()
        else:
            st.info("No hay facturas de ventas registradas en esta empresa.")

# ==========================================
# 4. DESPENSA FAMILIAR (COCINA)
# ==========================================
elif selected_menu == "🏠 Despensa Familiar (Cocina)":
    st.title("🏠 Control de Despensa Familiar y Cocina")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS despensa_articulos (
            id TEXT PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL,
            unidad TEXT NOT NULL, stock_actual NUMERIC DEFAULT 0, stock_minimo NUMERIC DEFAULT 1,
            costo_referencial NUMERIC DEFAULT 0, created_at TEXT
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS despensa_movimientos (
            id TEXT PRIMARY KEY, articulo_id TEXT NOT NULL, fecha DATE NOT NULL,
            tipo TEXT NOT NULL, cantidad NUMERIC NOT NULL, observaciones TEXT,
            usuario TEXT, created_at TEXT
        );
    """)
    conn.commit()
    df_desp = pd.read_sql_query("SELECT id, nombre, categoria, unidad, stock_actual, stock_minimo FROM despensa_articulos ORDER BY categoria, nombre", conn)
    if not df_desp.empty:
        df_desp = df_desp.rename(columns={
            'nombre': 'Artículo',
            'categoria': 'Categoría',
            'unidad': 'Unidad',
            'stock_actual': 'Existencia',
            'stock_minimo': 'Mínimo'
        })
    conn.close()

    t_desp1, t_desp2, t_desp3 = st.tabs(["📦 Inventario de Alimentos", "➕ Registrar Consumo", "🛒 Ingreso de Víveres"])
    with t_desp1:
        if not df_desp.empty:
            st.dataframe(df_desp[['Artículo', 'Categoría', 'Unidad', 'Existencia', 'Mínimo']], use_container_width=True)
        else:
            st.info("No hay alimentos en la despensa.")

        with st.expander("➕ Agregar Nuevo Alimento"):
            with st.form("form_nuevo_art_desp"):
                c1, c2, c3 = st.columns(3)
                nom_d = c1.text_input("Nombre del Alimento *")
                cat_d = c2.selectbox("Categoría:", ["Granos y Abarrotes", "Carnes y Embutidos", "Lácteos y Huevos", "Frutas y Verduras", "Limpieza", "Otros"])
                uni_d = c3.selectbox("Unidad:", ["Libras", "Unidades", "Bolsas", "Litros", "Docenas", "Cajas"])
                c4, c5 = st.columns(2)
                stk_ini = c4.number_input("Existencia Inicial:", min_value=0.0, step=1.0, value=0.0)
                stk_min = c5.number_input("Stock Mínimo:", min_value=1.0, step=1.0, value=2.0)
                if st.form_submit_button("Guardar en Despensa", type="primary"):
                    if nom_d.strip():
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("INSERT INTO despensa_articulos VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                  (f"desp-{int(datetime.now().timestamp())}", nom_d.strip(), cat_d, uni_d, float(stk_ini), float(stk_min), datetime.now().isoformat()))
                        conn.commit(); conn.close()
                        st.success("Guardado."); st.rerun()

    with t_desp2:
        if not df_desp.empty:
            with st.form("form_cons"):
                art_dict = {f"{r['Artículo']} ({r['Categoría']} - Hay: {r['Existencia']})": r['id'] for _, r in df_desp.iterrows()}
                asel = st.selectbox("Alimento a consumir:", list(art_dict.keys()))
                c_cant = st.number_input("Cantidad:", min_value=0.1, step=1.0, value=1.0)
                c_obs = st.text_input("Motivo:")
                if st.form_submit_button("Rebajar Stock", type="primary"):
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual - %s WHERE id = %s", (float(c_cant), art_dict[asel]))
                    c.execute("INSERT INTO despensa_movimientos VALUES (%s, %s, %s, 'SALIDA', %s, %s, %s, %s)",
                              (f"mov-{int(datetime.now().timestamp())}", art_dict[asel], str(hoy_gt()), float(c_cant), c_obs, curr_u['username'], datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Actualizado."); st.rerun()

    with t_desp3:
        if not df_desp.empty:
            with st.form("form_ingr"):
                art_dict2 = {f"{r['Artículo']} ({r['Categoría']})": r['id'] for _, r in df_desp.iterrows()}
                asel2 = st.selectbox("Alimento a ingresar:", list(art_dict2.keys()))
                i_cant = st.number_input("Cantidad:", min_value=0.1, step=1.0, value=1.0)
                i_obs = st.text_input("Comentarios:")
                if st.form_submit_button("Sumar Stock", type="primary"):
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("UPDATE despensa_articulos SET stock_actual = stock_actual + %s WHERE id = %s", (float(i_cant), art_dict2[asel2]))
                    c.execute("INSERT INTO despensa_movimientos VALUES (%s, %s, %s, 'ENTRADA', %s, %s, %s, %s)",
                              (f"mov-{int(datetime.now().timestamp())}", art_dict2[asel2], str(hoy_gt()), float(i_cant), i_obs, curr_u['username'], datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Actualizado."); st.rerun()

# ==========================================
# 5. DIRECTORIO TERCEROS
# ==========================================
elif selected_menu == "👥 Directorio Terceros (Clientes/Proveedores)":
    st.title("👥 Directorio Corporativo de Clientes y Proveedores")
    conn = get_db_connection()
    df_terc = pd.read_sql_query("SELECT nit, nombre, tipo, direccion, telefono, email FROM terceros_corporativos ORDER BY nombre", conn)
    if not df_terc.empty:
        df_terc = df_terc.rename(columns={
            'nit': 'NIT',
            'nombre': 'Nombre / Razón Social',
            'tipo': 'Tipo',
            'direccion': 'Dirección',
            'telefono': 'Teléfono',
            'email': 'Correo'
        })
    conn.close()
    if not df_terc.empty:
        st.dataframe(df_terc, use_container_width=True)
    with st.expander("➕ Registrar Nuevo Cliente / Proveedor"):
        with st.form("f_terc"):
            c1, c2 = st.columns(2)
            tnit = c1.text_input("NIT *")
            tnom = c2.text_input("Nombre *")
            c3, c4 = st.columns(2)
            ttipo = c3.selectbox("Tipo:", ["CLIENTE", "PROVEEDOR", "AMBOS"])
            tdir = c4.text_input("Dirección:", value="Guatemala")
            if st.form_submit_button("Guardar en Directorio", type="primary"):
                if tnit.strip() and tnom.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("INSERT INTO terceros_corporativos VALUES (%s, %s, %s, %s, %s, '', '', %s) ON CONFLICT (nit) DO UPDATE SET nombre = EXCLUDED.nombre",
                              (f"terc-{int(datetime.now().timestamp())}", tnit.strip().upper(), tnom.strip(), ttipo, tdir.strip(), datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Registrado."); st.rerun()

# ==========================================
# 6. CUENTAS POR PAGAR (CXP)
# ==========================================
elif selected_menu == "💳 Cuentas por Pagar (Proveedores)":
    if not selected_emp_id: st.stop()
    st.title(f"💳 Cuentas por Pagar — {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_comp = pd.read_sql_query("""
        SELECT id, fecha, serie, numero_factura, tercero_nit, tercero_nombre, total, saldo_pendiente, estado_pago, forma_pago
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA'
        ORDER BY fecha ASC, created_at ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()
    for col_n in ("total", "saldo_pendiente"):
        df_comp[col_n] = pd.to_numeric(df_comp[col_n], errors="coerce").fillna(0.0)
    df_comp["nit_key"] = df_comp["tercero_nit"].apply(_limpiar_nit)
    df_pend = df_comp[df_comp["estado_pago"] == "PENDIENTE"].copy()

    prov_dir = listar_proveedores(selected_emp_id)          # proveedores registrados (directorio + facturas)
    saldo_por_nit = df_pend.groupby("nit_key")["saldo_pendiente"].sum().to_dict() if not df_pend.empty else {}

    opciones_prov = {"📋 Todos los proveedores": None}
    for _, r_pv in prov_dir.iterrows():
        s_pv = float(saldo_por_nit.get(r_pv["nit_key"], 0.0))
        opciones_prov[f"{r_pv['nombre']} (NIT: {r_pv['nit']}) — Saldo Q{s_pv:,.2f}"] = r_pv["nit_key"]

    t_cxp1, t_cxp2, t_cxp3, t_cxp4 = st.tabs(["📋 Pendientes de Pago", "📊 Resumen por Proveedor", "🏭 Proveedores Registrados", "🛠️ Regularizar"])

    # ---- Pendientes + registro de pagos ----
    with t_cxp1:
        sel_prov_lbl = st.selectbox("🏭 Proveedor:", list(opciones_prov.keys()), key="cxp_prov_filter")
        nit_sel = opciones_prov[sel_prov_lbl]
        df_vista = df_pend if nit_sel is None else df_pend[df_pend["nit_key"] == nit_sel]

        m1, m2 = st.columns(2)
        m1.metric("Facturas pendientes", len(df_vista))
        m2.metric("Saldo pendiente", f"Q {df_vista['saldo_pendiente'].sum():,.2f}")

        if df_vista.empty:
            st.success("✨ ¡Al día! No hay facturas de proveedores pendientes de pago.")
        else:
            df_cxp = df_vista.rename(columns={
                "fecha": "Fecha", "serie": "Serie", "numero_factura": "No. Factura", "tercero_nombre": "Proveedor",
                "tercero_nit": "NIT", "total": "Total (Q)", "saldo_pendiente": "Saldo Pendiente (Q)"
            })[["id", "Fecha", "Serie", "No. Factura", "Proveedor", "NIT", "Total (Q)", "Saldo Pendiente (Q)"]].reset_index(drop=True)
            st.dataframe(df_cxp.drop(columns=["id"]), use_container_width=True)

            with st.form("form_abono_cxp"):
                st.subheader("Registrar Pago o Abono a Proveedor")
                c1, c2, c3, c4 = st.columns(4)
                fac_sel_p = c1.selectbox("Factura a Cancelar:", df_cxp['id'].tolist(),
                                         format_func=lambda x: f"Fac {df_cxp[df_cxp['id']==x]['No. Factura'].values[0]} - {df_cxp[df_cxp['id']==x]['Proveedor'].values[0]} (Saldo: Q{df_cxp[df_cxp['id']==x]['Saldo Pendiente (Q)'].values[0]:,.2f})")
                monto_abono = c2.number_input("Monto a Abonar (Q):", min_value=0.01, step=50.0)
                cta_salida_abono = c3.selectbox("Forma de Pago:", ["1.1.01.02 - Bancos (Transferencia)", "1.1.01.01 - Caja General (Efectivo)"])
                fecha_pago = c4.date_input("Fecha de Pago:", value=hoy_gt())

                if st.form_submit_button("Aplicar Pago al Proveedor", type="primary"):
                    row_sel = df_cxp[df_cxp['id'] == fac_sel_p].iloc[0]
                    saldo_actual = float(row_sel['Saldo Pendiente (Q)'])
                    if monto_abono > saldo_actual + 0.001:
                        st.error("El abono no puede superar el saldo pendiente.")
                    else:
                        nuevo_saldo = round(saldo_actual - float(monto_abono), 2)
                        nuevo_estado = "PAGADO" if nuevo_saldo <= 0 else "PENDIENTE"
                        conn = None
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s AND empresa_id = %s",
                                      (nuevo_saldo, nuevo_estado, fac_sel_p, str(selected_emp_id)))
                            c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                            p_no = int(c.fetchone()[0])
                            glosa_ab = f"Abono Factura {row_sel['No. Factura']} a {row_sel['Proveedor']}"
                            cod_sal, nom_sal = cta_salida_abono.split(" - ", 1)
                            q_ld = "INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                            c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_pago), "2.1.01.01", "Proveedores / Cuentas por Pagar", float(monto_abono), 0.0, glosa_ab, str(row_sel['No. Factura']), datetime.now().isoformat()))
                            c.execute(q_ld, (str(selected_emp_id), p_no, str(fecha_pago), str(cod_sal), str(nom_sal), 0.0, float(monto_abono), glosa_ab, str(row_sel['No. Factura']), datetime.now().isoformat()))
                            conn.commit(); conn.close()
                            st.success("Pago aplicado y saldos actualizados."); st.rerun()
                        except Exception as err:
                            try:
                                if conn:
                                    conn.rollback(); conn.close()
                            except Exception:
                                pass
                            st.error(f"❌ No se pudo aplicar el pago: {err}")

    # ---- Resumen por proveedor ----
    with t_cxp2:
        if df_pend.empty:
            st.info("No hay saldos pendientes con proveedores.")
        else:
            df_res = (df_pend.groupby("nit_key")
                      .agg(Proveedor=("tercero_nombre", "first"), NIT=("tercero_nit", "first"),
                           Facturas=("id", "count"), Saldo=("saldo_pendiente", "sum"), Mas_antigua=("fecha", "min"))
                      .reset_index(drop=True)
                      .sort_values("Saldo", ascending=False))
            df_res = df_res.rename(columns={"Saldo": "Saldo Pendiente (Q)", "Mas_antigua": "Factura más antigua"})
            st.dataframe(df_res, use_container_width=True)
            st.metric("Total por pagar a proveedores", f"Q {df_res['Saldo Pendiente (Q)'].sum():,.2f}")

    # ---- Proveedores registrados ----
    with t_cxp3:
        buscar_prov = st.text_input("🔎 Buscar proveedor (nombre o NIT):", key="cxp_buscar_prov")
        if prov_dir.empty:
            st.info("Aún no hay proveedores registrados. Se agregan automáticamente al guardar una factura de compra o desde el Directorio de Terceros.")
        else:
            df_reg = prov_dir[["nit", "nombre", "nit_key"]].copy()
            if df_comp.empty:
                df_reg["Facturas"] = 0
                df_reg["Total comprado (Q)"] = 0.0
            else:
                comp_res = (df_comp.groupby("nit_key").agg(Facturas=("id", "count"), **{"Total comprado (Q)": ("total", "sum")}).reset_index())
                df_reg = df_reg.merge(comp_res, on="nit_key", how="left")
                df_reg["Facturas"] = df_reg["Facturas"].fillna(0).astype(int)
                df_reg["Total comprado (Q)"] = df_reg["Total comprado (Q)"].fillna(0.0)
            df_reg["Saldo pendiente (Q)"] = df_reg["nit_key"].map(saldo_por_nit).fillna(0.0)
            df_reg = df_reg.drop(columns=["nit_key"]).rename(columns={"nit": "NIT", "nombre": "Proveedor"})
            if buscar_prov.strip():
                patron = re.escape(buscar_prov.strip())
                df_reg = df_reg[df_reg["Proveedor"].str.contains(patron, case=False, na=False) | df_reg["NIT"].str.contains(patron, case=False, na=False)]
            st.dataframe(df_reg, use_container_width=True)
            st.caption(f"{len(df_reg)} proveedor(es) · incluye los del Directorio de Terceros y los que ya tienen facturas en esta empresa.")

    # ---- Regularizar facturas guardadas sin saldo/estado ----
    with t_cxp4:
        if not is_admin:
            st.info("Solo un administrador puede regularizar facturas.")
        else:
            st.markdown("**Facturas a crédito que no aparecen como pendientes** (guardadas sin saldo o sin estado de pago y sin abonos registrados).")
            conn = get_db_connection()
            df_cand = pd.read_sql_query("""
                SELECT f.id, f.fecha, f.numero_factura, f.tercero_nombre, f.total, f.total_a_pagar_cobrar, f.estado_pago
                FROM facturas f
                WHERE f.empresa_id = %s AND f.tipo = 'COMPRA'
                  AND f.forma_pago LIKE 'Cr_dito%%'
                  AND COALESCE(f.estado_pago, '') <> 'PENDIENTE'
                  AND COALESCE(f.saldo_pendiente, 0) = 0
                  AND NOT EXISTS (SELECT 1 FROM libro_diario d
                                  WHERE d.empresa_id = f.empresa_id AND d.referencia_id = f.numero_factura
                                    AND d.concepto LIKE 'Abono Factura%%')
                ORDER BY f.fecha
            """, conn, params=(str(selected_emp_id),))
            conn.close()

            if df_cand.empty:
                st.success("No se encontraron facturas a crédito por regularizar.")
            else:
                st.dataframe(df_cand.drop(columns=["id"]), use_container_width=True)
                sel_ids = st.multiselect("Selecciona las facturas que realmente están pendientes de pago:", df_cand["id"].tolist(),
                                         format_func=lambda x: f"{df_cand[df_cand['id']==x]['numero_factura'].values[0]} - {df_cand[df_cand['id']==x]['tercero_nombre'].values[0]} (Q{float(df_cand[df_cand['id']==x]['total'].values[0]):,.2f})",
                                         key="cxp_reg_sel")
                if st.button("Marcar como pendientes de pago", type="primary", key="cxp_reg_btn") and sel_ids:
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("""
                        UPDATE facturas SET saldo_pendiente = COALESCE(NULLIF(total_a_pagar_cobrar, 0), total), estado_pago = 'PENDIENTE'
                        WHERE id = ANY(%s) AND empresa_id = %s AND tipo = 'COMPRA'
                    """, (sel_ids, str(selected_emp_id)))
                    conn.commit(); conn.close()
                    st.success(f"{len(sel_ids)} factura(s) restauradas en Cuentas por Pagar."); st.rerun()

            st.markdown("---")
            st.markdown("**Compras de contado sin estado de pago** (se marcan como PAGADO con saldo 0).")
            conn = get_db_connection(); c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago IS NULL AND forma_pago LIKE 'Contado%%'", (str(selected_emp_id),))
            n_contado = c.fetchone()[0]
            conn.close()
            st.write(f"Registros encontrados: **{n_contado}**")
            if n_contado > 0 and st.button("Normalizar compras de contado", key="cxp_norm_contado"):
                conn = get_db_connection(); c = conn.cursor()
                c.execute("UPDATE facturas SET estado_pago = 'PAGADO', saldo_pendiente = 0 WHERE empresa_id = %s AND tipo = 'COMPRA' AND estado_pago IS NULL AND forma_pago LIKE 'Contado%%'", (str(selected_emp_id),))
                conn.commit(); conn.close()
                st.success("Compras de contado normalizadas."); st.rerun()

# ==========================================
# 7. CUENTAS POR COBRAR (CXC)
# ==========================================
elif selected_menu == "💰 Cuentas por Cobrar (Clientes)":
    if not selected_emp_id: st.stop()
    st.title(f"💰 Cuentas por Cobrar — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_cxc = pd.read_sql_query("""
        SELECT id, fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
               tercero_nombre AS "Cliente", total AS "Total (Q)", saldo_pendiente AS "Saldo Pendiente (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA' AND estado_pago = 'PENDIENTE'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if df_cxc.empty:
        st.success("✨ ¡Excelente! No hay facturas de clientes pendientes de cobro.")
    else:
        st.dataframe(df_cxc, use_container_width=True)
        with st.form("form_abono_cxc"):
            st.subheader("Registrar Cobro a Cliente")
            c1, c2, c3 = st.columns(3)
            fac_sel_c = c1.selectbox("Factura a Cobrar:", df_cxc['id'].tolist(),
                                     format_func=lambda x: f"DTE {df_cxc[df_cxc['id']==x]['No. DTE'].values[0]} - {df_cxc[df_cxc['id']==x]['Cliente'].values[0]} (Saldo: Q{df_cxc[df_cxc['id']==x]['Saldo Pendiente (Q)'].values[0]:,.2f})")
            monto_cobro = c2.number_input("Monto Cobrado (Q):", min_value=0.01, step=50.0)
            cta_ing_cobro = c3.selectbox("Cuenta de Destino:", ["1.1.01.02 - Bancos (Depósito / Transferencia)", "1.1.01.01 - Caja General (Efectivo)"])

            if st.form_submit_button("Aplicar Cobro del Cliente", type="primary"):
                row_c = df_cxc[df_cxc['id'] == fac_sel_c].iloc[0]
                saldo_c_act = float(row_c['Saldo Pendiente (Q)'])
                if monto_cobro > saldo_c_act:
                    st.error("El monto a cobrar no puede superar el saldo pendiente.")
                else:
                    nuevo_saldo_c = round(saldo_c_act - float(monto_cobro), 2)
                    nuevo_est_c = "PAGADO" if nuevo_saldo_c <= 0 else "PENDIENTE"
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE facturas SET saldo_pendiente = %s, estado_pago = %s WHERE id = %s", (nuevo_saldo_c, nuevo_est_c, fac_sel_c))
                    c.execute("SELECT COALESCE(MAX(partida_no), 0) + 1 FROM libro_diario WHERE empresa_id = %s", (str(selected_emp_id),))
                    p_no_c = int(c.fetchone()[0])
                    glosa_cb = f"Cobro Factura {row_c['No. DTE']} de {row_c['Cliente']}"
                    cod_in, nom_in = cta_ing_cobro.split(" - ", 1)
                    q_ld = "INSERT INTO libro_diario (empresa_id, partida_no, fecha, codigo_cuenta, nombre_cuenta, debe, haber, concepto, referencia_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    c.execute(q_ld, (str(selected_emp_id), p_no_c, str(hoy_gt()), str(cod_in), str(nom_in), float(monto_cobro), 0.0, glosa_cb, str(row_c['No. DTE']), datetime.now().isoformat()))
                    c.execute(q_ld, (str(selected_emp_id), p_no_c, str(hoy_gt()), "1.1.02.01", "Clientes / Cuentas por Cobrar", 0.0, float(monto_cobro), glosa_cb, str(row_c['No. DTE']), datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    st.success("Cobro registrado y saldos actualizados."); st.rerun()

# ==========================================
# 8. KÁRDEX / INVENTARIOS (CORPORATIVO CON EDICIÓN)
# ==========================================
elif selected_menu == "📦 Control de Inventarios (Kárdex)":
    st.title("📦 Catálogo e Inventario de Productos (Kárdex)")
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
    conn.commit()

    c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'productos'")
    info_cols = c.fetchall()
    nombres_cols = [r[0].lower() for r in info_cols]
    dict_tipos = {r[0].lower(): r[1].lower() for r in info_cols}

    df_prod = pd.read_sql_query("SELECT * FROM productos ORDER BY codigo ASC", conn)
    conn.close()

    if not df_prod.empty:
        cols_mostrar = {}
        if 'codigo' in df_prod.columns: cols_mostrar['codigo'] = 'Código'
        if 'descripcion' in df_prod.columns: cols_mostrar['descripcion'] = 'Descripción'
        if 'existencia' in df_prod.columns: cols_mostrar['existencia'] = 'Existencia'
        if 'costo_promedio' in df_prod.columns: cols_mostrar['costo_promedio'] = 'Costo Promedio (Q)'
        if 'precio_venta' in df_prod.columns: cols_mostrar['precio_venta'] = 'Precio Venta Ref. (Q)'

        df_vista = df_prod.rename(columns=cols_mostrar)
        cols_finales = [c for c in ['Código', 'Descripción', 'Existencia', 'Costo Promedio (Q)', 'Precio Venta Ref. (Q)'] if c in df_vista.columns]
        st.dataframe(df_vista[cols_finales], use_container_width=True)
    else:
        st.info("No hay productos registrados en el catálogo.")

    # Pestañas o secciones para Crear y Modificar
    t_crear_p, t_editar_p = st.tabs(["➕ Crear Nuevo Producto", "✏️ Modificar / Editar Producto"])

    with t_crear_p:
        prox_cod = "1"
        if not df_prod.empty and 'codigo' in df_prod.columns:
            try:
                numeros = [int(re.sub(r'\D', '', str(x))) for x in df_prod['codigo'] if re.sub(r'\D', '', str(x))]
                if numeros:
                    prox_cod = str(max(numeros) + 1)
            except Exception:
                prox_cod = str(len(df_prod) + 1)

        with st.form("form_prod_corporativo"):
            c1, c2 = st.columns(2)
            pcod = c1.text_input("Código de Artículo (Secuencia 1 - 1,000,000) *", value=prox_cod, key="p_new_cod")
            pdesc = c2.text_input("Descripción del Producto *", placeholder="Ej. Cascarilla de arroz / Betaina al 96%", key="p_new_desc")
            c3, c4, c5 = st.columns(3)
            pexi = c3.number_input("Existencia Inicial:", min_value=0.0, step=1.0, value=0.0, key="p_new_exi")
            pcost = c4.number_input("Costo Unitario Referencial (Q):", min_value=0.0, step=10.0, value=0.0, key="p_new_cost")
            pprec = c5.number_input("Precio Venta Sugerido (Q):", min_value=0.0, step=10.0, value=0.0, key="p_new_prec")

            if st.form_submit_button("Guardar Producto en Catálogo", type="primary"):
                if pcod.strip() and pdesc.strip():
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        now_str = datetime.now().isoformat()
                        ts_num = int(datetime.now().timestamp())

                        campos = []
                        valores = []
                        placeholders = []

                        if 'id' in nombres_cols:
                            campos.append("id")
                            placeholders.append("%s")
                            t_id = dict_tipos.get('id', '')
                            if "int" in t_id or "serial" in t_id:
                                valores.append(ts_num % 2147483647)
                            else:
                                valores.append(f"prod-{ts_num}")

                        if 'empresa_id' in nombres_cols:
                            campos.append("empresa_id")
                            placeholders.append("%s")
                            valores.append(str(selected_emp_id) if selected_emp_id else "global")

                        campos.extend(["codigo", "descripcion", "existencia", "costo_promedio", "precio_venta"])
                        placeholders.extend(["%s", "%s", "%s", "%s", "%s"])
                        valores.extend([pcod.strip(), pdesc.strip(), float(pexi), float(pcost), float(pprec)])

                        if 'created_at' in nombres_cols:
                            campos.append("created_at")
                            placeholders.append("%s")
                            valores.append(now_str)

                        q_insert_prod = f"INSERT INTO productos ({', '.join(campos)}) VALUES ({', '.join(placeholders)})"
                        c.execute(q_insert_prod, tuple(valores))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Producto '{pdesc.strip()}' guardado exitosamente con código {pcod.strip()}.")
                        st.rerun()
                    except Exception as err:
                        st.error(f"❌ Error al guardar producto: {err}")
                else:
                    st.error("Código y Descripción son obligatorios.")

    with t_editar_p:
        if not df_prod.empty:
            dict_prods_edit = {f"Código {r['codigo']} — {r['descripcion']} (Existencia: {r.get('existencia', 0)})": r['id'] for _, r in df_prod.iterrows()}
            prod_sel_label = st.selectbox("Selecciona el producto a corregir o modificar:", list(dict_prods_edit.keys()), key="prod_sel_edit_box")
            prod_id_edit = dict_prods_edit[prod_sel_label]
            row_prod = df_prod[df_prod['id'] == prod_id_edit].iloc[0]

            with st.form(f"form_editar_producto_kardex_{prod_id_edit}"):
                st.subheader(f"Modificar datos del Producto")
                ce1, ce2 = st.columns(2)
                edit_pcod = ce1.text_input("Código de Artículo *", value=str(row_prod['codigo']), key=f"edit_prod_cod_{prod_id_edit}")
                edit_pdesc = ce2.text_input("Descripción del Producto *", value=str(row_prod['descripcion']), key=f"edit_prod_desc_{prod_id_edit}")

                ce3, ce4, ce5 = st.columns(3)
                edit_pexi = ce3.number_input("Existencia:", min_value=0.0, step=0.01, format="%.2f", value=float(row_prod.get('existencia', 0.0) or 0.0), key=f"edit_prod_exi_{prod_id_edit}")
                edit_pcost = ce4.number_input("Costo Promedio (Q):", min_value=0.0, step=10.0, format="%.2f", value=float(row_prod.get('costo_promedio', 0.0) or 0.0), key=f"edit_prod_cost_{prod_id_edit}")
                edit_pprec = ce5.number_input("Precio Venta Ref. (Q):", min_value=0.0, step=10.0, format="%.2f", value=float(row_prod.get('precio_venta', 0.0) or 0.0), key=f"edit_prod_prec_{prod_id_edit}")

                b_guardar_pe, b_eliminar_pe = st.columns([2, 1])
                btn_save_pe = b_guardar_pe.form_submit_button("💾 Guardar Corrección del Producto", type="primary")

                if btn_save_pe:
                    if edit_pcod.strip() and edit_pdesc.strip():
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("""
                                UPDATE productos
                                SET codigo = %s, descripcion = %s, existencia = %s, costo_promedio = %s, precio_venta = %s
                                WHERE id = %s
                            """, (edit_pcod.strip(), edit_pdesc.strip(), float(edit_pexi), float(edit_pcost), float(edit_pprec), prod_id_edit))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ Producto '{edit_pdesc.strip()}' actualizado exitosamente.")
                            st.rerun()
                        except Exception as err:
                            st.error(f"❌ Error al actualizar producto: {err}")
                    else:
                        st.error("Código y Descripción no pueden quedar vacíos.")

            if is_admin:
                with st.expander("🗑️ Eliminar Producto del Catálogo (Solo Administrador)"):
                    if st.button(f"Confirmar Eliminación Definitiva de '{row_prod['descripcion']}'", type="secondary", key="btn_del_prod"):
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM productos WHERE id = %s", (prod_id_edit,))
                            conn.commit()
                            conn.close()
                            st.warning(f"Producto '{row_prod['descripcion']}' eliminado.")
                            st.rerun()
                        except Exception as err:
                            st.error(f"Error al eliminar: {err}")
        else:
            st.info("No hay productos disponibles para editar.")

# ==========================================
# 9. LIBRO DE COMPRAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Compras (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Compras Tributario — {selected_empresa_info[1]}")
    col_idp_libro = ', COALESCE(idp, 0) AS "IDP (Q)"' if st.session_state.get("schema_idp") else ""
    conn = get_db_connection()
    df_c_sat = pd.read_sql_query(f"""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. Factura",
               tercero_nit AS "NIT Proveedor", tercero_nombre AS "Proveedor",
               subtotal AS "Base Imponible (Q)", iva AS "Crédito Fiscal (Q)"{col_idp_libro},
               monto_ret_isr AS "Ret. ISR (Q)", total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'COMPRA'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_c_sat.empty:
        st.dataframe(df_c_sat, use_container_width=True)
        c_e1, c_e2 = st.columns(2)
        buff_c = io.BytesIO()
        df_c_sat.to_excel(buff_c, index=False, engine='openpyxl')
        c_e1.download_button("📥 Descargar Libro en Excel (.xlsx)", buff_c.getvalue(), f"Libro_Compras_{selected_empresa_info[2]}.xlsx")

        with c_e2:
            r_sat_c = st.text_input("Resolución SAT:", value="2026-5-100-01", key="res_c_sat")
            f_ini_c = st.number_input("Folio Inicial:", min_value=1, value=1, key="fol_c_sat")
            pdf_bytes_c = exportar_libro_pdf(df_c_sat, "Libro de Compras y Servicios", selected_empresa_info[1], selected_empresa_info[2], r_sat_c, f_ini_c)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes_c, f"Libro_Compras_{selected_empresa_info[2]}.pdf", "application/pdf")
    else:
        st.info("Sin registros de compras.")

# ==========================================
# 10. LIBRO DE VENTAS (SAT)
# ==========================================
elif selected_menu == "📋 Libro de Ventas (SAT)":
    if not selected_emp_id: st.stop()
    st.title(f"📋 Libro de Ventas Tributario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_v_sat = pd.read_sql_query("""
        SELECT fecha AS "Fecha", serie AS "Serie", numero_factura AS "No. DTE",
               tercero_nit AS "NIT Cliente", tercero_nombre AS "Cliente",
               subtotal AS "Base Imponible (Q)", iva AS "Débito Fiscal (Q)",
               monto_ret_isr AS "Ret. ISR Soportada (Q)", total AS "Total (Q)"
        FROM facturas WHERE empresa_id = %s AND tipo = 'VENTA'
        ORDER BY fecha ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_v_sat.empty:
        st.dataframe(df_v_sat, use_container_width=True)
        col_v1, col_v2 = st.columns(2)
        buff_v = io.BytesIO()
        df_v_sat.to_excel(buff_v, index=False, engine='openpyxl')
        col_v1.download_button("📥 Descargar Libro en Excel (.xlsx)", buff_v.getvalue(), f"Libro_Ventas_{selected_empresa_info[2]}.xlsx")

        with col_v2:
            r_sat_v = st.text_input("Resolución SAT:", value="2026-5-100-02", key="res_v_sat")
            f_ini_v = st.number_input("Folio Inicial:", min_value=1, value=1, key="fol_v_sat")
            pdf_bytes_v = exportar_libro_pdf(df_v_sat, "Libro de Ventas y Servicios", selected_empresa_info[1], selected_empresa_info[2], r_sat_v, f_ini_v)
            st.download_button("📄 Descargar Libro en PDF", pdf_bytes_v, f"Libro_Ventas_{selected_empresa_info[2]}.pdf", "application/pdf")
    else:
        st.info("Sin registros de ventas.")

# ==========================================
# 11. LIBRO DE RETENCIONES
# ==========================================
elif selected_menu == "📑 Libro de Retenciones":
    if not selected_emp_id: st.stop()
    st.title(f"📑 Libro de Retenciones Fiscales (SAT) — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ret = pd.read_sql_query("""
        SELECT tipo AS "Operación", fecha AS "Fecha", numero_factura AS "Documento",
               tercero_nombre AS "Tercero", subtotal AS "Base (Q)", monto_ret_isr AS "Retención ISR (Q)", monto_ret_iva AS "Retención IVA (Q)"
        FROM facturas WHERE empresa_id = %s AND (monto_ret_isr > 0 OR monto_ret_iva > 0)
        ORDER BY fecha DESC
    """, conn, params=(str(selected_emp_id),))
    conn.close()
    if not df_ret.empty:
        st.dataframe(df_ret, use_container_width=True)
    else:
        st.info("No hay retenciones registradas.")

# ==========================================
# 12. LIBRO DIARIO
# ==========================================
elif selected_menu == "📖 Libro Diario":
    if not selected_emp_id: st.stop()
    st.title(f"📖 Libro Diario — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ld = pd.read_sql_query("""
        SELECT partida_no AS "Partida #", fecha AS "Fecha", codigo_cuenta AS "Código",
               nombre_cuenta AS "Cuenta", debe AS "Debe (Q)", haber AS "Haber (Q)",
               concepto AS "Glosa", referencia_id AS "Documento"
        FROM libro_diario WHERE empresa_id = %s
        ORDER BY partida_no ASC, id ASC
    """, conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_ld.empty:
        c1, c2 = st.columns(2)
        c1.metric("Total DEBE", f"Q {df_ld['Debe (Q)'].sum():,.2f}")
        c2.metric("Total HABER", f"Q {df_ld['Haber (Q)'].sum():,.2f}")
        st.dataframe(df_ld, use_container_width=True)
    else:
        st.info("No hay partidas registradas.")

# ==========================================
# 13. LIBRO MAYOR
# ==========================================
elif selected_menu == "📚 Libro Mayor":
    if not selected_emp_id: st.stop()
    st.title(f"📚 Libro Mayor — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_ld_m = pd.read_sql_query("SELECT codigo_cuenta, nombre_cuenta, debe, haber FROM libro_diario WHERE empresa_id = %s", conn, params=(str(selected_emp_id),))
    conn.close()

    if not df_ld_m.empty:
        df_may = df_ld_m.groupby(['codigo_cuenta', 'nombre_cuenta']).agg({'debe': 'sum', 'haber': 'sum'}).reset_index()
        df_may['Saldo Deudor (Q)'] = df_may.apply(lambda r: round(r['debe'] - r['haber'], 2) if r['debe'] >= r['haber'] else 0.0, axis=1)
        df_may['Saldo Acreedor (Q)'] = df_may.apply(lambda r: round(r['haber'] - r['debe'], 2) if r['haber'] > r['debe'] else 0.0, axis=1)
        df_may.rename(columns={'codigo_cuenta': 'Código', 'nombre_cuenta': 'Cuenta', 'debe': 'Debe Acumulado (Q)', 'haber': 'Haber Acumulado (Q)'}, inplace=True)
        st.dataframe(df_may, use_container_width=True)
    else:
        st.info("Sin partidas para mayorizar.")

# ==========================================
# 14. BALANCE GENERAL (SALDOS)
# ==========================================
elif selected_menu == "⚖️ Balance General (Saldos)":
    if not selected_emp_id: st.stop()
    st.title(f"⚖️ Balance General de Saldos — {selected_empresa_info[1]}")

    conn = get_db_connection()
    df_anios_b = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_b = df_anios_b['anio'].dropna().tolist() if not df_anios_b.empty else [str(datetime.now().year)]

    col_b1, col_b2, col_b3 = st.columns([2, 1.5, 1])
    modo_b = col_b1.radio("Visualizar Balance por:", ["📅 Por Mes", "📆 Por Año", "🌐 Saldo Acumulado (Mes a Mes)"], horizontal=True, key="bal_modo_rad")
    anio_b = col_b2.selectbox("Año Fiscal:", anios_b, key="bal_anio_sel")
    col_b3.color_picker("Color del Año:", value="#1E3A8A", key="bal_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_b == "🌐 Saldo Acumulado (Mes a Mes)":
        q_b_acum = """
            SELECT d.codigo_cuenta AS "Código", d.nombre_cuenta AS "Cuenta",
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_raw_b = pd.read_sql_query(q_b_acum, conn, params=(str(selected_emp_id), str(anio_b)))
        conn.close()

        if not df_raw_b.empty:
            df_raw_b['neto'] = df_raw_b['tot_debe'] - df_raw_b['tot_haber']
            pivot_b = df_raw_b.pivot_table(index=['Código', 'Cuenta'], columns='mes', values='neto', aggfunc='sum', fill_value=0.0).reset_index()
            for m_num, m_nom in meses_nombres.items():
                if m_num not in pivot_b.columns:
                    pivot_b[m_num] = 0.0
            cols_ord = [m for m in sorted(meses_nombres.keys())]
            pivot_b['TOTAL ANUAL ACUMULADO (Q)'] = pivot_b[cols_ord].sum(axis=1)
            pivot_b.rename(columns=meses_nombres, inplace=True)
            st.dataframe(pivot_b, use_container_width=True)

            b_xls = io.BytesIO()
            pivot_b.to_excel(b_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance Acumulado en Excel (.xlsx)", b_xls.getvalue(), f"Balance_Acumulado_{anio_b}.xlsx")
        else:
            st.info("Sin registros en este ejercicio fiscal.")
    else:
        cond_b = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_b = [str(selected_emp_id), str(anio_b)]
        if modo_b == "📅 Por Mes":
            mes_b = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="b_mes_sel")
            cond_b += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_b.append(mes_b)

        q_b = f"""
            SELECT codigo_cuenta AS "Código", nombre_cuenta AS "Cuenta",
                   SUM(debe) AS "Total Debe (Q)", SUM(haber) AS "Total Haber (Q)"
            FROM libro_diario d
            WHERE {cond_b}
            GROUP BY codigo_cuenta, nombre_cuenta ORDER BY codigo_cuenta
        """
        df_b = pd.read_sql_query(q_b, conn, params=tuple(params_b))
        conn.close()

        if not df_b.empty:
            df_b['Saldo Deudor (Q)'] = df_b.apply(lambda r: round(r['Total Debe (Q)'] - r['Total Haber (Q)'], 2) if r['Total Debe (Q)'] >= r['Total Haber (Q)'] else 0.0, axis=1)
            df_b['Saldo Acreedor (Q)'] = df_b.apply(lambda r: round(r['Total Haber (Q)'] - r['Total Debe (Q)'], 2) if r['Total Haber (Q)'] > r['Total Debe (Q)'] else 0.0, axis=1)
            st.dataframe(df_b, use_container_width=True)

            c1, c2 = st.columns(2)
            c1.metric("Sumas Deudoras", f"Q {df_b['Saldo Deudor (Q)'].sum():,.2f}")
            c2.metric("Sumas Acreedoras", f"Q {df_b['Saldo Acreedor (Q)'].sum():,.2f}")

            b_xls2 = io.BytesIO()
            df_b.to_excel(b_xls2, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance en Excel (.xlsx)", b_xls2.getvalue(), f"Balance_{anio_b}.xlsx")
        else:
            st.info("Sin registros en el período seleccionado.")

# ==========================================
# 15. BALANCE DE SITUACIÓN FINANCIERA (CLASIFICADO CON GANANCIA)
# ==========================================
elif selected_menu == "🏛️ Balance Situación Financiera":
    if not selected_emp_id: st.stop()
    st.title(f"🏛️ Balance de Situación Financiera — {selected_empresa_info[1]}")
    st.caption(f"Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios_s = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_s = df_anios_s['anio'].dropna().tolist() if not df_anios_s.empty else [str(datetime.now().year)]

    col_s1, col_s2, col_s3 = st.columns([2, 1.5, 1])
    modo_sit = col_s1.radio("Visualizar Situación Financiera por:", ["📅 Por Mes", "📆 Por Año", "🌐 Saldo Acumulado (Mes a Mes)"], horizontal=True, key="sit_modo_rad")
    anio_sit = col_s2.selectbox("Año Fiscal:", anios_s, key="sit_anio_sel")
    col_s3.color_picker("Color de Cabecera:", value="#1E3A8A", key="sit_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_sit == "🌐 Saldo Acumulado (Mes a Mes)":
        q_sit_acum = """
            SELECT d.codigo_cuenta, d.nombre_cuenta, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_sit_raw = pd.read_sql_query(q_sit_acum, conn, params=(str(selected_emp_id), str(anio_sit)))
        conn.close()

        if not df_sit_raw.empty:
            # Saldo natural: Activo y Gasto son deudores (debe - haber); Pasivo, Capital e Ingreso son acreedores (haber - debe)
            df_sit_raw['saldo'] = df_sit_raw.apply(
                lambda r: (r['tot_debe'] - r['tot_haber']) if r['tipo'] in ('Activo', 'Gasto') else (r['tot_haber'] - r['tot_debe']), axis=1
            )
            # Matriz horizontal
            piv_sit = df_sit_raw[df_sit_raw['tipo'].isin(['Activo', 'Pasivo', 'Capital'])].pivot_table(
                index=['codigo_cuenta', 'nombre_cuenta', 'tipo'], columns='mes', values='saldo', aggfunc='sum', fill_value=0.0
            ).reset_index()
            for m_num in sorted(meses_nombres.keys()):
                if m_num not in piv_sit.columns: piv_sit[m_num] = 0.0

            cols_m = sorted(meses_nombres.keys())
            piv_sit['TOTAL ANUAL ACUMULADO (Q)'] = piv_sit[cols_m].sum(axis=1)

            # Inyectar fila de Ganancia Neta mes a mes para que cuadre exactamente
            fila_ganancia = {"codigo_cuenta": "3.2.01.01", "nombre_cuenta": "Ganancia Neta del Ejercicio (Después de ISR)", "tipo": "Capital"}
            for m_k in cols_m:
                sub_m = df_sit_raw[df_sit_raw['mes'] == m_k]
                ing_m = sub_m[sub_m['tipo'] == 'Ingreso']['saldo'].sum()
                gas_m = sub_m[sub_m['tipo'] == 'Gasto']['saldo'].sum()
                ut_m = ing_m - gas_m
                imp_m, gan_m, _ = calcular_impuesto_renta(selected_empresa_info[3], ing_m, ut_m)
                fila_ganancia[m_k] = gan_m
            fila_ganancia['TOTAL ANUAL ACUMULADO (Q)'] = sum([fila_ganancia[m] for m in cols_m])

            piv_sit = pd.concat([piv_sit, pd.DataFrame([fila_ganancia])], ignore_index=True)
            piv_sit.rename(columns={'codigo_cuenta': 'Código', 'nombre_cuenta': 'Rubro / Cuenta', 'tipo': 'Clasificación'}, inplace=True)
            piv_sit.rename(columns=meses_nombres, inplace=True)
            st.dataframe(piv_sit, use_container_width=True)

            s_xls = io.BytesIO()
            piv_sit.to_excel(s_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Balance de Situación en Excel (.xlsx)", s_xls.getvalue(), f"Situacion_Financiera_{anio_sit}.xlsx")
        else:
            st.info("Sin registros en este ejercicio fiscal.")
    else:
        cond_sit = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_sit = [str(selected_emp_id), str(anio_sit)]
        periodo_label = f"Año Fiscal {anio_sit}"
        if modo_sit == "📅 Por Mes":
            mes_sit = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="sit_mes_sel")
            cond_sit += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_sit.append(mes_sit)
            periodo_label = f"{meses_nombres[mes_sit]} {anio_sit}"

        q_s_rep = f"""
            SELECT n.codigo, n.nombre, n.tipo,
                   SUM(d.debe) AS tot_debe, SUM(d.haber) AS tot_haber
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE {cond_sit}
            GROUP BY n.codigo, n.nombre, n.tipo
            ORDER BY n.codigo ASC
        """
        df_s_rep = pd.read_sql_query(q_s_rep, conn, params=tuple(params_sit))
        conn.close()

        if not df_s_rep.empty:
            # Saldo natural: Activo y Gasto son deudores; Pasivo, Capital e Ingreso son acreedores
            df_s_rep['Saldo'] = df_s_rep.apply(
                lambda r: (r['tot_debe'] - r['tot_haber']) if r['tipo'] in ('Activo', 'Gasto') else (r['tot_haber'] - r['tot_debe']), axis=1
            )
            # Cálculo de ganancia
            tot_ing = df_s_rep[df_s_rep['tipo'] == 'Ingreso']['Saldo'].sum()
            tot_gas = df_s_rep[df_s_rep['tipo'] == 'Gasto']['Saldo'].sum()
            ut_ant = tot_ing - tot_gas
            m_imp, ganancia_neta_sit, tasa_txt = calcular_impuesto_renta(selected_empresa_info[3], tot_ing, ut_ant)

            activos = df_s_rep[df_s_rep['tipo'] == 'Activo']
            pasivos = df_s_rep[df_s_rep['tipo'] == 'Pasivo']
            capital = df_s_rep[df_s_rep['tipo'] == 'Capital']

            tot_act = float(activos['Saldo'].sum())
            tot_pas = float(pasivos['Saldo'].sum()) + m_imp
            tot_pat = float(capital['Saldo'].sum()) + ganancia_neta_sit
            tot_pas_pat = tot_pas + tot_pat

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total ACTIVO", f"Q {tot_act:,.2f}")
            c2.metric("Total PASIVO", f"Q {tot_pas:,.2f}")
            c3.metric("Total PATRIMONIO (con Ganancia)", f"Q {tot_pat:,.2f}")
            diff_cuad = round(tot_act - tot_pas_pat, 2)
            c4.metric("Diferencia de Cuadratura", f"Q {diff_cuad:,.2f}", delta="Cuadrado ✅" if abs(diff_cuad) < 0.05 else "Revisar ⚠️")

            st.markdown("---")
            filas_sit_clas = []
            for _, r in activos.iterrows():
                filas_sit_clas.append({"Clasificación": "ACTIVO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            for _, r in pasivos.iterrows():
                filas_sit_clas.append({"Clasificación": "PASIVO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            if m_imp > 0:
                filas_sit_clas.append({"Clasificación": "PASIVO", "Cuenta": f"2.1.03.99 - Provisión ISR por Pagar ({tasa_txt})", "Monto (Q)": m_imp})
            for _, r in capital.iterrows():
                filas_sit_clas.append({"Clasificación": "PATRIMONIO", "Cuenta": f"{r['codigo']} - {r['nombre']}", "Monto (Q)": r['Saldo']})
            filas_sit_clas.append({"Clasificación": "PATRIMONIO", "Cuenta": "3.2.01.01 - Ganancia Neta del Ejercicio", "Monto (Q)": ganancia_neta_sit})

            df_sit_final = pd.DataFrame(filas_sit_clas)
            st.dataframe(df_sit_final, use_container_width=True)

            col_ds1, col_ds2 = st.columns(2)
            s_xls2 = io.BytesIO()
            df_sit_final.to_excel(s_xls2, index=False, engine='openpyxl')
            col_ds1.download_button("📥 Descargar Balance Situación en Excel (.xlsx)", s_xls2.getvalue(), f"Situacion_Financiera_{periodo_label}.xlsx")
            pdf_s = exportar_reporte_pdf(df_sit_final, f"Balance de Situación Financiera - {periodo_label}", selected_empresa_info[1], selected_empresa_info[2])
            col_ds2.download_button("📄 Descargar Balance Situación en PDF", pdf_s, f"Situacion_Financiera_{periodo_label}.pdf", "application/pdf")
        else:
            st.info("Sin registros en este período.")

# ==========================================
# 16. ESTADO DE RESULTADOS (OFICIAL GUATEMALA)
# ==========================================
elif selected_menu == "📉 Estado de Resultados":
    if not selected_emp_id: st.stop()
    st.title(f"📉 Estado de Resultados Oficial — {selected_empresa_info[1]}")
    st.caption(f"Régimen Fiscal: {selected_empresa_info[3]}")

    conn = get_db_connection()
    df_anios_er = pd.read_sql_query("SELECT DISTINCT SUBSTRING(fecha FROM 1 FOR 4) AS anio FROM libro_diario WHERE empresa_id = %s ORDER BY anio DESC", conn, params=(str(selected_emp_id),))
    anios_er = df_anios_er['anio'].dropna().tolist() if not df_anios_er.empty else [str(datetime.now().year)]

    col_e1, col_e2, col_e3 = st.columns([2, 1.5, 1])
    modo_er = col_e1.radio("Visualizar Estado de Resultados por:", ["📅 Mes Específico", "📆 Año Completo", "🌐 Histórico Acumulado (Mes a Mes)"], horizontal=True, key="er_modo_rad")
    anio_er = col_e2.selectbox("Año Fiscal:", anios_er, key="er_anio_sel")
    col_e3.color_picker("Color de Cabecera:", value="#1E3A8A", key="er_col_pic")

    meses_nombres = {"01":"Enero", "02":"Febrero", "03":"Marzo", "04":"Abril", "05":"Mayo", "06":"Junio", "07":"Julio", "08":"Agosto", "09":"Septiembre", "10":"Octubre", "11":"Noviembre", "12":"Diciembre"}

    if modo_er == "🌐 Histórico Acumulado (Mes a Mes)":
        q_er_acum = """
            SELECT d.codigo_cuenta, d.nombre_cuenta, n.tipo,
                   SUBSTRING(d.fecha FROM 6 FOR 2) AS mes,
                   SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS total_mov
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s AND n.tipo IN ('Ingreso', 'Gasto')
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo, SUBSTRING(d.fecha FROM 6 FOR 2)
            ORDER BY d.codigo_cuenta
        """
        df_er_raw = pd.read_sql_query(q_er_acum, conn, params=(str(selected_emp_id), str(anio_er)))
        conn.close()

        if not df_er_raw.empty:
            cols_meses_keys = sorted(meses_nombres.keys())

            # Pivot table estructurada de arriba hacia abajo
            # 1. Ingresos
            df_i = df_er_raw[df_er_raw['tipo'] == 'Ingreso'].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()
            # 2. Costo de ventas (5.1.01)
            df_c = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (df_er_raw['codigo_cuenta'].str.startswith('5.1.01'))].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()
            # 3. Gastos operativos
            df_g = df_er_raw[(df_er_raw['tipo'] == 'Gasto') & (~df_er_raw['codigo_cuenta'].str.startswith('5.1.01'))].pivot_table(index=['codigo_cuenta', 'nombre_cuenta'], columns='mes', values='total_mov', aggfunc='sum', fill_value=0.0).reset_index()

            filas_er_matriz = []

            # Función auxiliar para renglones
            def agregar_bloque(df_sub, nombre_bloque):
                if not df_sub.empty:
                    for m in cols_meses_keys:
                        if m not in df_sub.columns: df_sub[m] = 0.0
                    for _, r in df_sub.iterrows():
                        fila = {"Rubro Contable": f"{r['codigo_cuenta']} - {r['nombre_cuenta']}", "Sección": nombre_bloque}
                        for m in cols_meses_keys: fila[m] = float(r[m])
                        fila['TOTAL ACUMULADO (Q)'] = sum([fila[m] for m in cols_meses_keys])
                        filas_er_matriz.append(fila)

            # Renglones formales
            agregar_bloque(df_i, "(+) INGRESOS OPERACIONALES")
            agregar_bloque(df_c, "(-) COSTO DE VENTAS")
            agregar_bloque(df_g, "(-) GASTOS OPERATIVOS")

            # Totales calculados mes a mes
            fila_ing_tot = {"Rubro Contable": "TOTAL INGRESOS BRUTOS", "Sección": "RESUMEN"}
            fila_cost_tot = {"Rubro Contable": "TOTAL COSTO DE VENTAS", "Sección": "RESUMEN"}
            fila_bruta = {"Rubro Contable": "(=) UTILIDAD BRUTA EN VENTAS", "Sección": "RESUMEN"}
            fila_gas_tot = {"Rubro Contable": "TOTAL GASTOS OPERATIVOS", "Sección": "RESUMEN"}
            fila_op = {"Rubro Contable": "(=) UTILIDAD DE OPERACIÓN", "Sección": "RESUMEN"}
            fila_isr = {"Rubro Contable": f"(-) PROVISIÓN ISR ({selected_empresa_info[3]})", "Sección": "IMPUESTO"}
            fila_neta = {"Rubro Contable": "(=) UTILIDAD NETA DEL EJERCICIO", "Sección": "RESULTADO NETO"}

            for m in cols_meses_keys:
                sub_m = df_er_raw[df_er_raw['mes'] == m]
                ing_m = sub_m[sub_m['tipo'] == 'Ingreso']['total_mov'].sum()
                cost_m = sub_m[(sub_m['tipo'] == 'Gasto') & (sub_m['codigo_cuenta'].str.startswith('5.1.01'))]['total_mov'].sum()
                gas_m = sub_m[(sub_m['tipo'] == 'Gasto') & (~sub_m['codigo_cuenta'].str.startswith('5.1.01'))]['total_mov'].sum()

                bruta_m = ing_m - cost_m
                op_m = bruta_m - gas_m
                imp_m, net_m, _ = calcular_impuesto_renta(selected_empresa_info[3], ing_m, op_m)

                fila_ing_tot[m] = ing_m
                fila_cost_tot[m] = cost_m
                fila_bruta[m] = bruta_m
                fila_gas_tot[m] = gas_m
                fila_op[m] = op_m
                fila_isr[m] = imp_m
                fila_neta[m] = net_m

            for item in [fila_ing_tot, fila_cost_tot, fila_bruta, fila_gas_tot, fila_op, fila_isr, fila_neta]:
                item['TOTAL ACUMULADO (Q)'] = sum([item[m] for m in cols_meses_keys])
                filas_er_matriz.append(item)

            df_er_display = pd.DataFrame(filas_er_matriz)
            df_er_display.rename(columns=meses_nombres, inplace=True)
            st.dataframe(df_er_display, use_container_width=True)

            er_xls = io.BytesIO()
            df_er_display.to_excel(er_xls, index=False, engine='openpyxl')
            st.download_button("📥 Descargar Estado de Resultados Matricial en Excel (.xlsx)", er_xls.getvalue(), f"Estado_Resultados_Acumulado_{anio_er}.xlsx")
        else:
            st.info("Sin operaciones registradas en este ejercicio fiscal.")
    else:
        cond_er = "d.empresa_id = %s AND SUBSTRING(d.fecha FROM 1 FOR 4) = %s"
        params_er = [str(selected_emp_id), str(anio_er)]
        lbl_periodo = f"Año Fiscal Completo {anio_er}"
        if modo_er == "📅 Mes Específico":
            mes_er = st.selectbox("Seleccionar Mes:", list(meses_nombres.keys()), format_func=lambda x: f"{x} - {meses_nombres[x]}", key="er_mes_sel")
            cond_er += " AND SUBSTRING(d.fecha FROM 6 FOR 2) = %s"
            params_er.append(mes_er)
            lbl_periodo = f"{meses_nombres[mes_er]} {anio_er}"

        q_er_p = f"""
            SELECT d.codigo_cuenta AS "Código", d.nombre_cuenta AS "Cuenta", n.tipo,
                   SUM(CASE WHEN n.tipo = 'Ingreso' THEN (d.haber - d.debe) ELSE (d.debe - d.haber) END) AS "Monto (Q)"
            FROM libro_diario d
            JOIN nomenclatura n ON d.codigo_cuenta = n.codigo AND d.empresa_id = n.empresa_id
            WHERE {cond_er} AND n.tipo IN ('Ingreso', 'Gasto')
            GROUP BY d.codigo_cuenta, d.nombre_cuenta, n.tipo
            ORDER BY d.codigo_cuenta
        """
        df_er_p = pd.read_sql_query(q_er_p, conn, params=tuple(params_er))
        conn.close()

        if not df_er_p.empty:
            tot_i = df_er_p[df_er_p['tipo'] == 'Ingreso']['Monto (Q)'].sum()
            tot_c = df_er_p[(df_er_p['tipo'] == 'Gasto') & (df_er_p['Código'].str.startswith('5.1.01'))]['Monto (Q)'].sum()
            tot_g = df_er_p[(df_er_p['tipo'] == 'Gasto') & (~df_er_p['Código'].str.startswith('5.1.01'))]['Monto (Q)'].sum()

            utilidad_bruta = tot_i - tot_c
            utilidad_op = utilidad_bruta - tot_g
            impuesto, ganancia_neta, tasa_desc = calcular_impuesto_renta(selected_empresa_info[3], tot_i, utilidad_op)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("(+) Ingresos Operacionales", f"Q {tot_i:,.2f}")
            c2.metric("(=) Utilidad Bruta", f"Q {utilidad_bruta:,.2f}")
            c3.metric(f"(-) Provisión ISR ({tasa_desc})", f"Q {impuesto:,.2f}")
            c4.metric("(=) Ganancia Neta Ejercicio", f"Q {ganancia_neta:,.2f}", delta="Ganancia" if ganancia_neta >= 0 else "Pérdida")

            st.markdown("---")
            filas_verticales = [
                {"Estructura": "INGRESOS", "Concepto": "Total Ingresos Operacionales", "Monto (Q)": tot_i},
                {"Estructura": "COSTOS", "Concepto": "(-) Costo de Ventas", "Monto (Q)": tot_c},
                {"Estructura": "MARGEN", "Concepto": "(=) Utilidad Bruta en Ventas", "Monto (Q)": utilidad_bruta},
                {"Estructura": "GASTOS", "Concepto": "(-) Gastos de Operación y Administración", "Monto (Q)": tot_g},
                {"Estructura": "OPERACIÓN", "Concepto": "(=) Utilidad en Operación", "Monto (Q)": utilidad_op},
                {"Estructura": "IMPUESTOS", "Concepto": f"(-) Provisión ISR ({tasa_desc})", "Monto (Q)": impuesto},
                {"Estructura": "RESULTADO NETO", "Concepto": "(=) Utilidad Neta del Ejercicio", "Monto (Q)": ganancia_neta}
            ]
            df_er_vert = pd.DataFrame(filas_verticales)
            st.dataframe(df_er_vert, use_container_width=True)

            col_de1, col_de2 = st.columns(2)
            er_xls2 = io.BytesIO()
            df_er_vert.to_excel(er_xls2, index=False, engine='openpyxl')
            col_de1.download_button("📥 Descargar Estado de Resultados en Excel (.xlsx)", er_xls2.getvalue(), f"Estado_Resultados_{lbl_periodo}.xlsx")
            pdf_er = exportar_reporte_pdf(df_er_vert, f"Estado de Resultados Oficial - {lbl_periodo}", selected_empresa_info[1], selected_empresa_info[2])
            col_de2.download_button("📄 Descargar Estado de Resultados en PDF", pdf_er, f"Estado_Resultados_{lbl_periodo}.pdf", "application/pdf")
        else:
            st.info("Sin operaciones registradas en el período.")

# ==========================================
# 17. NOMENCLATURA CONTABLE
# ==========================================
elif selected_menu == "🗂️ Nomenclatura Contable":
    if not selected_emp_id: st.stop()
    st.title(f"🗂️ Nomenclatura Contable — {selected_empresa_info[1]}")
    conn = get_db_connection()
    df_nom = pd.read_sql_query("SELECT codigo, nombre, tipo FROM nomenclatura WHERE empresa_id = %s ORDER BY codigo", conn, params=(str(selected_emp_id),))
    if not df_nom.empty:
        df_nom = df_nom.rename(columns={
            'codigo': 'Código',
            'nombre': 'Nombre de Cuenta',
            'tipo': 'Tipo'
        })
    conn.close()

    if not df_nom.empty:
        st.dataframe(df_nom, use_container_width=True)

    with st.expander("➕ Crear Nueva Cuenta Contable"):
        with st.form("form_nueva_cta"):
            c1, c2, c3 = st.columns(3)
            cod_c = c1.text_input("Código (ej: 4.1.01.04)")
            nom_c = c2.text_input("Nombre de Cuenta")
            tip_c = c3.selectbox("Clasificación:", ["Activo", "Pasivo", "Capital", "Ingreso", "Gasto"])
            if st.form_submit_button("Guardar Cuenta", type="primary"):
                if cod_c.strip() and nom_c.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    c.execute("INSERT INTO nomenclatura (empresa_id, codigo, nombre, tipo) VALUES (%s, %s, %s, %s)",
                              (str(selected_emp_id), cod_c.strip(), nom_c.strip(), tip_c))
                    conn.commit(); conn.close()
                    st.success("Cuenta agregada exitosamente."); st.rerun()

# ==========================================
# 18. CONTROL DE USUARIOS (ADMIN) — CON EDICIÓN Y ASIGNACIÓN DE EMPRESAS
# ==========================================
elif selected_menu == "👤 Control de Usuarios (Admin)":
    if not is_admin: st.error("Acceso restringido."); st.stop()
    st.title("👤 Control de Usuarios del Sistema")

    conn = get_db_connection()
    df_users = pd.read_sql_query("SELECT id, username, name, email, role, status, assigned_empresas FROM users ORDER BY name", conn)
    df_emp_all = pd.read_sql_query("SELECT id, nombre, nit FROM empresas ORDER BY nombre", conn)
    conn.close()

    mapa_emp = {r['id']: f"{r['nombre']} (NIT: {r['nit']})" for _, r in df_emp_all.iterrows()}

    def nombres_empresas_usuario(asig, rol):
        a = str(asig or "").strip()
        if str(rol) == "admin" or a == "all":
            return "Todas las empresas"
        ids_u = [x.strip() for x in a.split(",") if x.strip()]
        return ", ".join(mapa_emp.get(i, f"(empresa eliminada: {i})") for i in ids_u) or "— Sin empresas asignadas —"

    if not df_users.empty:
        df_users_vista = df_users.copy()
        df_users_vista["Empresas con acceso"] = df_users_vista.apply(lambda r: nombres_empresas_usuario(r['assigned_empresas'], r['role']), axis=1)
        df_users_vista = df_users_vista.drop(columns=["id", "assigned_empresas"]).rename(columns={
            'username': 'Usuario', 'name': 'Nombre', 'email': 'Correo', 'role': 'Rol', 'status': 'Estado'
        })
        st.dataframe(df_users_vista, use_container_width=True)
    else:
        st.info("No hay usuarios registrados.")

    t_edit_u, t_new_u = st.tabs(["✏️ Editar Usuario / Accesos a Empresas", "➕ Crear Usuario"])

    # ---------- EDITAR ----------
    with t_edit_u:
        if df_users.empty:
            st.info("No hay usuarios para editar.")
        else:
            sel_uid = st.selectbox(
                "Selecciona el usuario a modificar:", df_users['id'].tolist(),
                format_func=lambda x: f"@{df_users[df_users['id']==x]['username'].values[0]} — {df_users[df_users['id']==x]['name'].values[0]}",
                key="usr_edit_sel"
            )
            ru = df_users[df_users['id'] == sel_uid].iloc[0]
            asig_actual = str(ru['assigned_empresas'] or "").strip()
            ids_actuales = [] if asig_actual == "all" else [x.strip() for x in asig_actual.split(",") if x.strip() and x.strip() in mapa_emp]

            roles_op = ["operador", "admin"]
            if ru['role'] and ru['role'] not in roles_op:
                roles_op.append(ru['role'])
            estados_op = ["active", "inactive"]
            if ru['status'] and ru['status'] not in estados_op:
                estados_op.append(ru['status'])

            with st.form(f"form_edit_user_{sel_uid}"):
                e1, e2 = st.columns(2)
                e_name = e1.text_input("Nombre completo *", value=str(ru['name'] or ""), key=f"eu_name_{sel_uid}")
                e_email = e2.text_input("Correo", value=str(ru['email'] or ""), key=f"eu_email_{sel_uid}")
                e3, e4 = st.columns(2)
                e_role = e3.selectbox("Rol:", roles_op, index=roles_op.index(ru['role']) if ru['role'] in roles_op else 0, key=f"eu_role_{sel_uid}")
                e_status = e4.selectbox("Estado:", estados_op, index=estados_op.index(ru['status']) if ru['status'] in estados_op else 0, key=f"eu_status_{sel_uid}")

                st.markdown("**Acceso a empresas**")
                e_todas = st.checkbox("Acceso a TODAS las empresas", value=(asig_actual == "all"), key=f"eu_todas_{sel_uid}")
                e_emps = st.multiselect("O selecciona las empresas específicas:", list(mapa_emp.keys()), default=ids_actuales,
                                        format_func=lambda i: mapa_emp[i], key=f"eu_emps_{sel_uid}")
                st.caption("Los usuarios con rol admin ven todas las empresas. Los cambios aplican de inmediato, sin que el usuario tenga que volver a iniciar sesión.")

                e_pass = st.text_input("Nueva contraseña (dejar vacío para no cambiarla)", type="password", key=f"eu_pass_{sel_uid}")

                if st.form_submit_button("💾 Guardar Cambios del Usuario", type="primary"):
                    es_yo = str(ru['username']).lower() == str(curr_u['username']).lower()
                    if not e_name.strip():
                        st.error("El nombre es obligatorio.")
                    elif e_pass and len(e_pass) < 8:
                        st.error("La nueva contraseña debe tener al menos 8 caracteres.")
                    elif es_yo and (e_role != "admin" or e_status.lower() in STATUS_BLOQUEADOS):
                        st.error("No puedes quitarte tu propio rol de administrador ni desactivar tu propia cuenta.")
                    else:
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            # Evitar quedarse sin administradores activos
                            if ru['role'] == "admin" and (e_role != "admin" or e_status.lower() in STATUS_BLOQUEADOS):
                                c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id <> %s AND LOWER(COALESCE(status, 'active')) <> ALL(%s)",
                                          (sel_uid, list(STATUS_BLOQUEADOS)))
                                if c.fetchone()[0] == 0:
                                    conn.close()
                                    st.error("Debe quedar al menos un administrador activo en el sistema.")
                                    st.stop()
                            asig_nuevo = "all" if e_todas else ",".join(e_emps)
                            if e_pass:
                                c.execute("UPDATE users SET name = %s, email = %s, role = %s, status = %s, assigned_empresas = %s, password_hash = %s WHERE id = %s",
                                          (e_name.strip(), e_email.strip(), e_role, e_status, asig_nuevo, hash_password(e_pass), sel_uid))
                            else:
                                c.execute("UPDATE users SET name = %s, email = %s, role = %s, status = %s, assigned_empresas = %s WHERE id = %s",
                                          (e_name.strip(), e_email.strip(), e_role, e_status, asig_nuevo, sel_uid))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ Usuario @{ru['username']} actualizado.")
                            st.rerun()
                        except Exception as err:
                            try:
                                conn.rollback(); conn.close()
                            except Exception:
                                pass
                            st.error(f"❌ Error al actualizar el usuario: {err}")

    # ---------- CREAR ----------
    with t_new_u:
        with st.form("form_new_user"):
            n1, n2 = st.columns(2)
            n_user = n1.text_input("Usuario (sin espacios) *", key="nu_user")
            n_name = n2.text_input("Nombre completo *", key="nu_name")
            n3, n4 = st.columns(2)
            n_email = n3.text_input("Correo", key="nu_email")
            n_role = n4.selectbox("Rol:", ["operador", "admin"], key="nu_role")
            n5, n6 = st.columns(2)
            n_pass = n5.text_input("Contraseña (mín. 8 caracteres) *", type="password", key="nu_pass")
            n_pass2 = n6.text_input("Confirmar contraseña *", type="password", key="nu_pass2")
            n_todas = st.checkbox("Acceso a TODAS las empresas", value=False, key="nu_todas")
            n_emps = st.multiselect("O selecciona las empresas específicas:", list(mapa_emp.keys()), format_func=lambda i: mapa_emp[i], key="nu_emps")

            if st.form_submit_button("Crear Usuario", type="primary"):
                usuario_nuevo = n_user.strip()
                if not usuario_nuevo or not n_name.strip() or not n_pass:
                    st.error("Usuario, nombre y contraseña son obligatorios.")
                elif not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", usuario_nuevo):
                    st.error("El usuario debe tener de 3 a 30 caracteres: letras, números, punto, guion o guion bajo.")
                elif len(n_pass) < 8:
                    st.error("La contraseña debe tener al menos 8 caracteres.")
                elif n_pass != n_pass2:
                    st.error("Las contraseñas no coinciden.")
                elif not n_todas and not n_emps and n_role != "admin":
                    st.error("Asigna al menos una empresa o marca acceso a todas.")
                elif not df_users.empty and usuario_nuevo.lower() in [str(x).lower() for x in df_users['username'].tolist()]:
                    st.error("Ya existe un usuario con ese nombre.")
                else:
                    try:
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("""
                            INSERT INTO users (id, username, password_hash, name, email, role, status, assigned_empresas, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                        """, (f"usr-{uuid.uuid4().hex[:12]}", usuario_nuevo, hash_password(n_pass), n_name.strip(), n_email.strip(),
                              n_role, "all" if n_todas else ",".join(n_emps), datetime.now().isoformat()))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ Usuario @{usuario_nuevo} creado.")
                        st.rerun()
                    except Exception as err:
                        try:
                            conn.rollback(); conn.close()
                        except Exception:
                            pass
                        st.error(f"❌ Error al crear el usuario: {err}")

# ==========================================
# 19. GESTIÓN DE EMPRESAS (ADMIN)
# ==========================================
elif selected_menu == "🏢 Gestión de Empresas (Admin)":
    if not is_admin: st.error("Acceso restringido."); st.stop()
    st.title("🏢 Gestión de Empresas")
    conn = get_db_connection()
    df_e = pd.read_sql_query("SELECT id, nombre, nit, regimen_isr FROM empresas ORDER BY nombre", conn)
    if not df_e.empty:
        df_e = df_e.rename(columns={
            'nombre': 'Razón Social',
            'nit': 'NIT',
            'regimen_isr': 'Régimen ISR'
        })
    conn.close()

    if not df_e.empty:
        st.dataframe(df_e, use_container_width=True)

    with st.expander("➕ Registrar Nueva Empresa"):
        with st.form("form_reg_empresa"):
            c1, c2, c3 = st.columns(3)
            nom_emp = c1.text_input("Nombre o Razón Social *")
            nit_emp = c2.text_input("NIT *")
            reg_emp = c3.selectbox("Régimen Fiscal:", ["Pequeño Contribuyente (5%)", "Opcional Simplificado (5% / 7%)", "General sobre Utilidades (25%)"])
            if st.form_submit_button("Guardar Empresa", type="primary"):
                if nom_emp.strip() and nit_emp.strip():
                    conn = get_db_connection(); c = conn.cursor()
                    emp_uid = f"emp-{int(datetime.now().timestamp())}"
                    c.execute("""
                        INSERT INTO empresas (id, nombre, nit, direccion, telefono, regimen_isr, actividad_economica, created_at)
                        VALUES (%s, %s, %s, 'Guatemala', '', %s, 'General', %s)
                    """, (emp_uid, nom_emp.strip(), nit_emp.strip().upper(), reg_emp, datetime.now().isoformat()))
                    conn.commit(); conn.close()
                    sembrar_nomenclatura_inicial(emp_uid)
                    st.success("Empresa creada y configurada exitosamente."); st.rerun()
