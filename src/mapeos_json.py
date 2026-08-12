"""
mapeos_json.py — Configuración de mapeo de campos por institución.

Este archivo es CONFIGURACIÓN, no lógica. Para añadir una institución nueva
basta con agregar una entrada aquí; no hay que tocar el código de extracción.

Derivado del reconocimiento real del corpus (inspect_data_formats.py).

Sintaxis de las rutas de campo:
    "title"                        -> clave directa
    "body_paragraphs[]"            -> lista de strings, se unen
    "alerta_meta.tema_clave"       -> clave anidada
    "sections[].heading"           -> lista de objetos, se saca una clave de cada uno
    "sections[].paragraphs[]"      -> lista de objetos, cada uno con lista de strings
"""

from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Archivos que NO son contenido del corpus
# ---------------------------------------------------------------------------

ARCHIVOS_EXCLUIDOS = {
    "Indice_Datos_Codefest.xlsx",
    "FASE ORDENADA CODEFEST.xlsx",
}

PREFIJO_BLOQUEO_EXCEL = "~$"

# Basura del sistema operativo. Se excluye EN SILENCIO: no son fallos de
# extracción, no aportan nada al log y solo lo ensucian.
BASURA_SISTEMA = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"}


# ---------------------------------------------------------------------------
# Familia A — esquema común de artículo web
# ---------------------------------------------------------------------------

FAMILIA_ARTICULO_WEB = {
    "texto": ["title", "body_paragraphs[]"],
    "titulo": "title",
    "extra": ["url", "date"],
}


# ---------------------------------------------------------------------------
# Familia B — casos especiales
# ---------------------------------------------------------------------------

MAPEO_ALERTAS = {
    "texto": [
        "alerta_meta.tipo",
        "alerta_meta.municipios",
        "alerta_meta.tema_clave",
        "body_paragraphs[]",
    ],
    "titulo": None,
    "extra": ["url", "alerta_meta.codigo", "alerta_meta.fecha_emision"],
}

MAPEO_CEEEP = {
    "texto": ["title", "abstract"],
    "titulo": "title",
    "extra": ["url", "date", "doi", "issue"],
}

MAPEO_CENIA = {
    "texto": [
        "title",
        "sections[].heading",
        "sections[].paragraphs[]",
        "lists[]",
    ],
    "titulo": "title",
    "extra": ["url"],
}


# ---------------------------------------------------------------------------
# Familia C — catálogos de scraping
# ---------------------------------------------------------------------------

PATRONES_CATALOGO = ("catalog", "catalogo", "registro", "tiles-index", "indice")

CAMPOS_RUIDO_CATALOGO = {
    # URLs y rutas
    "url", "url_page", "url_pdf", "pdf_url", "page_url", "detail_url",
    "local_path", "path", "dest", "file", "filename", "pdf_local",
    "urls", "hashes", "src", "href", "link", "links",
    # Estado del scraping
    "status", "scraped_at", "from_cache", "content_type", "error",
    "desde", "page", "entries",
    # Tamaños y códigos internos
    "size_bytes", "size_mb", "pdf_size_kb", "fid", "detail_id",
    "study_id", "tile", "zoom", "x", "y", "pmid", "doi",
    "id", "_id", "uuid", "hash", "md5",
}


# ---------------------------------------------------------------------------
# Mapeo institución -> configuración
# ---------------------------------------------------------------------------

GENERIC_TEXTO_FIELDS = [
    "article.body",
    "articulo.body",
    "body",
    "content",
    "texto",
    "description",
    "detalle",
]
GENERIC_TITULO = ["title", "titulo", "headline", "articulo.title"]

