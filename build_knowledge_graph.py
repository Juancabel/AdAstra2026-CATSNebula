from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_graph import build_graph_from_chunks, load_chunks_from_jsonl, save_graph


try:
    import chunk as chunker  # type: ignore
except Exception:  # pragma: no cover
    chunker = None


def load_chunk_rows(path: Path):
    path = path.resolve()
    with path.open("r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows


def chunks_from_documents(documents_path: Path):
    if chunker is None:
        raise RuntimeError("No se pudo importar chunk.py; asegúrate de correr desde la raíz del proyecto.")
    docs = load_chunk_rows(documents_path)
    chunks: list[dict] = []
    for doc in docs:
        if "doc_id" not in doc:
            continue
        chunks.extend(chunker.chunkear_documento(doc))
    return chunks


def iter_chunks(input_path: Path):
    rows = load_chunk_rows(input_path)
    if not rows:
        return []
    if all("chunk_id" in row and ("texto" in row or "text" in row) for row in rows):
        return rows
    if all("doc_id" in row and "text" in row for row in rows):
        return chunks_from_documents(input_path)
    raise ValueError("El archivo de entrada no parece ser chunks.jsonl ni documents.jsonl válidos.")


def main():
    ap = argparse.ArgumentParser(description="Construye un grafo de conocimiento sobre chunks del corpus.")
    ap.add_argument("input", type=Path, nargs="?", default=Path("data/chunks.jsonl"), help="Ruta a chunks.jsonl o documents.jsonl")
    ap.add_argument("--output", type=Path, default=Path("entrega/base_vectorial/grafo/grafo.graphml"), help="Ruta del grafo GraphML")
    ap.add_argument("--triplets", type=Path, default=Path("entrega/base_vectorial/grafo/tripletas.jsonl"), help="Ruta de salida para tripletas JSONL")
    ap.add_argument("--strict", action="store_true", help="Falla si no existe el archivo de entrada o si no se pudieron cargar chunks")
    args = ap.parse_args()

    if not args.input.exists():
        if args.strict:
            raise FileNotFoundError(f"No existe el archivo de entrada: {args.input}")
        demo = Path("entrega/base_vectorial/grafo/demo_chunks.jsonl")
        if not demo.exists():
            demo.parent.mkdir(parents=True, exist_ok=True)
            demo.write_text(
                "\n".join([
                    json.dumps({"doc_id": "F1-AIINDEX-001", "chunk_id": "F1-AIINDEX-001_c000", "fuente": "F1_IA/ai_index/intro.json", "fenomeno": 1, "texto": "Estados Unidos desarrolla sistemas autónomos para defensa. Colombia regula el uso de estos sistemas."}, ensure_ascii=False),
                    json.dumps({"doc_id": "F2-ESA-005", "chunk_id": "F2-ESA-005_c000", "fuente": "F2_Seguridad_Entorno_Espacial/ESA_Space_Debris/reportes.json", "fenomeno": 2, "texto": "La Fuerza Aérea coordina con la ONU para mitigar riesgos espaciales y la basura orbital."}, ensure_ascii=False),
                    json.dumps({"doc_id": "F3-COL-010", "chunk_id": "F3-COL-010_c000", "fuente": "F3_Territorio_Colombia/observatorio.json", "fenomeno": 3, "texto": "Colombia afecta la gobernanza territorial y la seguridad regional mediante decisiones de política pública."}, ensure_ascii=False),
                ]) + "\n",
                encoding="utf-8",
            )
        args.input = demo

    chunks = iter_chunks(args.input)
    graph = build_graph_from_chunks(chunks)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_graph(graph, args.output)

    triplets = []
    for source, target, data in graph.edges(data=True):
        triplets.append({
            "subject": source,
            "relation": data.get("relation", ""),
            "object": target,
            "doc_ids": data.get("doc_ids", ""),
            "chunk_ids": data.get("chunk_ids", ""),
            "evidence": data.get("evidence", ""),
        })

    args.triplets.parent.mkdir(parents=True, exist_ok=True)
    with args.triplets.open("w", encoding="utf-8") as f:
        for item in triplets:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Grafo construido: {graph.number_of_nodes()} nodos, {graph.number_of_edges()} aristas")
    print(f"Salida: {args.output}")
    print(f"Tripletas: {args.triplets}")


if __name__ == "__main__":
    main()
