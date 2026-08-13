# -*- coding: utf-8 -*-
"""
Pruebas de encode_index.py que NO necesitan el modelo.

El encodeo en sí se valida con scripts/verificar_indice.py sobre un índice real.
Aquí va la lógica que decide QUÉ se encodea y EN QUÉ ORDEN, que es donde está
el riesgo de desalinear el mapeo 1:1 sin que salte ningún error.
"""

import json

import pytest

from encode_index import (
    DIMENSION,
    Parcial,
    es_catalogo_masivo,
    huella_orden,
    indice_ordenado,
    leer_en_offset,
    muestrear_estratificado,
    texto_a_encodear,
)


def escribir_chunks(tmp_path, chunks):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ruta = tmp_path / "chunks.jsonl"
    with ruta.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return ruta


def chunk(doc_id, posicion, **extra):
    base = {
        "doc_id": doc_id,
        "chunk_id": f"{doc_id}_c{posicion:03d}",
        "fuente": f"{doc_id}.pdf",
        "formato": "pdf",
        "fenomeno": 1,
        "posicion": posicion,
        "num_tokens": 10,
        "texto": f"texto de {doc_id} {posicion}",
        "num_words": 5,
        "lang": "es",
        "contexto": "",
    }
    base.update(extra)
    return base


# --- Orden -----------------------------------------------------------------

def test_orden_numerico_no_lexicografico(tmp_path):
    """
    El caso que rompe ordenar por chunk_id: hay documentos con 3.826 chunks, así
    que conviven `_c999` y `_c1000`, y como string `_c1000` va ANTES que `_c999`.
    """
    ruta = escribir_chunks(tmp_path, [chunk("D", p) for p in (1000, 999, 1)])
    entradas = indice_ordenado(ruta)
    assert [e[1] for e in entradas] == [1, 999, 1000]


def test_orden_independiente_del_orden_del_archivo(tmp_path):
    """Reescribir chunks.jsonl en otro orden no puede cambiar el índice."""
    chunks = [chunk("B", 0), chunk("A", 1), chunk("A", 0), chunk("C", 2)]
    a = indice_ordenado(escribir_chunks(tmp_path / "a", chunks))
    b = indice_ordenado(escribir_chunks(tmp_path / "b", list(reversed(chunks))))
    assert [(e[0], e[1]) for e in a] == [(e[0], e[1]) for e in b]
    assert huella_orden(a) == huella_orden(b)


def test_huella_distingue_ordenes_distintos(tmp_path):
    ruta = escribir_chunks(tmp_path, [chunk("A", 0), chunk("A", 1)])
    entradas = indice_ordenado(ruta)
    assert huella_orden(entradas) != huella_orden(list(reversed(entradas)))


def test_offsets_apuntan_al_chunk_correcto(tmp_path):
    """El offset es lo que garantiza que el metadato escrito es el del vector."""
    chunks = [chunk("B", 0), chunk("A", 7), chunk("A", 0)]
    ruta = escribir_chunks(tmp_path, chunks)
    entradas = indice_ordenado(ruta)
    with ruta.open("rb") as f:
        leidos = [leer_en_offset(f, e[2]) for e in entradas]
    assert [(c["doc_id"], c["posicion"]) for c in leidos] == [("A", 0), ("A", 7), ("B", 0)]


def test_lineas_en_blanco_no_generan_entradas(tmp_path):
    ruta = tmp_path / "chunks.jsonl"
    ruta.write_text(json.dumps(chunk("A", 0)) + "\n\n" + json.dumps(chunk("A", 1)) + "\n",
                    encoding="utf-8")
    assert len(indice_ordenado(ruta)) == 2


def test_utf8_no_desplaza_offsets(tmp_path):
    """Los offsets son en BYTES; un título con acentos ocupa más que su len()."""
    chunks = [chunk("A", 0, texto="ñandú áéíóú 中文"), chunk("A", 1, texto="segundo")]
    ruta = escribir_chunks(tmp_path, chunks)
    entradas = indice_ordenado(ruta)
    with ruta.open("rb") as f:
        assert leer_en_offset(f, entradas[1][2])["texto"] == "segundo"


# --- Muestreo ---------------------------------------------------------------

def test_muestra_cubre_todos_los_fenomenos(tmp_path):
    chunks = ([chunk(f"A{i}", 0, fenomeno=1) for i in range(100)]
              + [chunk(f"B{i}", 0, fenomeno=2) for i in range(50)]
              + [chunk(f"C{i}", 0, fenomeno=3) for i in range(10)])
    ruta = escribir_chunks(tmp_path, chunks)
    muestra = muestrear_estratificado(indice_ordenado(ruta), ruta, 32)
    with ruta.open("rb") as f:
        fenomenos = {leer_en_offset(f, e[2])["fenomeno"] for e in muestra}
    assert fenomenos == {1, 2, 3}
    assert muestra == sorted(muestra, key=lambda e: (e[0], e[1]))


