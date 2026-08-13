# -*- coding: utf-8 -*-
"""
clasificar_residual.py — Reparte los chunks de prosa SIN terminador en
categorías, para distinguir cortes de oración reales de finales que
simplemente no llevan punto por naturaleza (pies de gráfica, ejes, notas).

Uso:  python clasificar_residual.py chunks.jsonl
"""
import json, re, sys
from collections import Counter

FORMATOS_REGISTRO = {"pbf", "xlsx", "csv", "jpg"}
TERMINADORES = (".", "!", "?", "…", "\"", "'", "”", "’", ")", "»", "]")

# nota al pie pegada: "...institutions.5"  (oración COMPLETA + superíndice)
RE_NOTA_PIE = re.compile(r"[.!?…]\s?\d{1,3}$")
# termina en año o rango de años: "...2010–22", "...2003-23", "...2022"
RE_ANIO = re.compile(r"(19|20)\d{2}(\s*[–\-—]\s*(19|20)?\d{2})?$")
RE_NUMERICO = re.compile(r"^[\d.,%–\-—()$]+$")

# fragmentos inequívocos de pie/fuente de gráfica aunque terminen en palabra
MARCADORES_GRAFICA = ("chart:", "source:", "| chart", "index report",
                      "(log scale)", "petaflop")

def cola_es_grafica(texto: str) -> bool:
    cola = " ".join(texto.split()[-20:]).lower()
    if any(m in cola for m in MARCADORES_GRAFICA):
        return True
    toks = texto.split()[-15:]
    if not toks:
        return False
    numericos = sum(1 for t in toks if RE_NUMERICO.match(t))
    return numericos / len(toks) >= 0.4

def clasificar(texto: str) -> str:
    t = texto.rstrip()
    if not t:
        return "vacio"
    if t.endswith(TERMINADORES):
        return "ok"  # no debería entrar aquí, pero por si acaso
    if RE_NOTA_PIE.search(t):
        return "nota_pie"          # oración completa + superíndice de nota
    if RE_ANIO.search(t) or cola_es_grafica(t):
        return "grafica_o_dato"    # pie de figura, eje, fuente: no es prosa
    return "corte_real"            # termina a mitad de oración de verdad

def main():
    buckets = Counter()
    ejemplos = {"nota_pie": [], "grafica_o_dato": [], "corte_real": []}
    with open(sys.argv[1], encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            c = json.loads(linea)
            if (c.get("formato") or "").lower() in FORMATOS_REGISTRO:
                continue
            texto = (c.get("texto") or "").rstrip()
            if not texto or texto.endswith(TERMINADORES):
                continue
            cat = clasificar(texto)
            buckets[cat] += 1
            if len(ejemplos.get(cat, [])) < 4:
                ejemplos[cat].append((c["chunk_id"], texto[-70:]))

    total = sum(buckets.values())
    print(f"Chunks de prosa SIN terminador: {total}\n")
    for cat in ("corte_real", "grafica_o_dato", "nota_pie"):
        n = buckets[cat]
        pct = 100 * n / total if total else 0
        print(f"  {cat:16} {n:6}  ({pct:4.1f}%)")
    print()
    for cat in ("corte_real", "grafica_o_dato", "nota_pie"):
        print(f"--- ejemplos {cat} ---")
        for cid, fin in ejemplos[cat]:
            print(f"  {cid}: ...{fin!r}")
        print()

if __name__ == "__main__":
    main()
