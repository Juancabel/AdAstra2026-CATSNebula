# -*- coding: utf-8 -*-
"""
ab_empaquetado.py — A/B de `chunk.empaquetar(extender_colgantes=)` en muestra.

Arnés distinto al de ab_extraccion.py y a propósito: este fix vive en el
CHUNKING, no en la extracción. Parte del `text` que ya está en
documents.jsonl (Contrato 1) y solo cambia cómo se empaquetan las unidades, así
que no necesita reingerir nada ni tocar la caché de segmentación.

Mide tres cosas, no una:
  1. `corte_real` antes/después — el beneficio que se busca.
  2. distribución de `num_words` / `num_tokens` — el riesgo de inflar chunks.
  3. techo de tokens contra el límite de contexto de BGE-M3 (8192) — chequeo de
     seguridad barato; improbable que un ajuste de empaquetado lo dispare, pero
     un chunk truncado en el encoder se pierde en silencio.

También cronometra la segmentación en frío (pysbd), que es lo caro de un
rechunking completo, y extrapola a los 1.826 documentos del corpus.

NO escribe nada. Solo lee documents.jsonl.

Uso:
    python scripts/ab_empaquetado.py
    python scripts/ab_empaquetado.py --docs F2-UNOOSA-013 F1-AIINDEX-001
    python scripts/ab_empaquetado.py --sin-cronometro
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

import chunk as chunker                                    # noqa: E402
from ab_extraccion import MUESTRA                          # noqa: E402
from clasificar_residual import TERMINADORES, clasificar   # noqa: E402

DOCUMENTS = RAIZ / "data" / "documents.jsonl"

# Límite de contexto de BAAI/bge-m3. Un chunk por encima se trunca al codificar.
LIMITE_TOKENS_BGE_M3 = 8192


def cargar(doc_ids: set[str]) -> dict[str, dict]:
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


def medir(chunks: list[dict]) -> dict:
    total = corte_real = 0
    palabras, tokens = [], []
    por_doc: dict[str, list[int]] = {}

    for c in chunks:
        texto = (c["texto"] or "").rstrip()
        fila = por_doc.setdefault(c["doc_id"], [0, 0])
        total += 1
        fila[0] += 1
        palabras.append(c["num_words"])
        tokens.append(c["num_tokens"])
        if texto and texto.endswith(TERMINADORES):
            continue
        if clasificar(texto) == "corte_real":
            corte_real += 1
            fila[1] += 1

    return {
        "chunks": total,
        "corte_real": corte_real,
        "pct_corte_real": round(corte_real / total * 100, 1) if total else 0.0,
        "palabras_media": round(statistics.mean(palabras), 1) if palabras else 0,
        "palabras_mediana": statistics.median(palabras) if palabras else 0,
        "palabras_p95": (sorted(palabras)[int(len(palabras) * 0.95)]
                         if palabras else 0),
        "palabras_max": max(palabras) if palabras else 0,
        "tokens_media": round(statistics.mean(tokens), 1) if tokens else 0,
        "tokens_max": max(tokens) if tokens else 0,
        "sobre_limite_bge": sum(1 for t in tokens if t > LIMITE_TOKENS_BGE_M3),
        "sobre_techo": sum(
            1 for p in palabras
            if p > int(chunker.OBJETIVO_PALABRAS * chunker.FACTOR_TECHO_COLGANTE)
        ),
        "por_doc": por_doc,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", nargs="*", default=MUESTRA)
    ap.add_argument("--sin-cronometro", action="store_true")
    args = ap.parse_args()

    docs = cargar(set(args.docs))
    print(f"Muestra: {len(docs)} documentos")

    # La segmentación NO depende del fix: se calcula una vez y se reutiliza en
    # las dos ramas. Así el A/B mide el empaquetado y nada más, y de paso se
    # cronometra lo que costaría un rechunking en frío.
    print("\nSegmentando (esto es lo caro de un rechunking en frío) …")
    t0 = time.time()
    unidades = {}
    for doc_id, doc in docs.items():
        unidades[doc_id] = chunker.segmentar_documento(doc)
    segundos = time.time() - t0
    palabras_muestra = sum(len((d["text"] or "").split()) for d in docs.values())
    print(f"  {len(docs)} documentos, {palabras_muestra:,} palabras "
          f"en {segundos:.1f}s")

    mediciones = []
    for nombre, extender in (("base (actual)", False), ("extender colgantes", True)):
        chunks = []
        for doc_id, doc in docs.items():
            chunks.extend(chunker.chunkear_documento(
                doc, unidades=unidades[doc_id], extender_colgantes=extender))
        mediciones.append((nombre, medir(chunks)))

    base_nombre, base = mediciones[0]
    print("\n" + "=" * 78)
    print("EFECTO SOBRE corte_real Y SOBRE EL TAMAÑO DE CHUNK")
    print("=" * 78)
    print(f"{'variante':<22} {'chunks':>7} {'corte_real':>13} {'pal.med':>8} "
          f"{'pal.p95':>8} {'pal.max':>8} {'tok.max':>8}")
    for nombre, m in mediciones:
        delta = m["corte_real"] - base["corte_real"]
        marca = "" if nombre == base_nombre else f" ({delta:+d})"
        print(f"{nombre:<22} {m['chunks']:>7} "
              f"{m['corte_real']:>6} {m['pct_corte_real']:>5.1f}%{marca:<7} "
              f"{m['palabras_media']:>8} {m['palabras_p95']:>8} "
              f"{m['palabras_max']:>8} {m['tokens_max']:>8}")

    final_nombre, final = mediciones[-1]
    print(f"\nSeguridad — chunks por encima del contexto de BGE-M3 "
          f"({LIMITE_TOKENS_BGE_M3} tokens):")
    print(f"  base: {base['sobre_limite_bge']}   "
          f"{final_nombre}: {final['sobre_limite_bge']}")

    objetivo = chunker.OBJETIVO_PALABRAS
    techo = int(objetivo * chunker.FACTOR_TECHO_COLGANTE)
    print(f"\nTecho de empaquetado: objetivo {objetivo}, techo al extender "
          f"{techo} ({chunker.FACTOR_TECHO_COLGANTE:g}x)")
    print(f"  chunks por encima del techo — base: {base['sobre_techo']}, "
          f"{final_nombre}: {final['sobre_techo']}")
    print("  (superar el OBJETIVO es normal: una unidad larga cierra chunk por "
          "sí misma\n   y eso ya pasaba antes del fix)")

    print(f"\nPor documento — corte_real, '{base_nombre}' vs '{final_nombre}':")
    print(f"{'documento':<17} {'lang':<5} {'antes':>13} {'después':>9} {'cambio':>9}")
    for doc_id in sorted(base["por_doc"]):
        na, ca = base["por_doc"][doc_id]
        nb, cb = final["por_doc"].get(doc_id, [0, 0])
        pa = ca / na * 100 if na else 0
        pb = cb / nb * 100 if nb else 0
        marca = "" if abs(pb - pa) < 0.05 else ("  mejora" if pb < pa else "  EMPEORA")
        print(f"{doc_id:<17} {str(docs[doc_id].get('lang')):<5} "
              f"{ca:>6}/{na:<4} {pa:>5.1f}% {pb:>8.1f}% {pb - pa:>+7.1f}pp{marca}")

    if not args.sin_cronometro:
        print("\n" + "=" * 78)
        print("RECHUNKING EN FRÍO — proyección al corpus completo")
        print("=" * 78)
        total_palabras = 0
        n_docs = 0
        with DOCUMENTS.open(encoding="utf-8") as f:
            for linea in f:
                o = json.loads(linea)
                n_docs += 1
                total_palabras += len((o.get("text") or "").split())
        factor = total_palabras / palabras_muestra if palabras_muestra else 0
        print(f"  muestra   : {len(docs):>5} docs, {palabras_muestra:>12,} palabras, "
              f"{segundos:>7.1f}s")
        print(f"  corpus    : {n_docs:>5} docs, {total_palabras:>12,} palabras")
        print(f"  factor    : {factor:>7.1f}x")
        print(f"  proyección: {segundos * factor / 60:>7.1f} min de segmentación")
        print("\n  Nota: la proyección escala por palabras, no por documentos. El"
              "\n  empaquetado y la escritura del JSONL van aparte y son baratos.")


if __name__ == "__main__":
    main()
