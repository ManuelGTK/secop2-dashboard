# -*- coding: utf-8 -*-
"""
Dashboard SECOP II — búsqueda en vivo por palabra clave
=========================================================

Consulta en tiempo real la API de Datos Abiertos de Colombia (Socrata)
sobre el dataset "SECOP II - Procesos de Contratación" (p6dx-8zbt).

Tiene dos modos (dos pestañas):
  1. Búsqueda individual — una palabra clave, un rango de fechas.
  2. Reporte masivo — subes un Excel con muchas palabras clave y un rango
     de fechas, y te arma un Excel consolidado con todos los procesos que
     coincidan (deduplicados) más un resumen por palabra clave.

Cómo ejecutar:
    pip install -r requirements.txt
    streamlit run app.py

Este script hace llamadas HTTP reales cada vez que buscas, así que
necesitas conexión a internet. No guarda ni envía tus datos a nadie más
que a datos.gov.co.
"""

import datetime as dt
import io
import re

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

COLUMNAS_MOSTRAR = {
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
}

COLUMN_CONFIG_TABLA = {
    "Valor base (COP)": st.column_config.NumberColumn(format="$%,.0f"),
    "Valor adjudicado (COP)": st.column_config.NumberColumn(format="$%,.0f"),
    "Fecha publicación": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
    "Fecha adjudicación": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
    "Enlace SECOP": st.column_config.LinkColumn(display_text="Ver proceso"),
}

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

MAX_FILAS_DURO = 5000  # límite superior de seguridad para no saturar el navegador (búsqueda individual)
MAX_CANDIDATOS_DURO = 5000  # límite superior por palabra clave en el reporte masivo


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
# Reporte masivo: lectura de palabras clave y filtro de palabra completa
# ---------------------------------------------------------------------------


def leer_palabras_clave(archivo) -> list[str]:
    """Lee el Excel subido y devuelve la lista de palabras clave únicas
    (sin distinguir mayúsculas/minúsculas), en el orden en que aparecen."""
    df_kw = pd.read_excel(archivo)

    columna = None
    for c in df_kw.columns:
        if str(c).strip().lower() in ("palabra clave", "palabras clave", "keyword", "keywords"):
            columna = c
            break
    if columna is None:
        columna = df_kw.columns[0]

    valores = df_kw[columna].dropna().astype(str).str.strip()
    valores = [v for v in valores if v]

    vistos = set()
    unicas = []
    for v in valores:
        clave = v.lower()
        if clave not in vistos:
            vistos.add(clave)
            unicas.append(v)
    return unicas


def _patron_palabra_completa(kw: str) -> re.Pattern:
    """Coincidencia de PALABRA COMPLETA (no como pedazo de otra palabra).
    Ej.: 'SOC' coincide con '... el SOC detectó ...' pero NO con 'El Socorro'."""
    return re.compile(r"\b" + re.escape(kw.strip()) + r"\b", re.IGNORECASE)


def filtrar_coincidencias_reales(df: pd.DataFrame, kw: str) -> pd.DataFrame:
    """A partir de los candidatos que trajo $q (búsqueda difusa), se queda
    solo con los que de verdad contienen la palabra clave completa en el
    nombre, la descripción o la entidad."""
    if df.empty:
        return df
    patron = _patron_palabra_completa(kw)
    texto = (
        df["nombre_del_procedimiento"].fillna("") + " "
        + df["descripci_n_del_procedimiento"].fillna("") + " "
        + df["entidad"].fillna("")
    )
    mask = texto.apply(lambda t: bool(patron.search(t)))
    return df[mask]


# ---------------------------------------------------------------------------
# Excel: formato compartido
# ---------------------------------------------------------------------------


def formatear_hoja_excel(ws, df: pd.DataFrame) -> None:
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    formato_moneda = '$#,##0'
    formato_fecha = "yyyy-mm-dd"
    for idx, nombre_col in enumerate(df.columns, start=1):
        letra = ws.cell(row=1, column=idx).column_letter
        if "Valor" in nombre_col:
            for celda in ws[letra][1:]:
                celda.number_format = formato_moneda
        if "Fecha" in nombre_col:
            for celda in ws[letra][1:]:
                celda.number_format = formato_fecha
        largo = max(
            [len(str(nombre_col))]
            + [len(str(v)) for v in df[nombre_col].astype(str).head(200)]
        )
        ws.column_dimensions[letra].width = min(max(largo + 2, 10), 60)


