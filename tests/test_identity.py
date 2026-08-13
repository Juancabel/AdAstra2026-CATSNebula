"""
Pruebas de identity.py

Ejecutar desde la raíz del repo:
    pytest tests/test_identity.py -v

Estas pruebas son la red de seguridad del contrato de IDs. Si alguien toca
normalize_text() más adelante, TEST_VECTORES_CONGELADOS falla y avisa de que
todos los doc_id del corpus acaban de cambiar.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from identity import (  # noqa: E402
    NORMALIZER_VERSION,
    compute_content_sha1,
    normalize_text,
    text_is_usable,
)


# ---------------------------------------------------------------------------
# 1. Determinismo: lo mínimo que tiene que cumplirse
# ---------------------------------------------------------------------------

def test_mismo_texto_mismo_id():
    """Dos llamadas con el mismo texto dan el mismo ID."""
    texto = "La congestión orbital en LEO es un riesgo creciente."
    assert compute_content_sha1(texto) == compute_content_sha1(texto)


def test_textos_distintos_ids_distintos():
    """Textos diferentes dan IDs diferentes."""
    a = compute_content_sha1("Primer documento sobre basura espacial.")
    b = compute_content_sha1("Segundo documento sobre basura espacial.")
    assert a != b


def test_id_estable_entre_procesos():
    """
    El ID es idéntico en un proceso nuevo de Python.

    Importa porque hash() de Python SÍ está aleatorizado por proceso
    (PYTHONHASHSEED). hashlib.sha1 no lo está. Esta prueba lo demuestra.
    """
    texto = "Inteligencia artificial en el sector defensa."
    esperado = compute_content_sha1(texto)

    src = str(Path(__file__).resolve().parent.parent / "src")
    codigo = (
        f"import sys; sys.path.insert(0, {src!r});"
        f"from identity import compute_content_sha1;"
        f"print(compute_content_sha1({texto!r}))"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=True
    )
    assert resultado.stdout.strip() == esperado


# ---------------------------------------------------------------------------
# 2. Codificación: los casos que rompen un corpus ES/EN/PT
# ---------------------------------------------------------------------------

def test_nfc_vs_nfd_mismo_id():
    """
    "Bogotá" escrita de las dos formas Unicode válidas da el MISMO ID.

    NFC: "á" es un solo codepoint (U+00E1)
    NFD: "á" es "a" (U+0061) + tilde combinante (U+0301)

    Bytes distintos, palabra idéntica. Los extractores de PDF y HTML
    no se ponen de acuerdo en cuál emiten. Sin normalización NFC,
    el mismo documento tendría dos IDs distintos según de dónde salga.
    """
    nfc = "Bogot\u00e1 es la capital."
    nfd = "Bogota\u0301 es la capital."
    assert nfc != nfd  # los strings crudos SÍ son distintos
    assert compute_content_sha1(nfc) == compute_content_sha1(nfd)


def test_saltos_de_linea_windows_y_unix():
    """CRLF (Windows) y LF (Unix) dan el mismo ID."""
    unix = "Primera línea\nSegunda línea"
    windows = "Primera línea\r\nSegunda línea"
    mac_viejo = "Primera línea\rSegunda línea"
    assert compute_content_sha1(unix) == compute_content_sha1(windows) == compute_content_sha1(mac_viejo)


def test_bom_ignorado():
    """El BOM al principio del archivo no cambia el ID."""
    sin_bom = "Contenido del documento."
    con_bom = "\ufeffContenido del documento."
    assert compute_content_sha1(sin_bom) == compute_content_sha1(con_bom)


def test_espacio_no_separable():
    """
    El non-breaking space (\\xa0) se trata como espacio normal.

    Los PDFs y los &nbsp; de HTML lo producen constantemente.
    """
    normal = "Fuerza Aeroespacial Colombiana"
    nbsp = "Fuerza\u00a0Aeroespacial\u00a0Colombiana"
    assert compute_content_sha1(normal) == compute_content_sha1(nbsp)


def test_soft_hyphen_eliminado():
    """El guion suave de los PDFs justificados se elimina."""
    limpio = "internacional"
    con_shy = "inter\u00adnacional"
    assert compute_content_sha1(limpio) == compute_content_sha1(con_shy)


def test_separadores_de_linea_unicode():
    """
    U+2028 y U+2029 se convierten en salto de línea normal.

    ES EL BUG MÁS IMPORTANTE DE ESTE MÓDULO: json.dumps() NO los escapa,
    así que sobreviven al JSONL y cualquier herramienta que parta el archivo
    por líneas ve dos líneas donde solo escribimos un objeto. VS Code los
    reporta como "unusual line terminators".
    """
    con_u2028 = "Primer parrafo.\u2028Segundo parrafo."
    con_u2029 = "Primer parrafo.\u2029Segundo parrafo."
    normal = "Primer parrafo.\nSegundo parrafo."

    assert normalize_text(con_u2028) == normal
    assert normalize_text(con_u2029) == normal
    assert compute_content_sha1(con_u2028) == compute_content_sha1(normal)


def test_jsonl_sobrevive_una_sola_linea():
    """
    Un documento con separadores Unicode debe ocupar EXACTAMENTE una línea
    del JSONL tras normalizar. Es la prueba de regresión del bug real.
    """
    import json as _json

    crudo = "Texto con\u2028separador\u2029raro y \u200b invisible."
    doc = {"doc_id": compute_content_sha1(crudo), "text": normalize_text(crudo)}
    linea = _json.dumps(doc, ensure_ascii=False) + "\n"

    assert len(linea.splitlines()) == 1, (
        "El documento ocupa más de una línea: quedaron separadores Unicode "
        "sin normalizar y el JSONL está roto."
    )


def test_marcas_direccionales_y_word_joiner():
    """Caracteres de categoría Cf se eliminan (LRM, RLM, word joiner)."""
    limpio = "texto normal"
    sucio = "texto\u200e\u200f\u2060 normal"
    assert compute_content_sha1(limpio) == compute_content_sha1(sucio)


def test_espacios_redundantes():
    """Espacios y tabs repetidos no cambian el ID."""
    a = "Palabra    otra\t\tpalabra"
    b = "Palabra otra palabra"
    assert compute_content_sha1(a) == compute_content_sha1(b)


def test_espacios_al_final_de_linea():
    """Espacios finales de línea no cambian el ID."""
    a = "Línea uno   \nLínea dos  "
    b = "Línea uno\nLínea dos"
    assert compute_content_sha1(a) == compute_content_sha1(b)


# ---------------------------------------------------------------------------
# 3. Propiedades de normalize_text
# ---------------------------------------------------------------------------

def test_normalizacion_idempotente():
    """
    normalize(normalize(x)) == normalize(x)

    Si esto falla, aplicar la normalización dos veces (fácil que pase entre
    A/B y C) cambiaría el texto y por tanto el ID.
    """
    crudo = "  Título\r\n\n\n\nCuerpo\u00a0del   texto.  \n\n\n"
    una_vez = normalize_text(crudo)
    dos_veces = normalize_text(una_vez)
    assert una_vez == dos_veces


def test_estructura_de_parrafos_preservada():
    """
    Los saltos de párrafo sobreviven: C los necesita como frontera de chunk.
    3+ saltos se colapsan a 2, pero nunca a 0.
    """
    texto = "Párrafo uno.\n\n\n\n\nPárrafo dos."
    assert normalize_text(texto) == "Párrafo uno.\n\nPárrafo dos."


def test_salto_simple_no_se_convierte_en_espacio():
    """Un salto de línea simple se conserva como salto."""
    assert normalize_text("Línea A\nLínea B") == "Línea A\nLínea B"


# ---------------------------------------------------------------------------
# 4. Casos borde
# ---------------------------------------------------------------------------

def test_texto_vacio_da_none():
    """
    Un documento sin texto no tiene huella de contenido, y eso ya no es error.

    Cuando el doc_id era el hash, un texto vacío TENÍA que fallar: todos los
    documentos con extracción fallida habrían compartido el mismo id y se
    habrían pisado entre sí. Con el DOC_ID oficial la identidad viene del
    inventario de ADL, así que un documento sin texto (una imagen sin texto
    legible) se emite igual, con su metadata y sin content_sha1.

    Devolver None en vez del hash del string vacío es deliberado: si no, todos
    los vacíos compartirían huella y parecerían duplicados entre sí.
    """
    assert compute_content_sha1("") is None


def test_solo_espacios_da_none():
    """Texto que solo tiene espacios equivale a no tener contenido."""
    assert compute_content_sha1("   \n\n\t  \u00a0 ") is None


def test_none_da_none():
    """None no revienta con AttributeError: normalize_text lo absorbe."""
    assert compute_content_sha1(None) is None


def test_longitud_de_la_huella():
    """La huella tiene la longitud pedida y es hexadecimal."""
    sha1 = compute_content_sha1("Cualquier texto.")
    assert len(sha1) == 12
    assert all(c in "0123456789abcdef" for c in sha1)


def test_longitud_personalizable():
    """Se puede pedir otra longitud; es prefijo del mismo hash."""
    texto = "Cualquier texto."
    assert len(compute_content_sha1(texto, length=16)) == 16
    assert compute_content_sha1(texto, length=16).startswith(compute_content_sha1(texto, length=12))


# ---------------------------------------------------------------------------
# 5. text_is_usable
# ---------------------------------------------------------------------------

def test_texto_util_y_no_util():
    corto = "Figura 3"  # típico OCR de un pie de imagen
    largo = " ".join(["palabra"] * 50)
    assert not text_is_usable(corto)
    assert text_is_usable(largo)


# ---------------------------------------------------------------------------
# 6. Vectores congelados — la prueba de regresión que más importa
# ---------------------------------------------------------------------------

# Estos vectores congelaban el hash de identidad. Con el DOC_ID oficial la
# identidad ya no depende del texto, así que congelar el hash dejó de proteger
# lo que importaba y protegía algo que ya no existe.
#
# Lo que SÍ sigue importando es la salida literal de normalize_text(): es el
# texto que se indexa y se fragmenta. Por eso ahora se congela el texto en vez
# del digest — y de paso la prueba dice qué cambió, no solo que cambió: un
# hash distinto no te enseña dónde está la diferencia, dos strings sí.
TEXTOS_NORMALIZADOS_CONGELADOS = {
    "ascii_simple": ("Hello world.", "Hello world."),
    "espanol_acentos": (
        "La órbita baja terrestre está congestionada.",
        "La órbita baja terrestre está congestionada.",
    ),
    "portugues": (
        "A segurança espacial é uma preocupação crescente.",
        "A segurança espacial é uma preocupação crescente.",
    ),
    "multilinea": (
        "Título del informe\n\nPrimer párrafo del documento.\n\nSegundo párrafo.",
        "Título del informe\n\nPrimer párrafo del documento.\n\nSegundo párrafo.",
    ),
    # Los casos que de verdad puede romper un refactor de normalize_text.
    "espacios_y_saltos_de_sobra": (
        "  Línea   con    espacios  \n\n\n\n  Otro párrafo  \t \n",
        "Línea con espacios\n\nOtro párrafo",
    ),
    "invisibles_y_separadores": (
        "﻿Texto con​ invisibles y separador",
        "Texto con invisibles\ny separador",
    ),
    "guion_suave_de_pdf": ("con­gestión", "congestión"),
}


@pytest.mark.parametrize("nombre", list(TEXTOS_NORMALIZADOS_CONGELADOS))
def test_normalizacion_congelada(nombre):
    """
    Si esta prueba falla, normalize_text() cambió y el texto indexado es otro.

    Ya NO invalida los doc_id (esos vienen del inventario de ADL), pero sí
    obliga a reconstruir el índice FAISS y metadata.jsonl, porque cambia lo
    que se vectoriza y las fronteras de chunk.

    NO actualices los valores esperados sin avisar al equipo.
    """
    texto, esperado = TEXTOS_NORMALIZADOS_CONGELADOS[nombre]
    assert normalize_text(texto) == esperado, (
        f"Cambió la normalización de '{nombre}'. NORMALIZER_VERSION actual: "
        f"{NORMALIZER_VERSION}. Si el cambio es intencionado, súbela y "
        f"reconstruye el índice."
    )


# ---------------------------------------------------------------------------
# 7. Prueba de humo end-to-end con un archivo real
# ---------------------------------------------------------------------------

def test_flujo_completo_con_archivo(tmp_path):
    """
    Simula el flujo real: leer archivo -> extraer -> ID -> escribir JSONL
    -> releer y comprobar que el ID se recalcula igual.

    Usa un .md porque es el formato más rápido de probar (leer y ya),
    pero el flujo es idéntico para PDF, CSV, XLSX o PBF: lo único que
    cambia es la función de extracción.
    """
    md = tmp_path / "informe_prueba.md"
    md.write_text(
        "# Informe de Prueba\n\n"
        "La órbita baja terrestre (LEO) presenta congestión creciente.\n\n"
        "Los desechos espaciales son un riesgo para la sostenibilidad.\n",
        encoding="utf-8",
    )

    texto_crudo = md.read_text(encoding="utf-8")
    doc_id = compute_content_sha1(texto_crudo)

    documento = {
        "doc_id": doc_id,
        "fuente": md.name,  # verbatim, tal cual lo entrega ADL
        "formato": "md",
        "fenomeno": 2,
        "lang": "es",
        "text": normalize_text(texto_crudo),
    }

    salida = tmp_path / "documents.jsonl"
    with salida.open("w", encoding="utf-8") as f:
        f.write(json.dumps(documento, ensure_ascii=False) + "\n")

    # Releer y verificar que sobrevive el viaje de ida y vuelta.
    with salida.open(encoding="utf-8") as f:
        lineas = f.readlines()

    assert len(lineas) == 1, "El texto metió saltos de línea y rompió el JSONL"

    recuperado = json.loads(lineas[0])
    assert recuperado["doc_id"] == doc_id
    assert recuperado["fuente"] == "informe_prueba.md"

    # Recalcular el ID desde el texto ya normalizado da lo mismo (idempotencia).
    assert compute_content_sha1(recuperado["text"]) == doc_id