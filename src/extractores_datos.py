"""
extractores_datos.py — Extracción de texto para los formatos de B.

Formatos: json, xlsx, imagen (OCR), pbf (Mapbox Vector Tiles).

Cada extractor devuelve una tupla (texto, titulo, extra):
    texto  : str  — el contenido que se va a indexar
    titulo : str | None
    extra  : dict — metadata barata, lo que sale gratis del parseo

Ninguno lanza excepción por contenido vacío: eso lo decide ingest_data.py
al llamar a compute_doc_id().
"""

import csv
import json
from datetime import date, datetime
from pathlib import Path
import re

from mapeos_json import (
    ATRIBUTOS_PBF_DESCARTADOS,
    MAPEO_ALERTAS,
    ATRIBUTOS_PBF_PRIORITARIOS,
    ATRIBUTOS_PBF_SEMANTICOS,
    CAMPOS_RUIDO_CATALOGO,
    FAMILIA_ARTICULO_WEB,
    MAPEOS_POR_INSTITUCION,
    PATRONES_CATALOGO,
)


# ---------------------------------------------------------------------------
# Evaluador de rutas de campo
# ---------------------------------------------------------------------------

def extraer_ruta(obj, ruta: str) -> list[str]:
    """
    Evalúa una ruta de campo sobre un objeto JSON y devuelve la lista de
    strings encontrados.

    Soporta:
        "title"                    -> clave directa
        "body_paragraphs[]"        -> lista de strings
        "alerta_meta.tema_clave"   -> clave anidada
        "sections[].paragraphs[]"  -> anidamiento con listas

    Args:
        obj: el objeto JSON (o subobjeto) sobre el que evaluar.
        ruta: expresión de ruta. Cadena vacía significa "este nodo es la hoja".

    Returns:
        Lista de strings no vacíos. Lista vacía si la ruta no existe.
    """
    if obj is None:
        return []

    # Caso hoja: no queda ruta por recorrer.
    if not ruta:
        if isinstance(obj, str):
            return [obj] if obj.strip() else []
        if isinstance(obj, bool):
            return []  # los booleanos sueltos no aportan nada al texto
        if isinstance(obj, (int, float)):
            return [formatear_valor(obj)]
        if isinstance(obj, list):
            resultado = []
            for item in obj:
                resultado.extend(extraer_ruta(item, ""))
            return resultado
        return []

    partes = ruta.split(".", 1)
    segmento = partes[0]
    resto = partes[1] if len(partes) > 1 else ""

    es_lista = segmento.endswith("[]")
    clave = segmento[:-2] if es_lista else segmento

    if not isinstance(obj, dict):
        return []

    valor = obj.get(clave)
    if valor is None:
        return []

    if es_lista:
        if not isinstance(valor, list):
            valor = [valor]
        resultado = []
        for item in valor:
            resultado.extend(extraer_ruta(item, resto))
        return resultado

    return extraer_ruta(valor, resto)

_VINETA = re.compile(r"^\s*[-•*\u2022\u2013\u2014]\s+")


def aplanar_valor(texto: str) -> str:
    """
    Colapsa un valor multilínea en UNA sola línea.

    En datos de registro la unidad atómica de fragmentación es la línea: un
    valor con saltos internos parte el registro en trozos huérfanos que el
    chunker trataría como registros independientes, incumpliendo el §3.3.

    Distingue dos casos porque el separador correcto no es el mismo:
      - LISTA    (alguna línea empieza por viñeta)  -> se une con "; "
      - ENVUELTO (texto partido por ancho de celda) -> se une con " "

    Se usa SOLO en contextos de registro (xlsx, pbf, catálogos json).
    NUNCA sobre body_paragraphs: ahí los \\n\\n son estructura de párrafo.
    """
    if not texto:
        return ""
    lineas = [l for l in texto.splitlines() if l.strip()]
    if not lineas:
        return ""
    es_lista = any(_VINETA.match(l) for l in lineas)
    limpias = [x for x in (_VINETA.sub("", l).strip() for l in lineas) if x]
    plano = ("; " if es_lista else " ").join(limpias)
    plano = plano.replace("|", "/")
    return re.sub(r"[ \t\u00a0]+", " ", plano).strip()


