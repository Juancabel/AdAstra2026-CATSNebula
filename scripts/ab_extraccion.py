# -*- coding: utf-8 -*-
"""
ab_extraccion.py — A/B de un fix de extracción sobre una MUESTRA de documentos.

Por qué existe: la re-ingesta completa tarda ~70 min e invalida la caché de
chunking (y con ella la corrida de embeddings). Este script mide si un fix vale
esa reingesta ANTES de lanzarla, sobre 10-20 documentos representativos.

Cómo evita mentir en la comparación:

  * Las páginas crudas del PDF se extraen UNA vez y se cachean. Las dos ramas
    (antes/después) parten exactamente del mismo texto, así que la diferencia
    medida es del fix y no de la extracción.
  * La rama "antes" se compara contra el `text` que ya está en documents.jsonl.
    Si no coincide, el arnés no es fiel al pipeline y lo dice en vez de dar un
    número tranquilizador.
  * Todo lo demás del documento (lang, formato, fenómeno, título) se toma de
    documents.jsonl: solo cambia `text`.
  * El chunking usa chunk.py de verdad, y la medición scripts/medir_terminadores
    y scripts/clasificar_residual, los mismos de la sesión de line-wraps.

NO escribe nada en data/ ni toca la caché de chunking. Solo lee.

Uso:
    python scripts/ab_extraccion.py                      # muestra por defecto
    python scripts/ab_extraccion.py --docs F1-AIINDEX-001 F3-RESDAL-005
    python scripts/ab_extraccion.py --ejemplos 15        # ver qué se aisló
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

import chunk as chunker                                    # noqa: E402
from clasificar_residual import TERMINADORES, clasificar   # noqa: E402
from extractores_datos import _aplicar_limpieza            # noqa: E402
from identity import normalize_text                        # noqa: E402

DOCUMENTS = RAIZ / "data" / "documents.jsonl"
CACHE = RAIZ / "data" / "cache_paginas_muestra.json"

# Muestra por defecto: elegida por tasa de 'corte_real' sobre la corrida
# actual (scripts/clasificar_residual.py por documento), cubriendo los tres
# fenómenos y cinco idiomas. Los controles son documentos ya sanos: si el fix
# los empeora, el fix está mal.
MUESTRA = [
    # --- peores: el síntoma que se quiere arreglar --------------------------
    "F2-UNOOSA-013",   # ar, 32 pág — puntuación RTL desplazada (confirmado)
    "F3-ALERTAS-365",  # es, informe con mucha tabla
    "F2-UNOOSA-025",   # ar, 103 pág — RTL parcial (mezcla líneas buenas y malas)
    # Tercer positivo de RTL, añadido al barrer el corpus: sin él la muestra
    # solo tenía documentos de UNOOSA y el fix no se probaba fuera de una
    # fuente. 51 líneas abren con puntuación y 0 cierran con ella.
    "F2-SWF-039",      # ar
    "F3-RESDAL-005",   # es, atlas con fichas
    "F1-CSET-058",     # en, informe largo
    "F3-ALERTAS-368",  # es
    "F2-CSIS-199",     # en, carta abierta
    "F2-SWF-127",      # en, presentación (100% sin terminador por naturaleza)
    # --- el caso de referencia del brief ------------------------------------
    "F1-AIINDEX-001",  # en, "1.1 Publications" pegado a la prosa
    "F1-AIINDEX-020",  # en, 386 pág
    # --- mediana ------------------------------------------------------------
    "F2-SWF-128",      # pt
    "F1-DAIO-035",     # en
    "F3-SIPRI-120",    # en
    "F3-RESDAL-027",   # es
    # --- control: ya sanos, no deben empeorar -------------------------------
    "F1-CSET-124",
    "F1-AIINDEX-046",
    "F2-CSIS-145",
    "F2-SWF-098",
    "F2-SWF-093",
]


def cargar_documentos(doc_ids: set[str]) -> dict[str, dict]:
    docs = {}
    with DOCUMENTS.open(encoding="utf-8") as f:
        for linea in f:
            o = json.loads(linea)
            if o["doc_id"] in doc_ids:
                docs[o["doc_id"]] = o
    faltan = doc_ids - set(docs)
    if faltan:
        raise SystemExit(f"No están en documents.jsonl: {sorted(faltan)}")
    return docs


def paginas_crudas(docs: dict[str, dict]) -> dict[str, list[str]]:
    """
    Texto por página, tal cual sale de PyMuPDF, cacheado en data/.

    Es la parte cara y la que NO cambia entre ramas: cachearla es lo que hace
    que el A/B sea comparable y que iterar sobre la heurística cueste segundos.
    """
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    nuevos = [d for d in docs if d not in cache]

    if nuevos:
        import pymupdf
        for i, doc_id in enumerate(nuevos, 1):
            ruta = RAIZ / "corpus_original" / docs[doc_id]["fuente"]
            print(f"  extrayendo {i}/{len(nuevos)}  {doc_id} …", flush=True)
            pdf = pymupdf.open(ruta.as_posix())
            try:
                cache[doc_id] = [t for t in (p.get_text("text") for p in pdf) if t]
            finally:
                pdf.close()
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  caché de páginas -> {CACHE}")

    return {d: cache[d] for d in docs}


def chunks_de(docs: dict[str, dict], paginas: dict[str, list[str]],
              aislar: bool, unir: bool, rtl: bool = False) -> tuple[list[dict], dict]:
    """Aplica la limpieza (con o sin cada fix), chunkea y devuelve los chunks."""
    salida = []
    diag = {"aisladas": 0, "unidas": 0, "rtl": 0, "textos": {}}
    for doc_id, doc in docs.items():
        texto, extra = _aplicar_limpieza(paginas[doc_id], aislar=aislar,
                                         unir=unir, rtl=rtl)
        diag["rtl"] += extra.get("rtl_puntuacion_movida", 0)
        # ingest_data.py normaliza DESPUÉS de extraer; sin esto el arnés mide un
        # texto que nunca existió (y, en concreto, no colapsa los 3+ saltos que
        # deja el aislado a los 2 que separan párrafo).
        texto = normalize_text(texto)
        diag["aisladas"] += extra.get("limpieza_aisladas", 0)
        diag["unidas"] += extra.get("limpieza_paginas_unidas", 0)
        diag["textos"][doc_id] = texto
        nuevo = dict(doc, text=texto)
        salida.extend(chunker.chunkear_documento(nuevo))
    return salida, diag


def medir(chunks: list[dict]) -> dict:
    """Terminadores + reparto del residual, por documento y en total."""
    total = sin_term = 0
    categorias = {"corte_real": 0, "grafica_o_dato": 0, "nota_pie": 0}
    por_doc: dict[str, list[int]] = {}

    for c in chunks:
        texto = (c["texto"] or "").rstrip()
        fila = por_doc.setdefault(c["doc_id"], [0, 0, 0])
        total += 1
        fila[0] += 1
        if texto and texto.endswith(TERMINADORES):
            continue
        sin_term += 1
        fila[1] += 1
        cat = clasificar(texto)
        if cat in categorias:
            categorias[cat] += 1
            if cat == "corte_real":
                fila[2] += 1

    return {
        "chunks": total,
        "sin_terminador": sin_term,
        "pct_sin_terminador": round(sin_term / total * 100, 1) if total else 0.0,
        "corte_real": categorias["corte_real"],
        "pct_corte_real": round(categorias["corte_real"] / total * 100, 1) if total else 0.0,
        "grafica_o_dato": categorias["grafica_o_dato"],
        "nota_pie": categorias["nota_pie"],
        "por_doc": por_doc,
    }


def comparar(mediciones: list[tuple[str, dict]], docs: dict[str, dict]) -> None:
    """Tabla de todas las variantes contra la base, que es la primera."""
    base_nombre, base = mediciones[0]

    print("\n" + "=" * 78)
    print("MUESTRA — cada fix por separado y combinados")
    print("=" * 78)
    print(f"{'variante':<26} {'chunks':>7} {'sin term':>9} {'corte_real':>12} "
          f"{'graf/dato':>10} {'vs base':>9}")
    for nombre, m in mediciones:
        delta = m["corte_real"] - base["corte_real"]
        marca = "" if nombre == base_nombre else f"{delta:+d}"
        print(f"{nombre:<26} {m['chunks']:>7} "
              f"{m['sin_terminador']:>6} {m['pct_sin_terminador']:>5.1f}% "
              f"{m['corte_real']:>7} {m['pct_corte_real']:>5.1f}% "
              f"{m['grafica_o_dato']:>10} {marca:>9}")

    final_nombre, final = mediciones[-1]
    print(f"\nPor documento — corte_real, '{base_nombre}' vs '{final_nombre}':")
    print(f"{'documento':<17} {'lang':<5} {'antes':>13} {'después':>9} {'cambio':>9}")
    for doc_id in sorted(base["por_doc"]):
        na, _, ca = base["por_doc"][doc_id]
        nb, _, cb = final["por_doc"].get(doc_id, [0, 0, 0])
        pa = ca / na * 100 if na else 0
        pb = cb / nb * 100 if nb else 0
        marca = "" if abs(pb - pa) < 0.05 else ("  mejora" if pb < pa else "  EMPEORA")
        print(f"{doc_id:<17} {str(docs[doc_id].get('lang')):<5} "
              f"{ca:>6}/{na:<4} {pa:>5.1f}% {pb:>8.1f}% {pb - pa:>+7.1f}pp{marca}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", nargs="*", default=MUESTRA)
    ap.add_argument("--ejemplos", type=int, default=8,
                    help="cuántas líneas aisladas mostrar para auditar falsos positivos")
    ap.add_argument("--solo-rtl", action="store_true",
                    help="medir únicamente la corrección de puntuación árabe")
    args = ap.parse_args()

    docs = cargar_documentos(set(args.docs))
    print(f"Muestra: {len(docs)} documentos")
    paginas = paginas_crudas(docs)

    if args.solo_rtl:
        variantes = [
            ("base (corrida actual)", False, False, False),
            ("solo puntuación RTL", False, False, True),
        ]
    else:
        variantes = [
            ("base (corrida actual)", False, False, False),
            ("solo aislar no-prosa", True, False, False),
            ("solo unir páginas", False, True, False),
            ("aislar + unir", True, True, False),
            ("solo puntuación RTL", False, False, True),
        ]

    mediciones = []
    for nombre, aislar, unir, rtl in variantes:
        print(f"\n{nombre} …")
        chunks, diag = chunks_de(docs, paginas, aislar=aislar, unir=unir, rtl=rtl)

        if rtl:
            print(f"  puntuaciones movidas: {diag['rtl']}")

        if not aislar and not unir and not rtl:
            # Fidelidad: la base tiene que reproducir lo que ya hay en
            # documents.jsonl. Si no, el arnés mide otra cosa y el A/B no vale.
            iguales = sum(1 for d, doc in docs.items()
                          if diag["textos"][d] == doc["text"])
            print(f"  fidelidad vs documents.jsonl: {iguales}/{len(docs)} idénticos")
            if iguales != len(docs):
                print(f"  AVISO: no reproduce "
                      f"{[d for d in docs if diag['textos'][d] != docs[d]['text']]}")
        else:
            print(f"  líneas aisladas: {diag['aisladas']}   "
                  f"páginas unidas: {diag['unidas']}")

        mediciones.append((nombre, medir(chunks)))

    comparar(mediciones, docs)

    if args.ejemplos and not args.solo_rtl:
        print("\n" + "=" * 74)
        print("LÍNEAS AISLADAS — auditoría de falsos positivos")
        print("=" * 74)
        from extractores_datos import aislar_lineas_no_prosa
        vistas = 0
        for doc_id in docs:
            _, d = aislar_lineas_no_prosa(paginas[doc_id])
            for linea in d["muestra_aisladas"]:
                print(f"  {doc_id:<16} {linea[:78]!r}")
                vistas += 1
                if vistas >= args.ejemplos:
                    return


if __name__ == "__main__":
    main()
