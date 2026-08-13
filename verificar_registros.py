#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verificar_registros.py — Comprueba la atomicidad de los registros en un
documents.jsonl.

QUÉ COMPRUEBA
En los formatos de datos de registro (pbf, xlsx, csv) el texto se serializa
como "campo: valor | campo: valor", UNA línea por registro. Esa invariante es
la que sostiene toda la estrategia de chunking: el chunker corta entre líneas,
así que si una línea no es un registro completo, el corte produce fragmentos
mutilados y se incumple el §3.3 del reto.

La invariante se rompe cuando un VALOR trae saltos de línea internos (celdas
con texto envuelto, popups de PBF con listas de viñetas). El síntoma es una
línea que no empieza por "campo:".

USO
    python verificar_registros.py data/documents_data.jsonl
    python verificar_registros.py antes.jsonl --comparar despues.jsonl
    python verificar_registros.py data.jsonl --ejemplos 10 --json informe.json

Devuelve código de salida 1 si encuentra líneas huérfanas, para poder
encadenarlo en un script de validación.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Formatos serializados como registros. El resto (json de prosa, pdf, imagen)
# no tiene esta invariante y se ignora.
FORMATOS_REGISTRO = {"pbf", "xlsx", "csv"}

# Una línea válida empieza por un nombre de campo seguido de dos puntos.
# El nombre puede llevar espacios, puntos y guiones: en el corpus existen
# "au_area km", "au_invest. with presence", "b_ADM1_PT".
# El límite de 40 caracteres evita que una frase larga con dos puntos a
# mitad (p.ej. un título "...personal health records: Proof of concept")
# se confunda con un campo.
PREFIJO_CAMPO = re.compile(r"^[\w\u00C0-\u024F\.\- ]{1,40}:")


def analizar(ruta: Path, formatos: set[str]) -> dict:
    """Recorre el JSONL y cuenta líneas huérfanas por formato y documento."""
    informe = {
        "archivo": str(ruta),
        "docs_revisados": 0,
        "lineas_totales": 0,
        "lineas_huerfanas": 0,
        "por_formato": defaultdict(lambda: {"docs": 0, "lineas": 0, "huerfanas": 0}),
        "peores_docs": [],
        "ejemplos": [],
    }

    with ruta.open(encoding="utf-8") as f:
        for n_linea, cruda in enumerate(f, 1):
            cruda = cruda.strip()
            if not cruda:
                continue
            try:
                doc = json.loads(cruda)
            except json.JSONDecodeError as e:
                print(f"  aviso: línea {n_linea} del JSONL no es JSON válido ({e})",
                      file=sys.stderr)
                continue

            formato = (doc.get("formato") or "").lower()
            if formato not in formatos:
                continue

            lineas = [l for l in (doc.get("text") or "").split("\n") if l.strip()]
            if not lineas:
                continue

            huerfanas = [l for l in lineas if not PREFIJO_CAMPO.match(l)]

            informe["docs_revisados"] += 1
            informe["lineas_totales"] += len(lineas)
            informe["lineas_huerfanas"] += len(huerfanas)

            f_stats = informe["por_formato"][formato]
            f_stats["docs"] += 1
            f_stats["lineas"] += len(lineas)
            f_stats["huerfanas"] += len(huerfanas)

            if huerfanas:
                informe["peores_docs"].append({
                    "doc_id": doc.get("doc_id"),
                    "fuente": doc.get("fuente"),
                    "formato": formato,
                    "huerfanas": len(huerfanas),
                    "lineas": len(lineas),
                    "pct": round(len(huerfanas) / len(lineas) * 100, 1),
                })
                for l in huerfanas[:3]:
                    informe["ejemplos"].append({
                        "fuente": doc.get("fuente"),
                        "linea": l[:160],
                    })

    informe["por_formato"] = dict(informe["por_formato"])
    informe["peores_docs"].sort(key=lambda d: -d["huerfanas"])
    return informe


def imprimir(informe: dict, n_ejemplos: int) -> None:
    total = informe["lineas_totales"]
    huer = informe["lineas_huerfanas"]
    pct = huer / total * 100 if total else 0.0

    print(f"\n archivo: {informe['archivo']}")
    print(f" documentos de registro revisados: {informe['docs_revisados']}")
    print(f" líneas totales: {total}")
    print(f" líneas huérfanas: {huer} ({pct:.2f}%)")

    if informe["por_formato"]:
        print(f"\n {'formato':10s}{'docs':>7}{'líneas':>10}{'huérfanas':>12}{'%':>9}")
        for fmt, s in sorted(informe["por_formato"].items()):
            p = s["huerfanas"] / s["lineas"] * 100 if s["lineas"] else 0.0
            print(f" {fmt:10s}{s['docs']:>7}{s['lineas']:>10}{s['huerfanas']:>12}{p:>8.1f}%")

    if informe["peores_docs"]:
        print(f"\n documentos afectados ({len(informe['peores_docs'])}), peores primero:")
        for d in informe["peores_docs"][:10]:
            fuente = (d["fuente"] or "")[-58:]
            print(f"   {d['huerfanas']:>5}/{d['lineas']:<6} ({d['pct']:>5.1f}%)  {fuente}")

    if informe["ejemplos"] and n_ejemplos:
        print(f"\n ejemplos de líneas huérfanas:")
        for e in informe["ejemplos"][:n_ejemplos]:
            print(f"   {e['linea']!r}")

    print()
    if huer == 0:
        print(" OK — todos los registros son atómicos.")
    else:
        print(" FALLO — hay valores con saltos de línea internos.")
        print(" Revisar aplanar_valor() en extractores_datos.py y reingerir.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verifica que cada línea de los formatos de registro sea "
                    "un registro completo.")
    ap.add_argument("jsonl", type=Path, help="documents.jsonl a revisar")
    ap.add_argument("--comparar", type=Path, default=None,
                    help="segundo JSONL para comparar antes/después")
    ap.add_argument("--formatos", default=",".join(sorted(FORMATOS_REGISTRO)),
                    help="formatos a revisar, separados por coma")
    ap.add_argument("--ejemplos", type=int, default=5,
                    help="cuántas líneas huérfanas mostrar (0 para ninguna)")
    ap.add_argument("--json", type=Path, default=None,
                    help="guardar el informe completo en un JSON")
    args = ap.parse_args()

    formatos = {f.strip().lower() for f in args.formatos.split(",") if f.strip()}

    if not args.jsonl.exists():
        print(f"no existe: {args.jsonl}", file=sys.stderr)
        return 2

    informe = analizar(args.jsonl, formatos)
    imprimir(informe, args.ejemplos)

    if args.comparar:
        if not args.comparar.exists():
            print(f"no existe: {args.comparar}", file=sys.stderr)
            return 2
        despues = analizar(args.comparar, formatos)
        imprimir(despues, args.ejemplos)
        antes_h = informe["lineas_huerfanas"]
        desp_h = despues["lineas_huerfanas"]
        print(f" COMPARACIÓN  huérfanas: {antes_h} -> {desp_h} "
              f"({antes_h - desp_h:+d} corregidas)")
        print(f"              líneas totales: {informe['lineas_totales']} -> "
              f"{despues['lineas_totales']}  "
              f"(deben BAJAR: las huérfanas se reabsorben en su registro)\n")
        informe = {"antes": informe, "despues": despues}
        codigo = 0 if desp_h == 0 else 1
    else:
        codigo = 0 if informe["lineas_huerfanas"] == 0 else 1

    if args.json:
        args.json.write_text(json.dumps(informe, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f" informe guardado en {args.json}\n")

    return codigo


if __name__ == "__main__":
    sys.exit(main())