def df_a_tabla_mostrable(df: pd.DataFrame, columnas_extra: dict | None = None) -> pd.DataFrame:
    cols = list(COLUMNAS_MOSTRAR.keys())
    nombres = dict(COLUMNAS_MOSTRAR)
    if columnas_extra:
        for col, nombre in columnas_extra.items():
            cols.append(col)
            nombres[col] = nombre
    return df[cols].rename(columns=nombres)


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

hoy = dt.date.today()
hace_un_anio = hoy - dt.timedelta(days=365)

with st.sidebar:
    st.header("Filtros — búsqueda individual")
    st.caption("Esta sección aplica solo a la pestaña **Búsqueda individual**.")

    keyword = st.text_input(
        "Palabra clave",
        placeholder="ej. vías, alimentación escolar, software…",
        help="Busca en el nombre, la descripción y la entidad del proceso.",
    )

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

    st.divider()
    st.subheader("Configuración común")
    # Si la app está desplegada (ej. Streamlit Community Cloud) y alguien
    # configuró un "Secret" llamado SECOP_APP_TOKEN, se usa automáticamente
    # y no se le pide nada al usuario final. En una corrida local sin ese
    # secreto, se muestra el campo de siempre como respaldo. Aplica a las
    # dos pestañas.
    app_token = st.secrets.get("SECOP_APP_TOKEN", "") if hasattr(st, "secrets") else ""
    if not app_token:
        app_token = st.text_input(
            "App Token de datos.gov.co (opcional)",
            type="password",
            help="Sin token las consultas funcionan pero con más límite de velocidad. "
                 "Puedes crear uno gratis en tu perfil de datos.gov.co → Developer Settings. "
                 "Se usa en las dos pestañas.",
        )

    buscar = st.button("Buscar", type="primary", use_container_width=True)

tab_individual, tab_masivo = st.tabs(["🔍 Búsqueda individual", "📋 Reporte masivo por palabras clave"])

# ---------------------------------------------------------------------------
# Pestaña 1: búsqueda individual
# ---------------------------------------------------------------------------

with tab_individual:
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

    elif df.empty:
        st.warning("No se encontraron procesos con esos filtros. Prueba con otra palabra clave o un rango de fechas más amplio.")

    else:
        if total and total > len(df):
            st.warning(
                f"Tu búsqueda encontró **{total:,}** procesos, pero solo se descargaron "
                f"**{len(df):,}** (el máximo configurado). Acota más la palabra clave, "
                f"el rango de fechas o sube el límite en 'Avanzado' para ver más."
            )

        # --- KPIs ---
        valor_total = df["precio_base"].sum(skipna=True)
        valor_promedio = df["precio_base"].mean(skipna=True)
        entidades_unicas = df["entidad"].nunique()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Procesos encontrados", f"{total:,}" if total else f"{len(df):,}")
        c2.metric("Valor base total", f"${valor_total:,.0f}")
        c3.metric("Valor base promedio", f"${valor_promedio:,.0f}")
        c4.metric("Entidades distintas", f"{entidades_unicas:,}")

        st.divider()

        # --- Gráficos ---
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

        # --- Tabla ---
        st.subheader("Resultados")

        estados_disponibles = sorted(df["estado_del_procedimiento"].dropna().unique().tolist())
        estado_filtro = st.multiselect("Filtrar por estado del proceso", estados_disponibles, key="estado_filtro_individual")

        df_tabla = df.copy()
        if estado_filtro:
            df_tabla = df_tabla[df_tabla["estado_del_procedimiento"].isin(estado_filtro)]

        df_mostrar = df_a_tabla_mostrable(df_tabla)

        st.caption(
            "Las columnas de adjudicación (fecha, valor y proveedor) solo tienen dato "
            "cuando el proceso ya fue adjudicado; para el resto quedan vacías."
        )

        st.dataframe(
            df_mostrar,
            use_container_width=True,
            hide_index=True,
            column_config=COLUMN_CONFIG_TABLA,
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
                use_container_width=True, key="csv_individual",
            )

        with col_xlsx:
            buffer_excel = io.BytesIO()
            with pd.ExcelWriter(buffer_excel, engine="openpyxl") as writer:
                hoja = "Procesos SECOP II"
                df_mostrar.to_excel(writer, index=False, sheet_name=hoja)
                formatear_hoja_excel(writer.sheets[hoja], df_mostrar)

            st.download_button(
                "⬇ Descargar Excel (.xlsx)", data=buffer_excel.getvalue(),
                file_name="secop2_procesos.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key="xlsx_individual",
            )

# ---------------------------------------------------------------------------
# Pestaña 2: reporte masivo por lista de palabras clave
# ---------------------------------------------------------------------------

with tab_masivo:
    st.subheader("Reporte masivo: todas tus palabras clave, un solo Excel")
    st.caption(
        "Sube un Excel con una columna de palabras clave (una por fila), escoge un "
        "rango de fechas, y la app busca cada palabra en SECOP II y te devuelve un "
        "Excel consolidado con los procesos únicos que coincidan de verdad — sin "
        "duplicados aunque un proceso coincida con varias palabras."
    )
    st.caption(
        "Cada palabra clave se busca de forma amplia en datos.gov.co y luego se "
        "verifica que aparezca como **palabra completa** (no como pedazo de otra "
        "palabra) en el nombre, la descripción o la entidad — así 'SOC' no coincide "
        "con 'El Socorro', por ejemplo."
    )

    archivo_kw = st.file_uploader(
        "Excel de palabras clave (.xlsx)", type=["xlsx"],
        help="Se usa la primera columna, o una columna llamada 'Palabra clave' si existe.",
    )

    colm1, colm2 = st.columns(2)
    with colm1:
        fecha_ini_masivo = st.date_input(
            "Desde", value=hace_un_anio, max_value=hoy, key="fecha_ini_masivo",
        )
    with colm2:
        fecha_fin_masivo = st.date_input(
            "Hasta", value=hoy, max_value=hoy, key="fecha_fin_masivo",
        )

    with st.expander("Avanzado"):
        cap_por_palabra = st.slider(
            "Máximo de candidatos a revisar por palabra clave",
            min_value=200, max_value=MAX_CANDIDATOS_DURO, value=1500, step=100,
            help="Por cada palabra clave se traen hasta este número de candidatos "
                 "(antes del filtro de palabra completa) para no disparar el tiempo "
                 "de espera con términos muy genéricos.",
        )

    st.caption(
        "⏱ Con varias decenas de palabras clave esto puede tardar varios minutos "
        "(se hace una consulta por palabra). La barra de progreso te muestra en qué va."
    )

    generar = st.button("Generar consolidado", type="primary", key="generar_masivo")

    if "bulk_df" not in st.session_state:
        st.session_state.bulk_df = None
        st.session_state.bulk_resumen = None
        st.session_state.bulk_rango = None

    if generar:
        if archivo_kw is None:
            st.error("Sube primero un Excel con tus palabras clave.")
        elif fecha_ini_masivo > fecha_fin_masivo:
            st.error("La fecha inicial debe ser anterior a la fecha final.")
        else:
            try:
                palabras = leer_palabras_clave(archivo_kw)
            except Exception as e:
                st.error(f"No pude leer el Excel: {e}")
                palabras = []

            if not palabras:
                st.error("No encontré palabras clave usables en ese archivo.")
            else:
                where_masivo = construir_where(fecha_ini_masivo, fecha_fin_masivo, None, None)
                procesos_por_id: dict[str, dict] = {}
                resumen_filas = []
                errores = []

                barra = st.progress(0.0)
                estado_txt = st.empty()

                for i, kw in enumerate(palabras, start=1):
                    estado_txt.write(f"Buscando {i}/{len(palabras)}: **{kw}**")
                    try:
                        candidatos = traer_resultados(where_masivo, kw, cap_por_palabra, app_token or None)
                    except requests.exceptions.RequestException as e:
                        errores.append(kw)
                        barra.progress(i / len(palabras))
                        continue

                    reales = filtrar_coincidencias_reales(candidatos, kw)

                    resumen_filas.append({
                        "Palabra clave": kw,
                        "Candidatos revisados": len(candidatos),
                        "Coincidencias reales": len(reales),
                        "¿Alcanzó el límite?": "Sí" if len(candidatos) >= cap_por_palabra else "No",
                    })

                    for _, fila in reales.iterrows():
                        pid = fila["id_del_proceso"]
                        if pid not in procesos_por_id:
                            registro = fila.to_dict()
                            registro["_palabras"] = set()
                            procesos_por_id[pid] = registro
                        procesos_por_id[pid]["_palabras"].add(kw)

                    barra.progress(i / len(palabras))

                barra.empty()
                estado_txt.empty()

                if errores:
                    muestra = ", ".join(errores[:8]) + ("…" if len(errores) > 8 else "")
                    st.warning(
                        f"{len(errores)} palabra(s) clave fallaron por error de conexión y "
                        f"se omitieron (puedes volver a intentar): {muestra}"
                    )

                resumen_df = pd.DataFrame(resumen_filas).sort_values(
                    "Coincidencias reales", ascending=False
                ).reset_index(drop=True)
                st.session_state.bulk_resumen = resumen_df
                st.session_state.bulk_rango = (fecha_ini_masivo, fecha_fin_masivo)

                if not procesos_por_id:
                    st.session_state.bulk_df = None
                    st.warning("No se encontró ninguna coincidencia real para esas palabras clave en ese rango de fechas.")
                else:
                    filas_finales = []
                    for registro in procesos_por_id.values():
                        registro["palabras_coincidentes"] = ", ".join(sorted(registro.pop("_palabras")))
                        filas_finales.append(registro)
                    st.session_state.bulk_df = pd.DataFrame(filas_finales)

    resumen_df = st.session_state.bulk_resumen
    df_consolidado = st.session_state.bulk_df

    if resumen_df is not None:
        st.divider()
        con_coincidencias = int((resumen_df["Coincidencias reales"] > 0).sum())
        st.caption(
            f"{con_coincidencias} de {len(resumen_df)} palabras clave tuvieron al menos "
            f"una coincidencia real."
        )
        with st.expander("Ver resumen por palabra clave"):
            st.dataframe(resumen_df, use_container_width=True, hide_index=True)

    if df_consolidado is not None and not df_consolidado.empty:
        st.divider()
        rango_txt = ""
        if st.session_state.bulk_rango:
            rango_txt = f" ({st.session_state.bulk_rango[0]} → {st.session_state.bulk_rango[1]})"
        st.success(f"**{len(df_consolidado):,}** procesos únicos encontrados{rango_txt}.")

        valor_total_m = df_consolidado["precio_base"].sum(skipna=True)
        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("Procesos únicos", f"{len(df_consolidado):,}")
        cm2.metric("Valor base total", f"${valor_total_m:,.0f}")
        cm3.metric("Entidades distintas", f"{df_consolidado['entidad'].nunique():,}")

        df_mostrar_m = df_a_tabla_mostrable(
            df_consolidado, {"palabras_coincidentes": "Palabras clave coincidentes"}
        )

        st.dataframe(
            df_mostrar_m,
            use_container_width=True,
            hide_index=True,
            column_config=COLUMN_CONFIG_TABLA,
        )

        buffer_masivo = io.BytesIO()
        with pd.ExcelWriter(buffer_masivo, engine="openpyxl") as writer:
            df_mostrar_m.to_excel(writer, index=False, sheet_name="Procesos")
            formatear_hoja_excel(writer.sheets["Procesos"], df_mostrar_m)

            if resumen_df is not None:
                resumen_df.to_excel(writer, index=False, sheet_name="Resumen por palabra clave")
                formatear_hoja_excel(writer.sheets["Resumen por palabra clave"], resumen_df)

        nombre_archivo = "secop2_consolidado.xlsx"
        if st.session_state.bulk_rango:
            nombre_archivo = (
                f"secop2_consolidado_{st.session_state.bulk_rango[0]}_"
                f"{st.session_state.bulk_rango[1]}.xlsx"
            )

        st.download_button(
            "⬇ Descargar Excel consolidado (.xlsx)", data=buffer_masivo.getvalue(),
            file_name=nombre_archivo,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, key="xlsx_masivo",
        )