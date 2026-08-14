# -*- coding: utf-8 -*-
"""
ab_combinado.py — los tres fixes JUNTOS sobre la misma muestra.

Por qué existe: §A.1 (RTL) y §A.2 (empaquetado) se midieron cada uno contra la
base, por separado. Eso no demuestra que la combinación se comporte bien. Los
dos fixes se tocan: RTL cambia dónde cae el punto final, y de ahí dónde segmenta
pysbd y dónde corta el empaquetador. El efecto conjunto puede no ser la suma.

Mide cuatro configuraciones sobre los MISMOS documentos y las mismas páginas
cacheadas, así que cualquier diferencia es de los fixes:

    base            — nada
    solo extracción — RTL + cmap
    solo chunking   — empaquetado
    combinado       — los tres

Criterio de aceptación: ningún documento debe empeorar en `corte_real` respecto
a la base más allá del ruido, y el combinado no debe ser peor que el mejor de
los parciales.

NO escribe nada. Solo lee documents.jsonl y la caché de páginas.

Uso:
    python scripts/ab_combinado.py
    python scripts/ab_combinado.py --cmap vaciar    # opción (b) en vez de (a)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

import chunk as chunker                                    # noqa: E402
from ab_extraccion import MUESTRA, cargar_documentos, paginas_crudas  # noqa: E402
from clasificar_residual import TERMINADORES, clasificar   # noqa: E402
from extractores_datos import _aplicar_limpieza            # noqa: E402
from identity import normalize_text                        # noqa: E402

LIMITE_TOKENS_BGE_M3 = 8192

# `corte_real` mide si el chunk termina en oración cerrada. Es la métrica
# correcta para RTL y para el empaquetado, y la EQUIVOCADA para el cmap: un
# texto descifrado sigue sin puntos en los titulares, así que `corte_real` no
# mejora aunque el documento pase de ilegible a inglés. Para §A.3 hace falta
# medir legibilidad, que es lo que de verdad decide si el chunk se puede
# recuperar. Fracción de vocales del inglés sano medida en el corpus: 0,39.
VOCALES_INGLES = 0.39
_VOC = frozenset("aeiouAEIOU")


def legibilidad(texto: str) -> float | None:
    """Cercanía al perfil de vocales del inglés sano, en [0, 1]."""
    letras = [c for c in texto if "a" <= c.lower() <= "z"]
    if len(letras) < 50:
        return None
    frac = sum(1 for c in letras if c in _VOC) / len(letras)
    return max(0.0, 1 - abs(frac - VOCALES_INGLES) / VOCALES_INGLES)

# El documento del cmap roto no estaba en la muestra de extracción: se añade
# aquí porque es el único del corpus al que aplica §A.3.
MUESTRA_COMBINADA = sorted(set(MUESTRA) | {"F3-CEOBS-030"})


def construir(docs, paginas, rtl: bool, cmap: str | None,
              extender: bool) -> tuple[list[dict], dict]:
    chunks = []
    diag = {"rtl": 0, "cmap_corregidas": 0, "cmap_vaciadas": 0}
    for doc_id, doc in docs.items():
        texto, extra = _aplicar_limpieza(paginas[doc_id], rtl=rtl, cmap=cmap)
        diag["rtl"] += extra.get("rtl_puntuacion_movida", 0)
        diag["cmap_corregidas"] += extra.get("cmap_lineas_corregidas", 0)
        diag["cmap_vaciadas"] += extra.get("cmap_lineas_vaciadas", 0)
        texto = normalize_text(texto)
        chunks.extend(chunker.chunkear_documento(
            dict(doc, text=texto), extender_colgantes=extender))
    return chunks, diag


def medir(chunks: list[dict]) -> dict:
    total = corte = 0
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
            corte += 1
            fila[1] += 1
    techo = int(chunker.OBJETIVO_PALABRAS * chunker.FACTOR_TECHO_COLGANTE)
    return {
        "chunks": total,
        "corte_real": corte,
        "pct": round(corte / total * 100, 1) if total else 0.0,
        "pal_media": round(statistics.mean(palabras), 1) if palabras else 0,
        "pal_max": max(palabras) if palabras else 0,
        "tok_max": max(tokens) if tokens else 0,
        "sobre_bge": sum(1 for t in tokens if t > LIMITE_TOKENS_BGE_M3),
        "sobre_techo": sum(1 for p in palabras if p > techo),
        "por_doc": por_doc,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cmap", choices=("descifrar", "vaciar"),
                    default="descifrar")
    ap.add_argument("--docs", nargs="*", default=MUESTRA_COMBINADA)
    args = ap.parse_args()

    docs = cargar_documentos(set(args.docs))
    paginas = paginas_crudas(docs)
    print(f"Muestra: {len(docs)} documentos   (cmap: {args.cmap})\n")

    configs = [
        ("base",            False, None,       False),
        ("solo extracción",  True, args.cmap,  False),
        ("solo chunking",   False, None,        True),
        ("COMBINADO",        True, args.cmap,   True),
    ]

    resultados = []
    for nombre, rtl, cmap, extender in configs:
        chunks, diag = construir(docs, paginas, rtl, cmap, extender)
        resultados.append((nombre, medir(chunks), diag))

    base = resultados[0][1]
    print("=" * 84)
    print("EFECTO CONJUNTO")
    print("=" * 84)
    print(f"{'configuración':<18} {'chunks':>7} {'corte_real':>18} "
          f"{'pal.med':>8} {'pal.max':>8} {'tok.max':>8} {'>BGE':>5}")
    for nombre, m, _ in resultados:
        d = m["corte_real"] - base["corte_real"]
        marca = "" if nombre == "base" else f" ({d:+d})"
        print(f"{nombre:<18} {m['chunks']:>7} "
              f"{m['corte_real']:>6} {m['pct']:>5.1f}%{marca:<7} "
              f"{m['pal_media']:>8} {m['pal_max']:>8} {m['tok_max']:>8} "
              f"{m['sobre_bge']:>5}")

    print("\nDiagnóstico de los fixes de extracción:")
    for nombre, _, diag in resultados:
        if diag["rtl"] or diag["cmap_corregidas"] or diag["cmap_vaciadas"]:
            print(f"  {nombre:<18} rtl={diag['rtl']}  "
                  f"cmap_corregidas={diag['cmap_corregidas']}  "
                  f"cmap_vaciadas={diag['cmap_vaciadas']}")

    # ¿Es el combinado al menos tan bueno como el mejor parcial? Se excluye
    # CEOBS-030: su texto CAMBIA entre configuraciones, así que sus chunks no
    # son comparables uno a uno, y `corte_real` no sabe medir su fix.
    def sin_ceobs(m):
        return sum(f[1] for d, f in m["por_doc"].items() if d != "F3-CEOBS-030")

    mejor_parcial = min(sin_ceobs(r[1]) for r in resultados[1:3])
    comb = sin_ceobs(resultados[3][1])
    print(f"\nExcluyendo F3-CEOBS-030 — mejor parcial: {mejor_parcial}   "
          f"combinado: {comb}   {'OK' if comb <= mejor_parcial else 'REGRESIÓN'}")

    # §A.3 con su métrica propia.
    print("\n" + "=" * 84)
    print("F3-CEOBS-030 — legibilidad, que es lo que `corte_real` no ve")
    print("=" * 84)
    for nombre, rtl, cmap, extender in configs:
        texto, extra = _aplicar_limpieza(paginas["F3-CEOBS-030"],
                                         rtl=rtl, cmap=cmap)
        texto = normalize_text(texto)
        leg = legibilidad(texto)
        print(f"  {nombre:<18} legibilidad {leg:.3f}   "
              f"{len(texto.split()):>7,} palabras   "
              f"corregidas={extra.get('cmap_lineas_corregidas', 0)} "
              f"vaciadas={extra.get('cmap_lineas_vaciadas', 0)}"
              + ("   [salvaguarda 40% ACTIVADA]"
                 if extra.get("limpieza_omitida") else ""))

    print("\n" + "=" * 84)
    print("POR DOCUMENTO — corte_real, base vs COMBINADO")
    print("=" * 84)
    final = resultados[3][1]
    print(f"{'documento':<17} {'lang':<5} {'base':>13} {'combinado':>11} {'cambio':>10}")
    peores = []
    for doc_id in sorted(base["por_doc"]):
        na, ca = base["por_doc"][doc_id]
        nb, cb = final["por_doc"].get(doc_id, [0, 0])
        pa = ca / na * 100 if na else 0.0
        pb = cb / nb * 100 if nb else 0.0
        delta = pb - pa
        if delta > 0.05:
            marca = "  EMPEORA"
            peores.append((doc_id, delta))
        elif delta < -0.05:
            marca = "  mejora"
        else:
            marca = ""
        print(f"{doc_id:<17} {str(docs[doc_id].get('lang')):<5} "
              f"{ca:>5}/{na:<4} {pa:>5.1f}% {pb:>10.1f}% {delta:>+8.1f}pp{marca}")

    print(f"\nDocumentos que empeoran: {len(peores)} de {len(base['por_doc'])}")
    for doc_id, delta in sorted(peores, key=lambda x: -x[1]):
        print(f"  {doc_id:<17} {delta:+.1f}pp")


if __name__ == "__main__":
    main()
