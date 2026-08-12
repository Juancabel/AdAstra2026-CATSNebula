"""
indice_oficial.py — Stub minimal para pruebas locales.

Provee `cargar_indice_oficial` y `comparar_con_ingesta` con comportamiento
mínimo para que `ingest_data.py` pueda ejecutarse en entorno de desarrollo.
"""
from typing import Dict, Set

def cargar_indice_oficial(path) -> Dict[str, dict]:
    """Carga un índice oficial desde Excel/CSV.

    Stub: devuelve diccionario vacío; el código que llama ya maneja
    la ausencia de índice.
    """
    return {}


def comparar_con_ingesta(indice_de_B: Dict[str, dict], fuentes_de_B: Set[str]) -> dict:
    """Compara el índice con las fuentes vistas por la ingesta.

    Devuelve la estructura esperada por `ingest_data.ingestar`.
    """
    conjunto_indice = set(indice_de_B.keys())
    conjunto_vistos = set(fuentes_de_B)
    cubiertas = len(conjunto_indice & conjunto_vistos)
    return {
        "total_indice": len(conjunto_indice),
        "total_vistas": len(conjunto_vistos),
        "cubiertas": cubiertas,
        "faltan_por_ingerir": list(conjunto_indice - conjunto_vistos),
        "no_en_indice": list(conjunto_vistos - conjunto_indice),
    }
