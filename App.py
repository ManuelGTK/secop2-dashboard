# -*- coding: utf-8 -*-
"""
Dashboard SECOP II — búsqueda en vivo por palabra clave
=========================================================

Consulta en tiempo real la API de Datos Abiertos de Colombia (Socrata)
sobre el dataset "SECOP II - Procesos de Contratación" (p6dx-8zbt).

Cómo ejecutar:
    pip install -r requirements.txt
    streamlit run app.py

Este script hace llamadas HTTP reales cada vez que buscas, así que
necesitas conexión a internet. No guarda ni envía tus datos a nadie más
que a datos.gov.co.
"""

import datetime as dt
import io

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT_SEGUNDOS = 45


def _sesion_http() -> requests.Session:
    """Sesión con reintentos automáticos ante timeouts/errores 5xx transitorios."""
    s = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


_HTTP = _sesion_http()

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------

DATASET_ID = "p6dx-8zbt"  # SECOP II - Procesos de Contratación
BASE_URL = f"https://www.datos.gov.co/resource/{DATASET_ID}.json"

CAMPOS = [
    "id_del_proceso",
    "entidad",
    "nit_entidad",
    "departamento_entidad",
    "ciudad_entidad",
    "nombre_del_procedimiento",
    "descripci_n_del_procedimiento",
    "fecha_de_publicacion_del",
    "precio_base",
    "adjudicado",
    "fecha_adjudicacion",
    "valor_total_adjudicacion",
    "nombre_del_proveedor",
    "modalidad_de_contratacion",
    "estado_del_procedimiento",
    "estado_resumen",
    "tipo_de_contrato",
    "urlproceso",
]

# Paleta validada (skill dataviz) — orden categórico fijo, nunca ciclado
COLOR_SERIE_1 = "#2a78d6"  # azul
COLOR_SERIE_2 = "#eb6834"  # naranja
COLOR_SERIE_3 = "#1baf7a"  # aqua
COLOR_SERIE_4 = "#eda100"  # amarillo
CATEGORICAL = [COLOR_SERIE_1, COLOR_SERIE_2, COLOR_SERIE_3, COLOR_SERIE_4]
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"

MAX_FILAS_DURO = 5000  # límite superior de seguridad para no saturar el navegador


# ---------------------------------------------------------------------------
# Funciones de consulta a la API (SoQL / Socrata)
# ---------------------------------------------------------------------------


def construir_where(fecha_ini: dt.date, fecha_fin: dt.date,
                     valor_min: float | None, valor_max: float | None) -> str:
    """Solo fecha y valor van en $where. La palabra clave va aparte, en $q
    (búsqueda de texto completo indexada de Socrata) — es más rápida y evita
    los problemas de codificación de un LIKE con comodines '%'."""
    partes = [
        f"fecha_de_publicacion_del between '{fecha_ini.isoformat()}T00:00:00.000' "
        f"and '{fecha_fin.isoformat()}T23:59:59.000'"
    ]

    if valor_min is not None:
        partes.append(f"precio_base >= {valor_min}")
    if valor_max is not None:
        partes.append(f"precio_base <= {valor_max}")

    return " AND ".join(partes)


def contar_resultados(where: str, keyword: str | None, app_token: str | None) -> int:
    params = {"$select": "count(*)", "$where": where}
    if keyword and keyword.strip():
        params["$q"] = keyword.strip()
    headers = {"X-App-Token": app_token} if app_token else {}
    r = _HTTP.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT_SEGUNDOS)
    r.raise_for_status()
    data = r.json()
    if not data:
        return 0
    return int(data[0].get("count", 0))