MAPEOS_POR_INSTITUCION: Dict[str, Dict] = {
    # Familia A (referencia canonical)
    "F1_IA_y_Capacidades_Estrategicas/Atlantic_Council": FAMILIA_ARTICULO_WEB,
    "F2_Seguridad_Entorno_Espacial/CSIS_Aerospace": FAMILIA_ARTICULO_WEB,
    "F2_Seguridad_Entorno_Espacial/SWF_Counterspace": FAMILIA_ARTICULO_WEB,
    "F2_Seguridad_Entorno_Espacial/INPE": FAMILIA_ARTICULO_WEB,
    "F2_Seguridad_Entorno_Espacial/ESA_Space_Debris": FAMILIA_ARTICULO_WEB,
    "F3_Dinamicas_Territoriales/SIPRI": FAMILIA_ARTICULO_WEB,
    "F3_Dinamicas_Territoriales/CEOBS": FAMILIA_ARTICULO_WEB,

    # Familia B
    "F3_Dinamicas_Territoriales/Alertas_Tempranas": MAPEO_ALERTAS,
    "F3_Dinamicas_Territoriales/CEEEP": MAPEO_CEEEP,
    "F1_IA_y_Capacidades_Estrategicas/CENIA": MAPEO_CENIA,

    # Catálogo / asegurar presencia en el mapa
    "F1_IA_y_Capacidades_Estrategicas/DAIO": FAMILIA_ARTICULO_WEB,
    "F1_IA_y_Capacidades_Estrategicas/Defensa21_LatAm": FAMILIA_ARTICULO_WEB,
    "F1_IA_y_Capacidades_Estrategicas/RutaN_GEIAL": FAMILIA_ARTICULO_WEB,
    "F3_Dinamicas_Territoriales/MAPP_OEA": FAMILIA_ARTICULO_WEB,
    "F3_Dinamicas_Territoriales/RESDAL": FAMILIA_ARTICULO_WEB,
    "F3_Dinamicas_Territoriales/Amazon_Underworld": FAMILIA_ARTICULO_WEB,
}

# Insertar mapeos específicos / auto-generados que ya teníamos
MAPEOS_POR_INSTITUCION["F1_IA_y_Capacidades_Estrategicas/Atlantic_Council"] = FAMILIA_ARTICULO_WEB

# Defensa21_LatAm: mapeo específico derivado de inspección local
MAPEOS_POR_INSTITUCION["F1_IA_y_Capacidades_Estrategicas/Defensa21_LatAm"] = {
    "texto": [
        "articulo.contenido",
        "contenido",
        "body",
        "texto",
        "article.body",
    ],
    "titulo": "articulo.titulo",
    "extra": ["autor", "fecha", "seccion"],
}

# Entradas conservadoras para fuentes detectadas en fallos
MAPEOS_POR_INSTITUCION["F3_Dinamicas_Territoriales/CEOBS"] = FAMILIA_ARTICULO_WEB
MAPEOS_POR_INSTITUCION["F3_Dinamicas_Territoriales/MAPP_OEA"] = FAMILIA_ARTICULO_WEB
MAPEOS_POR_INSTITUCION["F3_Dinamicas_Territoriales/RESDAL"] = FAMILIA_ARTICULO_WEB
MAPEOS_POR_INSTITUCION["F3_Dinamicas_Territoriales/SIPRI"] = FAMILIA_ARTICULO_WEB


# ---------------------------------------------------------------------------
# PBF
# ---------------------------------------------------------------------------

ATRIBUTOS_PBF_PRIORITARIOS = [
    "au_popup_window_es",
    "au_popup_window_pt",
    "au_popup_window_en",
]

ATRIBUTOS_PBF_SEMANTICOS = [
    "au_country", "au_level1", "au_level2",
    "b_ADM1_PT", "b_ADM2_PT", "b_adm1_geral", "b_adm2_geral",
    "au_population", "au_area km",
    "au_emc", "au_embf", "au_eln", "au_c_d_f", "au_seg_marq",
    "au_lobos", "au_choneros", "au_cv", "au_pcc", "au_others",
    "au_no_info", "au_invest. with presence",
]

ATRIBUTOS_PBF_DESCARTADOS = {
    "fid", "b_ADM1_PCODE", "b_ADM2_PCODE",
    "b_ID_concatenated", "au_ID_concatenated",
}


__all__ = [
    "ARCHIVOS_EXCLUIDOS",
    "PREFIJO_BLOQUEO_EXCEL",
    "BASURA_SISTEMA",
    "FAMILIA_ARTICULO_WEB",
    "MAPEO_ALERTAS",
    "MAPEO_CEEEP",
    "MAPEO_CENIA",
    "PATRONES_CATALOGO",
    "CAMPOS_RUIDO_CATALOGO",
    "MAPEOS_POR_INSTITUCION",
    "ATRIBUTOS_PBF_PRIORITARIOS",
    "ATRIBUTOS_PBF_SEMANTICOS",
    "ATRIBUTOS_PBF_DESCARTADOS",
]
