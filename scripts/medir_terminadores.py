"""
medir_terminadores.py — Qué porcentaje de chunks NO cierra una oración.

POR QUÉ ESTA MÉTRICA
Un chunk que termina a media oración casi siempre significa que la unidad que
recibió el chunker traía boilerplate pegado: el final de un párrafo real más el
encabezado de la página siguiente, o el pie del sitio web. El chunker nunca
parte una unidad, así que el número mide la calidad de la EXTRACCIÓN, no la de
la fragmentación.

Base medida antes de limpiar el boilerplate (corrida del 2026-08-12):
    pdf   68.516 chunks, 73,3% sin terminador
    json   4.103 chunks, 24,8% sin terminador

Los formatos de registro (csv, xlsx, pbf) salen altísimos por construcción: una
línea "columna: valor | columna: valor" no termina en punto y no debe hacerlo.
Se informan aparte y no cuentan para el diagnóstico.

Uso:
    python scripts/medir_terminadores.py chunks.jsonl
    python scripts/medir_terminadores.py chunks.jsonl --guardar data/base_terminadores.json
    python scripts/medir_terminadores.py chunks.jsonl --comparar data/base_terminadores.json
"""

import argparse
import collections
import json
from pathlib import Path

# Cierres de oración válidos. Incluye comillas y paréntesis de cierre porque
# una cita bien terminada acaba en ." o .) y no es un corte a media frase.
TERMINADORES = set(".!?;:\"')]}»…”’")

# Formatos de prosa: los únicos donde la métrica significa algo.
FORMATOS_PROSA = {"pdf", "json", "txt"}


def medir(ruta: Path) -> dict:
    por_formato = collections.Counter()
    sin_terminador = collections.Counter()
    por_doc = collections.defaultdict(lambda: [0, 0])
    formato_de_doc = {}

    with ruta.open(encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            r = json.loads(linea)
            formato = r["formato"]
            texto = (r.get("texto") or "").rstrip()

            por_formato[formato] += 1
            formato_de_doc[r["doc_id"]] = formato
            por_doc[r["doc_id"]][0] += 1
            if not texto or texto[-1] not in TERMINADORES:
                sin_terminador[formato] += 1
                por_doc[r["doc_id"]][1] += 1

    # Documentos de prosa con suficientes chunks como para que el ratio hable.
    con_tres = malos = 0
    for doc, (n, s) in por_doc.items():
        if formato_de_doc[doc] in FORMATOS_PROSA and n >= 3:
            con_tres += 1
            if s > n / 2:
                malos += 1

    return {
        "por_formato": {
            f: {"chunks": por_formato[f],
                "sin_terminador": sin_terminador[f],
                "pct": round(sin_terminador[f] / por_formato[f] * 100, 1)}
            for f in sorted(por_formato)
        },
        "documentos_prosa_3_chunks": con_tres,
        "documentos_mayoria_sin_terminador": malos,
    }


def imprimir(datos: dict, ruta: Path) -> None:
    print("=" * 66)
    print(f"CHUNKS SIN CIERRE DE ORACIÓN — {ruta}")
    print("=" * 66)
    print(f"{'formato':<10} {'chunks':>9} {'sin term':>9} {'%':>8}")
    for formato, d in sorted(datos["por_formato"].items(),
                             key=lambda kv: -kv[1]["chunks"]):
        marca = "" if formato in FORMATOS_PROSA else "   (registro)"
        print(f"{formato:<10} {d['chunks']:>9} {d['sin_terminador']:>9} "
              f"{d['pct']:>7.1f}%{marca}")

    prosa = [d for f, d in datos["por_formato"].items() if f in FORMATOS_PROSA]
    n = sum(d["chunks"] for d in prosa)
    s = sum(d["sin_terminador"] for d in prosa)
    if n:
        print(f"{'PROSA':<10} {n:>9} {s:>9} {s / n * 100:>7.1f}%")

    con_tres = datos["documentos_prosa_3_chunks"]
    malos = datos["documentos_mayoria_sin_terminador"]
    print(f"\ndocumentos de prosa con >=3 chunks   : {con_tres}")
    if con_tres:
        print(f"  con >50% de chunks sin terminador  : {malos} "
              f"({malos / con_tres * 100:.0f}%)")


def comparar(antes: dict, ahora: dict) -> None:
    print("\n" + "=" * 66)
    print("ANTES vs AHORA")
    print("=" * 66)
    print(f"{'formato':<10} {'antes':>9} {'ahora':>9} {'cambio':>10}")
    formatos = sorted(set(antes["por_formato"]) | set(ahora["por_formato"]))
    for formato in formatos:
        a = antes["por_formato"].get(formato, {}).get("pct")
        b = ahora["por_formato"].get(formato, {}).get("pct")
        if a is None or b is None:
            continue
        flecha = "mejora" if b < a else ("igual" if b == a else "EMPEORA")
        print(f"{formato:<10} {a:>8.1f}% {b:>8.1f}% {b - a:>+8.1f}pp  {flecha}")

    a = antes["documentos_mayoria_sin_terminador"]
    b = ahora["documentos_mayoria_sin_terminador"]
    print(f"\ndocumentos con mayoría de chunks rotos: {a} -> {b} ({b - a:+d})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("chunks", type=Path, nargs="?", default=Path("chunks.jsonl"))
    ap.add_argument("--guardar", type=Path, default=None,
                    help="escribe la medición a un JSON para comparar después")
    ap.add_argument("--comparar", type=Path, default=None,
                    help="compara contra una medición guardada")
    args = ap.parse_args()

    datos = medir(args.chunks)
    imprimir(datos, args.chunks)

    if args.comparar and args.comparar.exists():
        comparar(json.loads(args.comparar.read_text(encoding="utf-8")), datos)

    if args.guardar:
        args.guardar.parent.mkdir(parents=True, exist_ok=True)
        args.guardar.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"\nmedición guardada en {args.guardar}")


if __name__ == "__main__":
    main()
