# -*- coding: utf-8 -*-
"""
revisar_csv.py — Diagnóstico de los documentos CSV antes de decidir la
estrategia de chunking para los datasets bibliométricos de AI_Index.

Reutiliza segmentar_documento() y empaquetar() de chunk.py TAL CUAL están
hoy — no las modifica ni las reimplementa — para medir qué produciría la
rama de "registro" con datos reales, antes de tocar el chunker.

Uso (desde el mismo directorio que chunk.py):
    python revisar_csv.py data/documents.jsonl

Requisito: chunk.py en el mismo directorio o en el PYTHONPATH. Ojo: Python
trae un módulo interno llamado `chunk` en versiones <3.13; al correr este
script desde la carpeta donde vive tu chunk.py, el import local tiene
prioridad, así que no debería haber colisión — pero si ves un error raro
de import, es la primera sospecha.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from chunk import segmentar_documento, empaquetar, OBJETIVO_PALABRAS, MAXIMO_PALABRAS


def cargar_csvs(ruta: Path) -> list[dict]:
    docs = []
    with ruta.open(encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            doc = json.loads(linea)
            if (doc.get("formato") or "").lower() == "csv":
                docs.append(doc)
    return docs


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python revisar_csv.py ruta/a/documents.jsonl")
        sys.exit(1)

    docs = cargar_csvs(Path(sys.argv[1]))
    docs.sort(key=lambda d: len(d.get("text") or ""), reverse=True)

    print(f"CSV en el corpus: {len(docs)}\n")
    print(f"{'doc_id':<18} {'KB texto':>10} {'filas':>8}  fuente")
    print("-" * 95)
    for d in docs:
        texto = d.get("text") or ""
        filas = texto.count("\n") + (1 if texto else 0)
        print(f"{d.get('doc_id', ''):<18} {len(texto) / 1024:>10.1f} {filas:>8}  "
              f"{str(d.get('fuente', ''))[-55:]}")

    print("\n--- Corriendo la lógica actual de chunk.py sobre cada CSV ---\n")
    resultados = []
    t_total0 = time.perf_counter()
    for d in docs:
        texto = d.get("text") or ""
        filas = texto.count("\n") + (1 if texto else 0)
        t0 = time.perf_counter()
        unidades = segmentar_documento(d, MAXIMO_PALABRAS)
        t1 = time.perf_counter()
        chunks = empaquetar(unidades, OBJETIVO_PALABRAS, MAXIMO_PALABRAS, "\n")
        t2 = time.perf_counter()
        resultados.append((d, filas, unidades, chunks, t1 - t0, t2 - t1))
    t_total1 = time.perf_counter()

    for d, filas, unidades, chunks, t_seg, t_emp in resultados[:5]:
        lineas = (d.get("text") or "").splitlines()
        print(f"{d.get('doc_id')}  ({str(d.get('fuente'))[-55:]})")
        print(f"  filas             : {filas}")
        print(f"  unidades atómicas : {len(unidades)}")
        print(f"  chunks resultantes: {len(chunks)}")
        print(f"  tiempo segmentar/empaquetar: {t_seg:.2f}s / {t_emp:.2f}s")
        if lineas:
            print(f"  muestra fila 0    : {lineas[0][:180]!r}")
        if len(lineas) > 1:
            print(f"  muestra fila 1    : {lineas[1][:180]!r}")
        print()

    total_chunks = sum(len(chunks) for _, _, _, chunks, _, _ in resultados)
    print(f"Total chunks de los {len(docs)} CSV con la lógica ACTUAL: {total_chunks}")
    print(f"Tiempo total de segmentación+empaquetado: {t_total1 - t_total0:.2f}s")


if __name__ == "__main__":
    main()
