"""
corpus_paths.py — Inferir fenomeno y formato a partir de la ruta del archivo.

Basado en el árbol real del corpus (carpetas F1_/F2_/F3_ en la raíz).
Módulo de B. No decide doc_id (eso es identity.py) ni chunking (eso es C).
"""

from pathlib import Path, PurePosixPath

# Prefijo de carpeta -> número de fenómeno, según el árbol real del corpus.
_PREFIJO_A_FENOMENO = {
    "F1_IA_y_Capacidades_Estrategicas": 1,
    "F2_Seguridad_Entorno_Espacial": 2,
    "F3_Dinamicas_Territoriales": 3,
}

# Extensión de archivo -> formato. Fuente de verdad: la extensión real,
# NUNCA el nombre de la carpeta ("articulos", "noticias", etc. son pistas,
# no garantías — un archivo puede estar mal ubicado).
_EXTENSION_A_FORMATO = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".md": "md",
    ".txt": "md",
    ".png": "imagen",
    ".jpg": "imagen",
    ".jpeg": "imagen",
    ".pbf": "pbf",
    ".mvt": "pbf",  # Mapbox Vector Tile, extensión alternativa común
}


def infer_fenomeno(ruta: str) -> int:
    """
    Determina el fenómeno (1, 2 o 3) a partir del primer segmento de la ruta.

    Funciona con ruta absoluta, relativa, o solo el nombre de la carpeta raíz.
    Ejemplos válidos:
        "F1_IA_y_Capacidades_Estrategicas/AI_Index_Stanford/pdfs/a.pdf"
        "/home/user/corpus/F2_Seguridad_Entorno_Espacial/CSIS_Aerospace/b.pdf"

    Args:
        ruta: ruta del archivo dentro del corpus.

    Returns:
        1, 2 o 3.

    Raises:
        ValueError: si ningún segmento de la ruta coincide con un prefijo
            F1_/F2_/F3_ conocido. Esto es intencional: un archivo sin
            fenómeno claro NO debe indexarse en silencio con un valor
            adivinado — hay que revisarlo a mano.
    """
    partes = PurePosixPath(ruta.replace("\\", "/")).parts

    for parte in partes:
        if parte in _PREFIJO_A_FENOMENO:
            return _PREFIJO_A_FENOMENO[parte]

    raise ValueError(
        f"No se pudo determinar el fenómeno para la ruta: {ruta!r}. "
        f"Se esperaba encontrar una carpeta F1_/F2_/F3_ en la ruta. "
        f"Revisar manualmente antes de indexar."
    )


def infer_formato(ruta: str) -> str:
    """
    Determina el formato a partir de la EXTENSIÓN real del archivo.

    Deliberadamente ignora el nombre de la carpeta contenedora
    ("articulos", "pdfs_full", "noticias", "paginas"...): son pistas
    útiles al ojear el árbol, pero no una fuente confiable — nada impide
    que alguien guarde un .html dentro de una carpeta llamada "pdfs".

    Args:
        ruta: ruta o nombre del archivo.

    Returns:
        Uno de: pdf, html, json, csv, xlsx, md, imagen, pbf.

    Raises:
        ValueError: extensión no reconocida. Mejor fallar aquí que
            adivinar y meter un formato incorrecto en la metadata.
    """
    extension = Path(ruta).suffix.lower()

    if extension not in _EXTENSION_A_FORMATO:
        raise ValueError(
            f"Extensión no reconocida: {extension!r} en {ruta!r}. "
            f"Formatos soportados: {sorted(_EXTENSION_A_FORMATO)}. "
            f"Si es un formato nuevo y legítimo, agregarlo a "
            f"_EXTENSION_A_FORMATO y avisar al equipo."
        )

    return _EXTENSION_A_FORMATO[extension]


def looks_like_paginated_report(ruta: str) -> bool:
    """
    Detecta el patrón "carpeta/page_01/, page_02/, ..." visto en
    Atlantic_Council/GeoTech_Cues.

    Esto NO decide automáticamente qué hacer — solo avisa. El equipo
    debe decidir si estas páginas se concatenan en un solo documento
    o se tratan como documentos separados (ver discusión en el chat).

    Args:
        ruta: ruta del archivo.

    Returns:
        True si algún segmento de la ruta tiene forma "page_NN" o "pagina_NN".
    """
    partes = PurePosixPath(ruta.replace("\\", "/")).parts
    for parte in partes:
        p = parte.lower()
        if (p.startswith("page_") or p.startswith("pagina_")) and p[
            p.index("_") + 1 :
        ].isdigit():
            return True
    return False


def looks_like_map_tile(ruta: str) -> bool:
    """
    Detecta el patrón "tiles/zoom/x/y" visto en Amazon_Underworld.

    El mismo elemento geográfico se repite en cada nivel de zoom
    (el spec lo advierte explícitamente en §2.1) — hay que deduplicar
    antes de indexar, no solo al extraer.

    Args:
        ruta: ruta del archivo.

    Returns:
        True si la ruta contiene una carpeta "tiles" seguida de
        segmentos puramente numéricos (zoom/x/y).
    """
    partes = PurePosixPath(ruta.replace("\\", "/")).parts
    if "tiles" not in partes:
        return False
    idx = partes.index("tiles")
    siguientes = partes[idx + 1 :]
    return any(p.isdigit() for p in siguientes)