def traer_resultados(where: str, keyword: str | None, limite: int,
                      app_token: str | None) -> pd.DataFrame:
    headers = {"X-App-Token": app_token} if app_token else {}
    filas = []
    offset = 0
    paso = 1000
    while offset < limite:
        lote = min(paso, limite - offset)
        params = {
            "$select": ",".join(CAMPOS),
            "$where": where,
            "$order": "fecha_de_publicacion_del DESC",
            "$limit": lote,
            "$offset": offset,
        }
        if keyword and keyword.strip():
            params["$q"] = keyword.strip()
        r = _HTTP.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT_SEGUNDOS)
        r.raise_for_status()
        datos = r.json()
        if not datos:
            break
        filas.extend(datos)
        if len(datos) < lote:
            break
        offset += lote

    if not filas:
        return pd.DataFrame(columns=CAMPOS)

    df = pd.DataFrame(filas)

    # Tipado y limpieza
    for col in ["precio_base", "valor_total_adjudicacion"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "fecha_de_publicacion_del" in df.columns:
        df["fecha_de_publicacion_del"] = pd.to_datetime(
            df["fecha_de_publicacion_del"], errors="coerce"
        )
    if "fecha_adjudicacion" in df.columns:
        # Solo tiene valor cuando el proceso ya fue adjudicado; queda NaT si no.
        df["fecha_adjudicacion"] = pd.to_datetime(
            df["fecha_adjudicacion"], errors="coerce"
        )
    if "urlproceso" in df.columns:
        df["url"] = df["urlproceso"].apply(
            lambda x: x.get("url") if isinstance(x, dict) else None
        )

    return df


# ---------------------------------------------------------------------------
# Interfaz Streamlit
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SECOP II · Búsqueda de procesos",
    page_icon="🔎",
    layout="wide",
)

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #f9f9f7; }}
    h1, h2, h3 {{ color: {INK_PRIMARY}; }}
    .stMetric label {{ color: {INK_SECONDARY} !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🔎 SECOP II — Buscador de procesos de contratación")
st.caption(
    "Consulta en vivo el dataset abierto de Colombia Compra Eficiente "
    "(*SECOP II - Procesos de Contratación*, datos.gov.co). Los resultados "
    "reflejan la última publicación disponible en el portal, no en tiempo real "
    "segundo a segundo."
)

with st.sidebar:
    st.header("Filtros")

    keyword = st.text_input(
        "Palabra clave",
        placeholder="ej. vías, alimentación escolar, software…",
        help="Busca en el nombre, la descripción y la entidad del proceso.",
    )

    hoy = dt.date.today()
    hace_un_anio = hoy - dt.timedelta(days=365)
    rango = st.date_input(
        "Rango de fecha de publicación",
        value=(hace_un_anio, hoy),
        max_value=hoy,
    )
    if isinstance(rango, tuple) and len(rango) == 2:
        fecha_ini, fecha_fin = rango
    else:
        fecha_ini, fecha_fin = hace_un_anio, hoy

    st.subheader("Valor del proceso (opcional)")
    usar_valor = st.checkbox("Filtrar por valor base (COP)")
    valor_min = valor_max = None
    if usar_valor:
        col1, col2 = st.columns(2)
        with col1:
            valor_min = st.number_input("Mínimo", min_value=0, value=0, step=1_000_000)
        with col2:
            valor_max = st.number_input("Máximo", min_value=0, value=0, step=1_000_000)
        if valor_max == 0:
            valor_max = None
        if valor_min == 0:
            valor_min = None

    st.subheader("Avanzado")
    limite_filas = st.slider(
        "Máximo de resultados a traer", min_value=200, max_value=MAX_FILAS_DURO,
        value=2000, step=200,
        help="Limita cuántas filas se descargan para no saturar el navegador. "
             "Si tu búsqueda tiene más resultados que este límite, verás un aviso.",
    )
    # Si la app está desplegada (ej. Streamlit Community Cloud) y alguien
    # configuró un "Secret" llamado SECOP_APP_TOKEN, se usa automáticamente
    # y no se le pide nada al usuario final. En una corrida local sin ese
    # secreto, se muestra el campo de siempre como respaldo.
    app_token = st.secrets.get("SECOP_APP_TOKEN", "") if hasattr(st, "secrets") else ""
    if not app_token:
        app_token = st.text_input(
            "App Token de datos.gov.co (opcional)",
            type="password",
            help="Sin token las consultas funcionan pero con más límite de velocidad. "
                 "Puedes crear uno gratis en tu perfil de datos.gov.co → Developer Settings.",
        )

    buscar = st.button("Buscar", type="primary", use_container_width=True)

if "df" not in st.session_state:
    st.session_state.df = None
    st.session_state.total = None

if buscar:
    if fecha_ini > fecha_fin:
        st.error("La fecha inicial debe ser anterior a la fecha final.")
    else:
        where = construir_where(fecha_ini, fecha_fin, valor_min, valor_max)
        with st.spinner("Consultando datos.gov.co… (puede tardar unos segundos en búsquedas amplias)"):
            try:
                total = contar_resultados(where, keyword, app_token or None)
                df = traer_resultados(
                    where, keyword, min(limite_filas, total or limite_filas), app_token or None
                )
                st.session_state.df = df
                st.session_state.total = total
            except requests.exceptions.Timeout:
                st.error(
                    "datos.gov.co tardó demasiado en responder. Esto pasa sobre todo "
                    "con rangos de fecha muy amplios sin palabra clave. Prueba con un "
                    "rango más corto, una palabra clave más específica, o intenta de "
                    "nuevo en un momento (el servidor puede estar congestionado)."
                )
                st.session_state.df = None
                st.session_state.total = None
            except requests.exceptions.RequestException as e:
                st.error(f"No se pudo conectar con la API de datos.gov.co: {e}")
                st.session_state.df = None
                st.session_state.total = None

df = st.session_state.df
total = st.session_state.total

if df is None:
    st.info("Configura tus filtros en la barra izquierda y presiona **Buscar** para comenzar.")
    st.stop()

if total and total > len(df):
    st.warning(
        f"Tu búsqueda encontró **{total:,}** procesos, pero solo se descargaron "
        f"**{len(df):,}** (el máximo configurado). Acota más la palabra clave, "
        f"el rango de fechas o sube el límite en 'Avanzado' para ver más."
    )

if df.empty:
    st.warning("No se encontraron procesos con esos filtros. Prueba con otra palabra clave o un rango de fechas más amplio.")
    st.stop()

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

valor_total = df["precio_base"].sum(skipna=True)
valor_promedio = df["precio_base"].mean(skipna=True)
entidades_unicas = df["entidad"].nunique()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Procesos encontrados", f"{total:,}" if total else f"{len(df):,}")
c2.metric("Valor base total", f"${valor_total:,.0f}")
c3.metric("Valor base promedio", f"${valor_promedio:,.0f}")
c4.metric("Entidades distintas", f"{entidades_unicas:,}")

st.divider()

# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

g1, g2 = st.columns(2)

with g1:
    st.subheader("Procesos por mes")
    por_mes = (
        df.dropna(subset=["fecha_de_publicacion_del"])
        .assign(mes=lambda d: d["fecha_de_publicacion_del"].dt.to_period("M").dt.to_timestamp())
        .groupby("mes")
        .size()
        .reset_index(name="procesos")
        .sort_values("mes")
    )
    fig1 = px.bar(por_mes, x="mes", y="procesos")
    fig1.update_traces(marker_color=COLOR_SERIE_1, marker_line_width=0)
    fig1.update_layout(
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb",
        font_color=INK_SECONDARY,
        xaxis_title=None, yaxis_title="Procesos",
        yaxis_gridcolor=GRIDLINE, xaxis_gridcolor=GRIDLINE,
        margin=dict(t=10, l=10, r=10, b=10),
    )
    st.plotly_chart(fig1, use_container_width=True)

with g2:
    st.subheader("Top 8 entidades por valor base")
    top_ent = (
        df.groupby("entidad")["precio_base"]
        .sum()
        .sort_values(ascending=False)
        .head(8)
        .reset_index()
    )
    fig2 = px.bar(top_ent, x="precio_base", y="entidad", orientation="h")
    fig2.update_traces(marker_color=COLOR_SERIE_1, marker_line_width=0)
    fig2.update_layout(
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb",
        font_color=INK_SECONDARY,
        xaxis_title="Valor base (COP)", yaxis_title=None,
        yaxis=dict(autorange="reversed"),
        xaxis_gridcolor=GRIDLINE, yaxis_gridcolor=GRIDLINE,
        margin=dict(t=10, l=10, r=10, b=10),
    )
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Tabla de resultados
# ---------------------------------------------------------------------------

st.subheader("Resultados")

estados_disponibles = sorted(df["estado_del_procedimiento"].dropna().unique().tolist())
estado_filtro = st.multiselect("Filtrar por estado del proceso", estados_disponibles)

df_tabla = df.copy()
if estado_filtro:
    df_tabla = df_tabla[df_tabla["estado_del_procedimiento"].isin(estado_filtro)]

df_mostrar = df_tabla[[
    "entidad", "nit_entidad", "estado_del_procedimiento",
    "modalidad_de_contratacion", "fecha_de_publicacion_del", "precio_base",
    "fecha_adjudicacion", "valor_total_adjudicacion", "nombre_del_proveedor",
    "nombre_del_procedimiento", "descripci_n_del_procedimiento", "url",
]].rename(columns={
    "entidad": "Entidad",
    "nit_entidad": "NIT Entidad",
    "estado_del_procedimiento": "Estado",
    "modalidad_de_contratacion": "Modalidad",
    "fecha_de_publicacion_del": "Fecha publicación",
    "precio_base": "Valor base (COP)",
    "fecha_adjudicacion": "Fecha adjudicación",
    "valor_total_adjudicacion": "Valor adjudicado (COP)",
    "nombre_del_proveedor": "Proveedor adjudicado",
    "nombre_del_procedimiento": "Nombre",
    "descripci_n_del_procedimiento": "Descripción",
    "url": "Enlace SECOP",
})

st.caption(
    "Las columnas de adjudicación (fecha, valor y proveedor) solo tienen dato "
    "cuando el proceso ya fue adjudicado; para el resto quedan vacías."
)

st.dataframe(
    df_mostrar,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Valor base (COP)": st.column_config.NumberColumn(format="$%,.0f"),
        "Valor adjudicado (COP)": st.column_config.NumberColumn(format="$%,.0f"),
        "Fecha publicación": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        "Fecha adjudicación": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
        "Enlace SECOP": st.column_config.LinkColumn(display_text="Ver proceso"),
    },
)

st.caption(
    "El Excel evita cualquier problema de comas dentro del texto (descripciones, "
    "nombres de entidad, etc.) porque no depende de un separador; el CSV usa "
    "punto y coma (`;`) para que Excel en español lo abra bien en columnas."
)

col_csv, col_xlsx = st.columns(2)

with col_csv:
    csv = df_mostrar.to_csv(index=False, sep=";").encode("utf-8-sig")
    st.download_button(
        "⬇ Descargar CSV", data=csv,
        file_name="secop2_procesos.csv", mime="text/csv",
        use_container_width=True,
    )

with col_xlsx:
    buffer_excel = io.BytesIO()
    with pd.ExcelWriter(buffer_excel, engine="openpyxl") as writer:
        hoja = "Procesos SECOP II"
        df_mostrar.to_excel(writer, index=False, sheet_name=hoja)
        ws = writer.sheets[hoja]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        formato_moneda = '$#,##0'
        formato_fecha = "yyyy-mm-dd"
        for idx, nombre_col in enumerate(df_mostrar.columns, start=1):
            letra = ws.cell(row=1, column=idx).column_letter
            if "Valor" in nombre_col:
                for celda in ws[letra][1:]:
                    celda.number_format = formato_moneda
            if "Fecha" in nombre_col:
                for celda in ws[letra][1:]:
                    celda.number_format = formato_fecha
            largo = max(
                [len(str(nombre_col))]
                + [len(str(v)) for v in df_mostrar[nombre_col].astype(str).head(200)]
            )
            ws.column_dimensions[letra].width = min(max(largo + 2, 10), 60)

    st.download_button(
        "⬇ Descargar Excel (.xlsx)", data=buffer_excel.getvalue(),
        file_name="secop2_procesos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )