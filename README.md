# Dashboard SECOP II — búsqueda por palabra clave

Esta es una aplicación que corre **en tu computador** (no en la nube) y consulta
en vivo el dataset abierto *"SECOP II - Procesos de Contratación"* de
[datos.gov.co](https://www.datos.gov.co) cada vez que buscas. Por eso puedes
escribir cualquier palabra clave y cualquier rango de fechas: no depende de un
snapshot fijo.

## ¿Por qué una app local y no un link?

Un dashboard publicado como página web (artifact) no puede hacer, por
seguridad del navegador, llamadas en vivo a datos.gov.co con una palabra
clave que tú escribas en el momento. Para que la búsqueda sea realmente libre
y en tiempo real, la consulta tiene que salir desde tu propio computador —
por eso te entrego una app que ejecutas tú mismo.

## Instalación (una sola vez)

Necesitas Python 3.9 o superior instalado. Si no lo tienes, descárgalo de
[python.org](https://www.python.org/downloads/).

Abre una terminal en la carpeta donde guardaste estos archivos y ejecuta:

```bash
pip install -r requirements.txt
```

(En Windows, si `pip` no funciona, prueba `python -m pip install -r requirements.txt`.)

## Ejecutar el dashboard

```bash
streamlit run app.py
```

Se abrirá automáticamente en tu navegador en `http://localhost:8501`.
Para detenerlo, vuelve a la terminal y presiona `Ctrl + C`.

## Cómo usarlo

1. En la barra lateral, escribe una **palabra clave** (por ejemplo "vías",
   "alimentación escolar", "dotación hospitalaria", el nombre de una
   entidad, etc.). Si la dejas vacía, trae todos los procesos del rango de
   fechas.
2. Elige el **rango de fechas** de publicación.
3. (Opcional) Filtra por **valor base** del proceso.
4. Presiona **Buscar**.
5. Verás tarjetas con totales, dos gráficos (procesos por mes y top
   entidades por valor) y una tabla con entidad, estado, valor, fechas,
   descripción y enlace directo al proceso en SECOP II.
6. Puedes descargar los resultados en CSV con el botón al final de la tabla.

### Límite de resultados

Cada búsqueda tiene un tope configurable (por defecto 2.000 filas) para no
saturar el navegador — SECOP II publica más de 100.000 procesos al mes en
todo el país, así que una palabra clave muy genérica en un rango de fechas
muy amplio puede tener más resultados de los que se descargan. Si ves el
aviso de "se encontraron más de los que se descargaron", acota la búsqueda
(palabra clave más específica o rango de fechas más corto) o sube el límite
en la sección "Avanzado".

### App Token (opcional pero recomendado)

Sin token las consultas funcionan, pero datos.gov.co limita la velocidad de
peticiones sin identificar. Si vas a usar el dashboard seguido, crea un
token gratis:

1. Crea una cuenta en [datos.gov.co](https://www.datos.gov.co).
2. Ve a tu perfil → **Developer Settings** (o entra directo en
   [dev.socrata.com](https://dev.socrata.com) con la misma cuenta).
3. Genera un **App Token** y pégalo en el campo correspondiente de la barra
   lateral.

## Notas sobre los datos

- La fuente es el dataset público `p6dx-8zbt` en datos.gov.co, mantenido por
  la Agencia Nacional de Contratación Pública – Colombia Compra Eficiente.
- Los datos se publican de forma periódica, no segundo a segundo: puede haber
  un pequeño rezago entre lo que ves en SECOP II directamente y lo que
  aparece aquí.
- "Valor base" corresponde al campo `precio_base` del proceso (el presupuesto
  estimado), no necesariamente al valor final adjudicado.