def test_muestra_es_reproducible(tmp_path):
    chunks = [chunk(f"D{i}", 0) for i in range(100)]
    ruta = escribir_chunks(tmp_path, chunks)
    entradas = indice_ordenado(ruta)
    assert (muestrear_estratificado(entradas, ruta, 20)
            == muestrear_estratificado(entradas, ruta, 20))


# --- Bandera de catálogo masivo --------------------------------------------

@pytest.mark.parametrize("formato,palabras,esperado", [
    ("csv", 1800, True),
    ("xlsx", 1800, True),
    ("pbf", 1800, True),
    ("csv", 250, False),      # el límite es estricto, como en el reporte de chunk.py
    ("csv", 251, True),
    ("pdf", 1800, False),     # prosa larga no es catálogo: no se subdivide por filas
    ("json", 1800, False),
])
def test_catalogo_masivo(formato, palabras, esperado):
    assert es_catalogo_masivo(chunk("A", 0, formato=formato, num_words=palabras)) is esperado


# --- Header opcional --------------------------------------------------------

def test_sin_contexto_el_texto_va_intacto():
    c = chunk("A", 0, contexto="Informe anual")
    assert texto_a_encodear(c, False) == c["texto"]


def test_con_contexto_antepone_header_y_fenomeno():
    c = chunk("A", 0, contexto="Informe anual", fenomeno=2)
    salida = texto_a_encodear(c, True)
    assert salida.startswith("Informe anual — fenómeno 2\n\n")
    assert salida.endswith(c["texto"])


def test_con_contexto_vacio_no_deja_header_colgando():
    c = chunk("A", 0, contexto="", fenomeno=None)
    assert texto_a_encodear(c, True) == c["texto"]


# --- Reanudación ------------------------------------------------------------

def estado(**extra):
    base = {"modelo": "BAAI/bge-m3", "revision": "abc", "con_contexto": False,
            "batch_size": 4, "dispositivo": "cpu", "huella_orden": "h", "total": 100}
    base.update(extra)
    return base


def test_reanudar_retrocede_al_batch_completo(tmp_path):
    """
    Reanudar a mitad de batch cambiaría la composición del batch, y con ella el
    padding y los últimos bits de los vectores. Se retrocede siempre.
    """
    p = Parcial(tmp_path)
    p.crear(estado())
    p.vectores.write_bytes(b"\x00" * (DIMENSION * 4 * 6))       # 6 vectores
    p.metadata.write_bytes(b'{"a":1}\n' * 6)
    assert p.reanudar(estado(), batch_size=4) == 4               # no 6
    assert p.vectores.stat().st_size == DIMENSION * 4 * 4
    assert sum(1 for _ in p.metadata.open("rb")) == 4


def test_reanudar_descarta_escrituras_a_medias(tmp_path):
    """Un corte puede dejar medio vector y media línea de metadata en disco."""
    p = Parcial(tmp_path)
    p.crear(estado())
    p.vectores.write_bytes(b"\x00" * (DIMENSION * 4 * 5 + 17))   # 5 vectores y pico
    p.metadata.write_bytes(b'{"a":1}\n' * 5 + b'{"a":')
    assert p.reanudar(estado(), batch_size=4) == 4
    assert p.vectores.stat().st_size % (DIMENSION * 4) == 0
    assert p.metadata.read_bytes().endswith(b"\n")


def test_reanudar_se_niega_si_cambio_el_corpus(tmp_path):
    p = Parcial(tmp_path)
    p.crear(estado())
    with pytest.raises(SystemExit, match="huella_orden"):
        p.reanudar(estado(huella_orden="otra"), batch_size=4)


@pytest.mark.parametrize("clave,valor", [
    ("batch_size", 8), ("revision", "def"), ("con_contexto", True), ("dispositivo", "cuda"),
])
def test_reanudar_se_niega_si_cambio_la_configuracion(tmp_path, clave, valor):
    p = Parcial(tmp_path)
    p.crear(estado())
    with pytest.raises(SystemExit, match=clave):
        p.reanudar(estado(**{clave: valor}), batch_size=4)


def test_reanudar_sin_parcial_falla_claro(tmp_path):
    with pytest.raises(SystemExit, match="reanudar"):
        Parcial(tmp_path).reanudar(estado(), batch_size=4)
