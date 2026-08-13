"""
Pruebas de corpus_paths.py, usando rutas reales tomadas del árbol del corpus.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from corpus_paths import (  # noqa: E402
    infer_fenomeno,
    FORMATOS_VALIDOS,
    infer_formato,
    looks_like_map_tile,
    looks_like_paginated_report,
)


# ---------------------------------------------------------------------------
# infer_fenomeno — con rutas reales del árbol compartido
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ruta,esperado",
    [
        ("F1_IA_y_Capacidades_Estrategicas/AI_Index_Stanford/pdfs/reporte.pdf", 1),
        ("F1_IA_y_Capacidades_Estrategicas/CSET_Georgetown/pdfs/Annual_Report/a.pdf", 1),
        ("F2_Seguridad_Entorno_Espacial/CSIS_Aerospace/csis_pdfs/b.pdf", 2),
        ("F2_Seguridad_Entorno_Espacial/UNOOSA/pdfs/2025/c.pdf", 2),
        ("F3_Dinamicas_Territoriales/Amazon_Underworld/tiles/3/2/1.pbf", 3),
        ("F3_Dinamicas_Territoriales/SIPRI/pdfs_full/2026/d.pdf", 3),
    ],
)
def test_fenomeno_con_rutas_reales(ruta, esperado):
    assert infer_fenomeno(ruta) == esperado


def test_fenomeno_con_ruta_absoluta_windows():
    """El corpus puede llegar con rutas estilo Windows si alguien lo descarga ahí."""
    ruta = r"C:\Users\equipo\corpus\F2_Seguridad_Entorno_Espacial\ESA_Space_Debris\pdfs\x.pdf"
    assert infer_fenomeno(ruta) == 2


def test_fenomeno_sin_prefijo_falla():
    """Un archivo fuera de F1_/F2_/F3_ no debe indexarse con un fenómeno adivinado."""
    with pytest.raises(ValueError):
        infer_fenomeno("carpeta_suelta/archivo.pdf")


# ---------------------------------------------------------------------------
# infer_formato
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ruta,esperado",
    [
        ("AI_Index_Stanford/pdfs/reporte.pdf", "pdf"),
        ("CEEEP/articulos/revista/CEEEP_issue2-9-cultura.json", "json"),
        ("RutaN_GEIAL/pdfs/Observatorio/x.PDF", "pdf"),  # extensión en mayúsculas
        ("AI_Index_Stanford/recursos/Research_Development/datasets/d.csv", "csv"),
        ("AI_Index_Stanford/recursos/Healthcare_Medicine/datasets/e.xlsx", "xlsx"),
        ("SWF_Counterspace/swf_counterspace_2026/images/foto.jpg", "jpg"),
        ("SWF_Counterspace/swf_counterspace_2026/images/foto.avif", "avif"),
        ("SWF_Counterspace/swf_counterspace_2026/SWF_full-text.txt", "txt"),
        ("Amazon_Underworld/tiles/5/10/8.pbf", "pbf"),
    ],
)
def test_formato_con_rutas_reales(ruta, esperado):
    assert infer_formato(ruta) == esperado


def test_formato_solo_del_vocabulario_del_reto():
    """
    ADL fijó el vocabulario: la extensión real en minúsculas. Nada de
    etiquetas de la Tabla 1 del handbook ("Imagen", "Excel", "Otro") ni de
    agrupaciones propias como "imagen", que juntaba jpg y avif en un valor
    que el contrato no admite.
    """
    for ruta in (
        "a/b.pdf", "a/b.json", "a/b.csv", "a/b.xlsx",
        "a/b.jpg", "a/b.avif", "a/b.txt", "a/b.pbf",
    ):
        assert infer_formato(ruta) in FORMATOS_VALIDOS


def test_txt_no_se_confunde_con_markdown():
    """
    `.txt` mapeaba a "md", y con eso extraer_txt() era inalcanzable y el
    único .txt del corpus (SWF_full-text.txt) se quedaba sin ingerir.
    """
    assert infer_formato("SWF_Counterspace/swf_counterspace_2026/SWF_full-text.txt") == "txt"


def test_jpg_y_avif_son_formatos_distintos():
    """El índice oficial trae ambos bajo `Tipo: Otro`/`Imagen`; el contrato no."""
    assert infer_formato("x/foto.jpg") == "jpg"
    assert infer_formato("x/foto.avif") == "avif"


def test_formato_ignora_nombre_de_carpeta():
    """
    Un .json metido por error en una carpeta llamada "pdfs" se detecta
    como json, no como pdf. La carpeta es una pista, no la verdad.
    """
    assert infer_formato("CSIS_Aerospace/csis_pdfs/en_realidad_es.json") == "json"


def test_formato_desconocido_falla():
    with pytest.raises(ValueError):
        infer_formato("archivo.docx")


def test_formatos_retirados_del_corpus_fallan():
    """
    No hay HTML ni Markdown en el corpus real. Se quitaron del mapeo para que
    un archivo inesperado se detecte en vez de colarse con un formato que el
    contrato no admite.
    """
    for ruta in ("x/pagina.html", "x/notas.md"):
        with pytest.raises(ValueError):
            infer_formato(ruta)


# ---------------------------------------------------------------------------
# looks_like_paginated_report — el caso Atlantic_Council/GeoTech_Cues
# ---------------------------------------------------------------------------

def test_detecta_reporte_paginado():
    ruta = (
        "F1_IA_y_Capacidades_Estrategicas/Atlantic_Council/GeoTech_Cues/"
        "page_07/contenido.pdf"
    )
    assert looks_like_paginated_report(ruta) is True


def test_no_confunde_carpeta_normal_con_pagina():
    ruta = "F1_IA_y_Capacidades_Estrategicas/CSET_Georgetown/pdfs/Reports/a.pdf"
    assert looks_like_paginated_report(ruta) is False


# ---------------------------------------------------------------------------
# looks_like_map_tile — el caso Amazon_Underworld/tiles
# ---------------------------------------------------------------------------

def test_detecta_tile_de_mapa():
    ruta = "F3_Dinamicas_Territoriales/Amazon_Underworld/tiles/5/10/8.pbf"
    assert looks_like_map_tile(ruta) is True


def test_no_confunde_pdf_normal_con_tile():
    ruta = "F3_Dinamicas_Territoriales/SIPRI/pdfs_full/2026/informe.pdf"
    assert looks_like_map_tile(ruta) is False
