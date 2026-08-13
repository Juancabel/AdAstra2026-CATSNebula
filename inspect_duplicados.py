"""
inspect_duplicados.py — Revisa los doc_id repetidos de un JSONL.

Un doc_id repetido significa contenido idéntico en archivos distintos. No es
un error del pipeline, pero SÍ es una decisión pendiente: si dos documentos
idénticos entran al índice, pueden ocupar dos de los tres slots del top-3
con la misma información, y eso cuesta F1@3.

Uso:
    python inspect_duplicados.py data/documents_data.jsonl
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def inspeccionar(ruta: str) -> None:
    grupos = defaultdict(list)

    with Path(ruta).open(encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            doc = json.loads(linea)
            grupos[doc["doc_id"]].append(doc)

    duplicados = {k: v for k, v in grupos.items() if len(v) > 1}

    print("=" * 68)
    print(f"DUPLICADOS DE CONTENIDO — {ruta}")
    print("=" * 68)
    print(f"Documentos totales : {sum(len(v) for v in grupos.values())}")
    print(f"doc_id únicos      : {len(grupos)}")
    print(f"Grupos duplicados  : {len(duplicados)}")
    print(f"Copias sobrantes   : {sum(len(v) - 1 for v in duplicados.values())}")

    if not duplicados:
        print("\nSin duplicados.")
        return

    # Agrupar por patrón para ver si hay una causa sistemática
    por_formato = defaultdict(int)
    por_institucion = defaultdict(int)

    for doc_id, docs in sorted(duplicados.items(), key=lambda kv: -len(kv[1])):
        por_formato[docs[0]["formato"]] += 1
        inst = "/".join(docs[0]["fuente"].split("/")[:2])
        por_institucion[inst] += 1

    print("\nPor formato:")
    for f, n in sorted(por_formato.items(), key=lambda kv: -kv[1]):
        print(f"  {f:<10} {n:>4} grupos")

    print("\nPor institución:")
    for i, n in sorted(por_institucion.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {i}")

    print(f"\n{'-' * 68}")
    print("Detalle (grupos más grandes primero):")
    print(f"{'-' * 68}")

    for doc_id, docs in sorted(duplicados.items(), key=lambda kv: -len(kv[1])):
        muestra = (docs[0].get("text") or "")[:110].replace("\n", " ")
        print(f"\n{doc_id}  ({len(docs)} copias, {docs[0]['formato']})")
        print(f"  título: {docs[0].get('title')}")
        print(f"  texto : {muestra}...")
        for d in docs:
            print(f"    - {d['fuente']}")

    print(f"\n{'=' * 68}")
    print("DECISIÓN PENDIENTE DEL EQUIPO")
    print("=" * 68)
    print(
        "Si dos documentos idénticos quedan indexados, pueden ocupar dos de\n"
        "los tres slots del top-3 con la misma información. Opciones:\n"
        "  A) Indexar todos  — el ground truth podría referenciar cualquiera\n"
        "  B) Quedarse con el primero por orden de 'fuente' (determinista)\n"
        "  C) Indexar todos, pero deduplicar por doc_id al construir el top-3\n"
        "\nLa C suele ser la mejor: no se pierde cobertura de 'fuente' y el\n"
        "top-3 no gasta slots repetidos. Es decisión de D en retrieve.py."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python inspect_duplicados.py <archivo.jsonl>")
        sys.exit(1)
    inspeccionar(sys.argv[1])
