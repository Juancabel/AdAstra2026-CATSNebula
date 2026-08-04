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
    compute_doc_id,
    normalize_text,
    text_is_usable,
)


# ---------------------------------------------------------------------------
# 1. Determinismo: lo mínimo que tiene que cumplirse
# ---------------------------------------------------------------------------

def test_mismo_texto_mismo_id():
    """Dos llamadas con el mismo texto dan el mismo ID."""
    texto = "La congestión orbital en LEO es un riesgo creciente."
    assert compute_doc_id(texto) == compute_doc_id(texto)


def test_textos_distintos_ids_distintos():
    """Textos diferentes dan IDs diferentes."""
    a = compute_doc_id("Primer documento sobre basura espacial.")
    b = compute_doc_id("Segundo documento sobre basura espacial.")
    assert a != b


def test_id_estable_entre_procesos():
    """
    El ID es idéntico en un proceso nuevo de Python.

    Importa porque hash() de Python SÍ está aleatorizado por proceso
    (PYTHONHASHSEED). hashlib.sha1 no lo está. Esta prueba lo demuestra.
    """
    texto = "Inteligencia artificial en el sector defensa."
    esperado = compute_doc_id(texto)

    src = str(Path(__file__).resolve().parent.parent / "src")
    codigo = (
        f"import sys; sys.path.insert(0, {src!r});"
        f"from identity import compute_doc_id;"
        f"print(compute_doc_id({texto!r}))"
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
    assert compute_doc_id(nfc) == compute_doc_id(nfd)


def test_saltos_de_linea_windows_y_unix():
    """CRLF (Windows) y LF (Unix) dan el mismo ID."""
    unix = "Primera línea\nSegunda línea"
    windows = "Primera línea\r\nSegunda línea"
    mac_viejo = "Primera línea\rSegunda línea"
    assert compute_doc_id(unix) == compute_doc_id(windows) == compute_doc_id(mac_viejo)


def test_bom_ignorado():
    """El BOM al principio del archivo no cambia el ID."""
    sin_bom = "Contenido del documento."
    con_bom = "\ufeffContenido del documento."
    assert compute_doc_id(sin_bom) == compute_doc_id(con_bom)


def test_espacio_no_separable():
    """
    El non-breaking space (\\xa0) se trata como espacio normal.

    Los PDFs y los &nbsp; de HTML lo producen constantemente.
    """
    normal = "Fuerza Aeroespacial Colombiana"
    nbsp = "Fuerza\u00a0Aeroespacial\u00a0Colombiana"
    assert compute_doc_id(normal) == compute_doc_id(nbsp)


def test_soft_hyphen_eliminado():
    """El guion suave de los PDFs justificados se elimina."""
    limpio = "internacional"
    con_shy = "inter\u00adnacional"
    assert compute_doc_id(limpio) == compute_doc_id(con_shy)


def test_espacios_redundantes():
    """Espacios y tabs repetidos no cambian el ID."""
    a = "Palabra    otra\t\tpalabra"
    b = "Palabra otra palabra"
    assert compute_doc_id(a) == compute_doc_id(b)


def test_espacios_al_final_de_linea():
    """Espacios finales de línea no cambian el ID."""
    a = "Línea uno   \nLínea dos  "
    b = "Línea uno\nLínea dos"
    assert compute_doc_id(a) == compute_doc_id(b)


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

def test_texto_vacio_lanza_error():
    """
    Un documento vacío NO puede recibir ID.

    Si lo permitiéramos, todos los documentos con extracción fallida
    compartirían el mismo hash y se sobrescribirían entre sí.
    """
    with pytest.raises(ValueError):
        compute_doc_id("")


def test_solo_espacios_lanza_error():
    """Texto que solo tiene espacios equivale a extracción fallida."""
    with pytest.raises(ValueError):
        compute_doc_id("   \n\n\t  \u00a0 ")


def test_none_lanza_error():
    """None no revienta con AttributeError, da ValueError controlado."""
    with pytest.raises(ValueError):
        compute_doc_id(None)


def test_longitud_del_id():
    """El ID tiene la longitud pedida y es hexadecimal."""
    doc_id = compute_doc_id("Cualquier texto.")
    assert len(doc_id) == 12
    assert all(c in "0123456789abcdef" for c in doc_id)


def test_longitud_personalizable():
    """Se puede pedir otra longitud; es prefijo del mismo hash."""
    texto = "Cualquier texto."
    assert len(compute_doc_id(texto, length=16)) == 16
    assert compute_doc_id(texto, length=16).startswith(compute_doc_id(texto, length=12))


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

TEST_VECTORES_CONGELADOS = {
    "ascii_simple": ("Hello world.", "e44f3364019d"),
    "espanol_acentos": (
        "La órbita baja terrestre está congestionada.",
        "fff761d4912a",
    ),
    "portugues": (
        "A segurança espacial é uma preocupação crescente.",
        "94b8133e8a02",
    ),
    "multilinea": (
        "Título del informe\n\nPrimer párrafo del documento.\n\nSegundo párrafo.",
        "f2c0075998d4",
    ),
}


@pytest.mark.parametrize("nombre", list(TEST_VECTORES_CONGELADOS))
def test_vectores_congelados(nombre):
    """
    Si esta prueba falla, normalize_text() cambió y TODOS los doc_id del
    corpus son ahora distintos. Hay que reconstruir el índice FAISS y
    metadata.jsonl enteros, y subir NORMALIZER_VERSION.

    NO actualices los valores esperados sin avisar al equipo.
    """
    texto, esperado = TEST_VECTORES_CONGELADOS[nombre]
    assert compute_doc_id(texto) == esperado, (
        f"Cambió el hash de '{nombre}'. NORMALIZER_VERSION actual: "
        f"{NORMALIZER_VERSION}. Si el cambio es intencionado, súbela y "
        f"reconstruye todo el índice."
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
    doc_id = compute_doc_id(texto_crudo)

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
    assert compute_doc_id(recuperado["text"]) == doc_id
