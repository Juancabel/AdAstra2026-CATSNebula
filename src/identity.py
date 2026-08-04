"""
identity.py — Normalización de texto e identificadores estables.

Módulo compartido por TODO el equipo (A, B, C y D).
Nadie debe calcular un doc_id fuera de aquí.

Alcance:
    Este módulo cubre SOLO identidad a nivel de documento.
    El chunk_id lo define C junto con la estrategia de fragmentación.

Regla crítica:
    Si NORMALIZER_VERSION cambia, TODOS los doc_id del corpus cambian.
    Eso obliga a reconstruir el índice FAISS y metadata.jsonl completos.
    No modificar normalize_text() después del Día 3 sin avisar al equipo.
"""

import hashlib
import re
import unicodedata

# Versión de la lógica de normalización.
# Súbela SOLO si cambias normalize_text(), y avisa al equipo: invalida los IDs.
NORMALIZER_VERSION = "1.0.0"

# Longitud por defecto del doc_id en caracteres hexadecimales.
# 12 hex = 48 bits. Con ~1e5 documentos la probabilidad de colisión es ~1e-5.
DOC_ID_LENGTH = 12

# Espacios "raros" que los extractores de PDF y HTML producen constantemente.
# \xa0 es el non-breaking space (&nbsp;), el más frecuente con diferencia.
_ESPACIOS_UNICODE = (
    "\u00a0"  # non-breaking space
    "\u1680"  # ogham space mark
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"  # espacios tipográficos
    "\u202f"  # narrow no-break space
    "\u205f"  # medium mathematical space
    "\u3000"  # ideographic space
)
_TABLA_ESPACIOS = {ord(c): " " for c in _ESPACIOS_UNICODE}

# Caracteres de ancho cero: invisibles, pero cambian el hash. Se eliminan.
_INVISIBLES = dict.fromkeys(
    [
        0xFEFF,  # BOM / zero-width no-break space
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x00AD,  # soft hyphen (muy común en PDFs justificados)
    ],
    None,
)

_RE_TRES_O_MAS_SALTOS = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """
    Normaliza texto antes de hashearlo o indexarlo.

    Estrategia "suave": limpia ruido de codificación pero PRESERVA la
    estructura de párrafos, porque C la usa como señal de chunking.

    Pasos:
      1. Unicode NFC — unifica "á" precompuesta vs. "a" + tilde combinante.
         Sin esto, un PDF y un HTML con la misma palabra dan hashes distintos.
      2. Elimina caracteres invisibles (BOM, zero-width, soft hyphen).
      3. Convierte espacios Unicode exóticos (\\xa0 etc.) a espacio normal.
      4. Unifica saltos de línea a \\n.
      5. Elimina caracteres de control (salvo \\n y \\t).
      6. Colapsa espacios/tabs repetidos dentro de cada línea.
      7. Quita espacios al final de cada línea.
      8. Colapsa 3+ saltos de línea a exactamente 2 (separador de párrafo).
      9. strip() global.

    Args:
        text: texto crudo recién extraído del archivo original.

    Returns:
        Texto normalizado, listo para hashear e indexar.
    """
    if text is None:
        return ""

    # 1. Normalización Unicode canónica compuesta.
    text = unicodedata.normalize("NFC", text)

    # 2. Fuera caracteres invisibles.
    text = text.translate(_INVISIBLES)

    # 3. Espacios Unicode exóticos -> espacio ASCII.
    text = text.translate(_TABLA_ESPACIOS)

    # 4. Saltos de línea: CRLF y CR sueltos -> LF.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 5. Caracteres de control (categoría Cc) excepto \n y \t.
    text = "".join(
        c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc"
    )

    # 6-7. Por línea: colapsar espacios internos y quitar los del final.
    lineas = [" ".join(linea.split()) for linea in text.split("\n")]
    text = "\n".join(lineas)

    # 8. Máximo dos saltos seguidos (un párrafo en blanco).
    text = _RE_TRES_O_MAS_SALTOS.sub("\n\n", text)

    # 9. Limpieza de bordes.
    return text.strip()


def compute_doc_id(text: str, length: int = DOC_ID_LENGTH) -> str:
    """
    Calcula el doc_id a partir del CONTENIDO del documento.

    Normaliza internamente: no hace falta (ni se debe) normalizar antes.
    Llamarla dos veces con el mismo texto crudo da siempre el mismo ID,
    en cualquier máquina, en cualquier proceso, en cualquier orden de archivos.

    Args:
        text: texto crudo extraído del archivo.
        length: caracteres hexadecimales a conservar.

    Returns:
        ID hexadecimal estable, ej. "a3f9c21b7e04".

    Raises:
        ValueError: si el texto normalizado queda vacío. Eso significa que la
            extracción falló, y NO debe indexarse: si dejáramos pasar el texto
            vacío, todos los documentos fallidos compartirían el mismo hash y
            se pisarían entre sí en metadata.jsonl.
    """
    normalizado = normalize_text(text)

    if not normalizado:
        raise ValueError(
            "El texto normalizado está vacío: la extracción falló. "
            "Registra este archivo en el log de fallos en vez de indexarlo."
        )

    digest = hashlib.sha1(normalizado.encode("utf-8")).hexdigest()
    return digest[:length]


def text_is_usable(text: str, min_palabras: int = 20) -> bool:
    """
    Heurística rápida para decidir si un documento aporta algo o es ruido.

    Útil sobre todo para imágenes tras OCR y para tiles PBF con pocos atributos:
    indexar 3 palabras sueltas solo mete ruido en el índice.

    Args:
        text: texto crudo o normalizado.
        min_palabras: umbral mínimo de palabras.

    Returns:
        True si el documento merece indexarse.
    """
    normalizado = normalize_text(text)
    return len(normalizado.split()) >= min_palabras