def formatear_valor(v) -> str:
    """
    Convierte un valor a texto de forma limpia.

    Resuelve la trampa confirmada en el reconocimiento: openpyxl y json
    devuelven enteros como float (32634855.0 en vez de 32634855), y en
    texto indexado eso es peor que el entero.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    return str(v)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def es_catalogo(ruta: Path) -> bool:
    """Detecta catálogos de scraping por el nombre del archivo."""
    nombre = ruta.name.lower()
    return any(p in nombre for p in PATRONES_CATALOGO)


def extraer_catalogo(datos) -> str:
    """
    Extrae solo los campos semánticos de un catálogo de scraping.

    Descarta URLs, tamaños, timestamps, hashes y códigos internos: son
    ruido que compite por posiciones en el top-10 sin poder responder
    nunca a una pregunta en lenguaje natural.

    Usa LISTA NEGRA, no blanca: en caso de duda se indexa. Una lista blanca
    descartaba contenido real (p.ej. el campo "valor" de SWF_report-data.json).
    """
    registros = datos if isinstance(datos, list) else [datos]
    lineas = []

    for registro in registros:
        if not isinstance(registro, dict):
            continue
        partes = []
        for clave, valor in registro.items():
            clave_norm = clave.lower().strip()
            if clave_norm in CAMPOS_RUIDO_CATALOGO:
                continue
            textos = extraer_ruta({clave: valor}, clave)
            if textos:
                # aplanar_valor: un catálogo es dato de registro (una línea por
                # registro), así que ningún valor puede traer saltos internos.
                partes.append(f"{clave}: {aplanar_valor(' '.join(textos))}")
        if partes:
            lineas.append(" | ".join(partes))

    return "\n".join(lineas)


def componer_titulo_alerta(datos: dict) -> str | None:
    """
    Compone un título sintético para Alertas Tempranas.

    Los 363 archivos traen title="Mapa", que es inútil. Un título como
    "Alerta 001-17 — Cartagena de Indias (Bolívar)" es mucho más útil
    para el experimento de prefijo de título del Día 5.
    """
    meta = datos.get("alerta_meta") or {}
    codigo = meta.get("codigo")
    municipios = meta.get("municipios")
    if codigo and municipios:
        return f"Alerta {codigo} — {municipios}"
    if codigo:
        return f"Alerta {codigo}"
    return None


def extraer_json(ruta: Path, institucion: str) -> tuple[str, str | None, dict]:
    """
    Extrae texto de un archivo JSON según el mapeo de su institución.

    Args:
        ruta: ruta al archivo.
        institucion: clave de MAPEOS_POR_INSTITUCION.

    Returns:
        (texto, titulo, extra)

    Raises:
        ValueError: si la institución no tiene mapeo definido. Es
            intencional: adivinar campos produce documentos silenciosamente
            vacíos o llenos de URLs. Mejor fallar y añadir el mapeo.
    """
    contenido = ruta.read_text(encoding="utf-8", errors="replace")
    datos = json.loads(contenido)

    # Los catálogos se detectan por nombre de archivo, antes que por institución.
    if es_catalogo(ruta):
        return extraer_catalogo(datos), None, {}

    mapeo = MAPEOS_POR_INSTITUCION.get(institucion)
    if mapeo is None:
        raise ValueError(
            f"Institución sin mapeo definido: {institucion!r}. "
            f"Añadirla a MAPEOS_POR_INSTITUCION en mapeos_json.py."
        )

    if isinstance(datos, list):
        # Un JSON con raíz de lista que no es catálogo: se concatenan los
        # elementos. El spec §1.3 dice archivo = documento.
        objetos = [d for d in datos if isinstance(d, dict)]
    else:
        objetos = [datos]

    bloques = []
    for obj in objetos:
        for campo in mapeo["texto"]:
            textos = extraer_ruta(obj, campo)
            bloques.extend(textos)

    texto = "\n\n".join(b.strip() for b in bloques if b.strip())

    # Red de seguridad: si el mapeo de la institución no encontró nada, el
    # archivo puede tener una estructura distinta a la del resto de su fuente
    # (p.ej. SWF_report-data.json usa seccion/campo/valor y no title/body).
    # Antes de darlo por perdido, se intenta extracción genérica por lista
    # negra. Vale más indexar algo imperfecto que perder el documento: si el
    # ground truth lo referencia y no está indexado, es F1@3 perdido.
    if not texto.strip():
        texto = extraer_catalogo(datos)

    # Título
    titulo = None
    if mapeo is MAPEO_ALERTAS and objetos:
        titulo = componer_titulo_alerta(objetos[0])
    elif mapeo.get("titulo") and objetos:
        candidatos = extraer_ruta(objetos[0], mapeo["titulo"])
        titulo = candidatos[0] if candidatos else None

    # Extra: lo que sale gratis del parseo
    extra = {}
    if objetos:
        for campo in mapeo.get("extra", []):
            valores = extraer_ruta(objetos[0], campo)
            if valores:
                extra[campo.split(".")[-1]] = valores[0]

    return texto, titulo, extra


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def extraer_xlsx(ruta: Path) -> tuple[str, str | None, dict]:
    """
    Serializa un XLSX como "columna: valor | columna: valor", una fila por línea.

    El nombre de la hoja se incluye como contexto: es información semántica
    real (p.ej. "Private investment" vs "Publications by country").

    Nota: en read_only=True openpyxl no siempre calcula ws.max_row, así que
    se itera directamente en vez de confiar en las dimensiones.
    """
    import openpyxl

    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    bloques = []
    hojas_con_datos = []

    try:
        for nombre_hoja in wb.sheetnames:
            ws = wb[nombre_hoja]
            filas = ws.iter_rows(values_only=True)

            cabecera = None
            lineas_hoja = []

            for fila in filas:
                if fila is None:
                    continue
                celdas = [c for c in fila if c is not None]
                if not celdas:
                    continue

                if cabecera is None:
                    cabecera = [formatear_valor(c) if c is not None else ""
                                for c in fila]
                    continue

                partes = []
                for col, valor in zip(cabecera, fila):
                    if valor is None or not col:
                        continue
                    # Una celda puede traer saltos de línea (texto envuelto o
                    # listas). Sin aplanar, la fila se parte en varias líneas
                    # del JSONL y deja de ser un registro atómico.
                    texto_valor = aplanar_valor(formatear_valor(valor))
                    if texto_valor:
                        partes.append(f"{col}: {texto_valor}")
                if partes:
                    lineas_hoja.append(" | ".join(partes))

            if lineas_hoja:
                hojas_con_datos.append(nombre_hoja)
                bloques.append(f"hoja: {nombre_hoja}\n" + "\n".join(lineas_hoja))
    finally:
        wb.close()

    # Las hojas van también en `extra` para que el chunker pueda anteponerlas
    # como cabecera de CADA chunk. En el texto, "hoja: X" solo aparece en la
    # primera línea, así que sin esto únicamente el chunk c000 tendría contexto.
    # Se deja además en el texto por seguridad: si C no llega a implementar la
    # cabecera, el contexto no se pierde del todo.
    extra = {}
    if hojas_con_datos:
        extra["hojas"] = hojas_con_datos
        # Nombres genéricos de openpyxl: no aportan señal semántica y conviene
        # que C sepa cuáles NO merece la pena anteponer.
        utiles = [h for h in hojas_con_datos
                  if not re.fullmatch(r"(?i)(sheet|hoja|feuille|planilha)\s*\d*", h.strip())]
        extra["hojas_utiles"] = utiles

    return "\n\n".join(bloques), ruta.stem, extra


# ---------------------------------------------------------------------------
# Imágenes (OCR)
# ---------------------------------------------------------------------------

def extraer_imagen(ruta: Path, idiomas: str = "spa+eng+por") -> tuple[str, str | None, dict]:
    """
    Aplica OCR multilingüe a una imagen.

    Requiere el binario de Tesseract instalado en el sistema (no basta con
    pip install pytesseract) con los paquetes de idioma spa, eng y por.

    El filtrado de resultados pobres NO se hace aquí: lo decide ingest_data.py
    con text_is_usable(), para que el criterio quede en un solo sitio.
    """
    import pytesseract
    from PIL import Image

    # AVIF necesita un plugin externo; si no está, Pillow lanza
    # "Unsupported image format/type". Se intenta cargar en silencio.
    try:
        import pillow_avif  # noqa: F401
    except ImportError:
        pass

    with Image.open(ruta) as img:
        ancho, alto = img.size
        # pytesseract mantiene una LISTA BLANCA de formatos (BMP, GIF, JPEG,
        # JPEG2000, PBM, PGM, PNG, PPM, TIFF, WEBP) y AVIF no está en ella:
        # aunque Pillow abra el archivo con pillow_avif, prepare() lanza
        # TypeError('Unsupported image format/type') al ver image.format='AVIF'.
        # convert() devuelve una imagen con format=None, y pytesseract trata
        # ese caso como PNG. Además normaliza el modo (P, LA, CMYK) y descarta
        # el canal alfa, que es otra fuente de fallos.
        texto = pytesseract.image_to_string(img.convert("RGB"), lang=idiomas)

    extra = {"ancho_px": ancho, "alto_px": alto}
    return texto, None, extra


# ---------------------------------------------------------------------------
# PBF (Mapbox Vector Tiles)
# ---------------------------------------------------------------------------

def parsear_zoom_x_y(ruta_relativa: str) -> dict:
    """
    Extrae zoom/x/y de una ruta tipo "tiles/3/2/AMAZONUW_4.pbf".

    El nombre del archivo lleva el prefijo del observatorio, así que el
    'y' hay que sacarlo del nombre quitando ese prefijo.
    """
    partes = ruta_relativa.replace("\\", "/").split("/")
    if "tiles" not in partes:
        return {}

    idx = partes.index("tiles")
    siguientes = partes[idx + 1:]
    if len(siguientes) < 3:
        return {}

    zoom, x = siguientes[0], siguientes[1]
    nombre = Path(siguientes[2]).stem
    y = nombre.split("_")[-1]  # "AMAZONUW_4" -> "4"

    if zoom.isdigit() and x.isdigit() and y.isdigit():
        return {"zoom": int(zoom), "x": int(x), "y": int(y)}
    return {}


def extraer_pbf(
    ruta: Path,
    ruta_relativa: str,
    features_asignados: dict | None = None,
) -> tuple[str, str | None, dict]:
    """
    Decodifica un Mapbox Vector Tile y serializa los atributos de sus features.

    Se priorizan los campos au_popup_window_* : son texto ya redactado para
    lectura humana, mucho mejor que la serialización cruda de atributos.

    La geometría y los códigos internos se descartan: no aportan nada
    semánticamente a un encoder de texto.

    Deduplicación: si se pasa features_asignados ({fid: props}), solo se
    emiten esos features, y se usan LOS PROPS DEL DICCIONARIO, no los del
    tile. Así el feature se indexa en el tile más granular pero conserva
    la versión más rica de sus atributos (ver src/dedup_pbf.py). El corpus tiene 88% de redundancia entre
    niveles de zoom (11.906 features, 1.409 únicos); sin esto, una consulta
    recuperaría hasta 26 fragmentos idénticos que llenarían el top-10.
    El conjunto lo calcula src/dedup_pbf.py.
    """
    import mapbox_vector_tile

    from dedup_pbf import CLAVE_RESUMEN

    datos = ruta.read_bytes()
    tile = mapbox_vector_tile.decode(datos)

    bloques = []
    total_features = 0
    features_indexados = 0

    # Tile de zoom bajo cuyos features ya se indexan en tiles más granulares:
    # emite un resumen de cobertura en vez de quedarse vacío. Un documento sin
    # texto no es recuperable, y cada tile es un `fuente` del ground truth.
    if features_asignados and CLAVE_RESUMEN in features_asignados:
        total_features = sum(
            len(capa.get("features", [])) for capa in tile.values()
        )
        extra = parsear_zoom_x_y(ruta_relativa)
        extra["num_features_tile"] = total_features
        extra["num_features_indexados"] = 0
        extra["capas"] = list(tile.keys())
        extra["es_resumen"] = True
        return features_asignados[CLAVE_RESUMEN], None, extra

    for nombre_capa, capa in tile.items():
        features = capa.get("features", [])
        total_features += len(features)
        lineas = []

        for feature in features:
            props = feature.get("properties", {}) or {}

            # Deduplicación: este feature se indexa en otro tile.
            if features_asignados is not None:
                fid = props.get("fid")
                if fid is None or fid not in features_asignados:
                    continue
                # Usar la versión más rica encontrada en todo el dataset.
                props = features_asignados[fid]

            # 1. Texto ya redactado, si existe.
            # Los popups vienen redactados para lectura humana y suelen traer
            # listas con viñetas en varias líneas. Es el origen del 59.7% de
            # líneas huérfanas detectadas en el JSONL: hay que aplanarlos.
            popups = [
                aplanar_valor(formatear_valor(props[k]))
                for k in ATRIBUTOS_PBF_PRIORITARIOS
                if props.get(k) not in (None, "")
            ]
            popups = [p for p in popups if p]

            # Deduplicar: las variantes es/pt/en del popup suelen traer el
            # MISMO texto (los nombres de grupos armados son propios y no se
            # traducen). Sin esto se indexa triplicado — "Los Lobos Los Lobos
            # Los Lobos" — , que infla el chunk sin añadir señal.
            vistos = set()
            unicos = []
            for p in popups:
                if p not in vistos:
                    vistos.add(p)
                    unicos.append(p)

            # Prefijo "popup:" para que TODA línea de registro tenga la forma
            # "campo: valor". Sin él esta rama emitía líneas sin prefijo,
            # indistinguibles de un fragmento huérfano al validar.
            if unicos:
                lineas.append("popup: " + "; ".join(unicos))
                features_indexados += 1
                continue

            # 2. Si no hay popup, serializar atributos semánticos.
            partes = []
            for clave in ATRIBUTOS_PBF_SEMANTICOS:
                if clave in ATRIBUTOS_PBF_DESCARTADOS:
                    continue
                valor = props.get(clave)
                if valor in (None, ""):
                    continue
                partes.append(f"{clave}: {aplanar_valor(formatear_valor(valor))}")
            if partes:
                lineas.append(" | ".join(partes))
                features_indexados += 1

        if lineas:
            bloques.append(f"capa: {nombre_capa}\n" + "\n".join(lineas))

    extra = parsear_zoom_x_y(ruta_relativa)
    extra["num_features_tile"] = total_features
    # Se cuentan features, no líneas. Antes se contaban líneas y los popups
    # multilínea inflaban la cifra: un feature de 4 líneas contaba como 4.
    extra["num_features_indexados"] = features_indexados
    extra["capas"] = list(tile.keys())

    return "\n\n".join(bloques), None, extra