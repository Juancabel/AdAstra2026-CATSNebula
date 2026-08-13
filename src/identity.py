"""
identity.py — Normalización de texto e identificadores estables.

Módulo compartido por TODO el equipo (A, B, C y D).

CAMBIO DE CONTRATO — el doc_id ya no se calcula aquí
    Los organizadores confirmaron en el Q&A que el emparejamiento de documentos
    se hace con el DOC_ID que ADL suministra en `Indice_Datos_Codefest.xlsx`
    (ej. "F1-AIINDEX-001"). El doc_id se LEE del índice oficial, no se deriva
    del texto: ver `indice_oficial.py`.

    Consecuencia buena: la identidad dejó de depender de `normalize_text()`.
    Cambiar la normalización ya NO invalida los doc_id del corpus, y el
    bloqueante de doc_id duplicados (11 doc_id compartidos entre 20 documentos)
    desaparece por construcción, porque los DOC_ID oficiales son únicos.

Lo que sí sigue viviendo aquí:
    normalize_text()      — limpieza del texto que se indexa (sin cambios)
    compute_content_sha1()— huella del CONTENIDO, va en `extra`. Ya no es
                            identidad, pero sigue detectando los duplicados
                            de contenido conocidos (CEOBS x8, SWF x1, teselas).
    text_is_usable()      — heurística de "esto aporta algo".

Alcance:
    Este módulo cubre SOLO texto y huella de contenido.
    El chunk_id lo define C junto con la estrategia de fragmentación.

Regla que sigue vigente:
    normalize_text() alimenta el índice vectorial. Cambiarla obliga a
    reconstruir FAISS y metadata.jsonl, aunque ya no toque los doc_id.
"""

import hashlib
import re
import unicodedata

# Versión de la lógica de normalización.
# Súbela SOLO si cambias normalize_text(), y avisa al equipo: invalida los IDs.
NORMALIZER_VERSION = "1.1.0"

# Longitud por defecto de content_sha1 en caracteres hexadecimales.
# 12 hex = 48 bits. Con ~1e5 documentos la probabilidad de colisión es ~1e-5.
CONTENT_SHA1_LENGTH = 12

# Separadores de línea Unicode. CRÍTICOS: json.dumps() NO los escapa, así que
# sobreviven al JSONL y cualquier herramienta que parta por líneas ve un objeto
# donde solo hay una línea real. Es la causa de las "líneas blancas" en VS Code.
_SEPARADORES_LINEA = {
    0x2028,  # LINE SEPARATOR (categoría Zl)
    0x2029,  # PARAGRAPH SEPARATOR (categoría Zp)
}

_RE_TRES_O_MAS_SALTOS = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """
    Normaliza texto antes de hashearlo o indexarlo.

    Estrategia "suave": limpia ruido de codificación pero PRESERVA la
    estructura de párrafos, porque C la usa como señal de chunking.

    Pasos:
      1. Unicode NFC — unifica "á" precompuesta vs. "a" + tilde combinante.
         Sin esto, un PDF y un HTML con la misma palabra dan hashes distintos.
      2. Convierte U+2028/U+2029 a salto de línea normal. json.dumps() NO
         los escapa, así que romperían el conteo de líneas del JSONL.
      3. Elimina todo carácter de formato (categoría Cf): BOM, zero-width,
         soft hyphen, marcas direccionales, word joiner.
      4. Convierte cualquier separador de espacio (categoría Zs) a espacio
         ASCII: cubre \\xa0 y todos los espacios tipográficos.
      5. Unifica saltos de línea a \\n y elimina controles (categoría Cc).
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

    # 2. Saltos de línea: CRLF y CR sueltos -> LF.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3-5. Una sola pasada por categoría Unicode.
    #      Cf = formato (BOM, zero-width, soft hyphen, LRM/RLM, word joiner)
    #      Zs = separador de espacio (\xa0 y todos los espacios tipográficos)
    #      Zl/Zp = separadores de línea Unicode -> salto normal
    #      Cc = control -> se eliminan salvo \n y \t
    salida = []
    for c in text:
        if ord(c) in _SEPARADORES_LINEA:
            salida.append("\n")
            continue
        categoria = unicodedata.category(c)
        if categoria == "Cf":
            continue                      # invisible: fuera
        if categoria == "Zs":
            salida.append(" ")            # cualquier espacio raro -> espacio
            continue
        if categoria == "Cc" and c not in "\n\t":
            continue                      # control: fuera
        salida.append(c)
    text = "".join(salida)

    # 6-7. Por línea: colapsar espacios internos y quitar los del final.
    lineas = [" ".join(linea.split()) for linea in text.split("\n")]
    text = "\n".join(lineas)

    # 8. Máximo dos saltos seguidos (un párrafo en blanco).
    text = _RE_TRES_O_MAS_SALTOS.sub("\n\n", text)

    # 9. Limpieza de bordes.
    return text.strip()


def compute_content_sha1(text: str, length: int = CONTENT_SHA1_LENGTH) -> str | None:
    """
    Huella del CONTENIDO del documento. Va en `extra`, no es la identidad.

    Normaliza internamente: no hace falta (ni se debe) normalizar antes.
    Llamarla dos veces con el mismo texto crudo da siempre la misma huella,
    en cualquier máquina, en cualquier proceso, en cualquier orden de archivos.

    Sirve para detectar documentos con contenido idéntico pero `fuente` (y por
    tanto DOC_ID) distintos: los 8 informes repetidos de CEOBS, el duplicado de
    SWF y las teselas PBF que comparten features.

    A diferencia del antiguo compute_doc_id(), un texto vacío NO es un error:
    ahora se emiten documentos sin texto (imágenes sin OCR legible) para
    conservar su DOC_ID y su metadata, tal como pidieron los organizadores.
    En ese caso no hay contenido que resumir y se devuelve None, en vez de
    darles a todos la misma huella del string vacío.

    Args:
        text: texto crudo extraído del archivo.
        length: caracteres hexadecimales a conservar.

    Returns:
        Huella hexadecimal estable (ej. "a3f9c21b7e04"), o None si no hay texto.
    """
    normalizado = normalize_text(text)

    if not normalizado:
        return None

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