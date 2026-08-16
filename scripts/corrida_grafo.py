"""
corrida_grafo.py — pipeline mínimo para grafo + evidencia + resultados.

Flujo:
  1) Construye el grafo de conocimiento a partir de chunks reales o demo.
  2) Exporta GraphML + tripletas JSONL bajo entrega/base_vectorial/grafo/.
  3) Si el índice vectorial real existe, dispara entrega/generador.py para
     producir resultados.jsonl y la evidencia paralela del grafo.

Uso:
  python scripts/corrida_grafo.py
  python scripts/corrida_grafo.py --chunks data/chunks.jsonl
  python scripts/corrida_grafo.py --sin-generador
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GRAPH_DIR = RAIZ / "entrega" / "base_vectorial" / "grafo"
INDEX_DIR = RAIZ / "entrega" / "base_vectorial" / "encoder_bge-m3"
GENERATOR = RAIZ / "entrega" / "generador.py"
BUILDER = RAIZ / "build_knowledge_graph.py"


def ensure_demo_chunks() -> Path:
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    demo = GRAPH_DIR / "demo_chunks.jsonl"
    if not demo.exists():
        demo.write_text(
            "\n".join([
                json.dumps({
                    "doc_id": "F1-AIINDEX-001",
                    "chunk_id": "F1-AIINDEX-001_c000",
                    "fuente": "F1_IA/ai_index/intro.json",
                    "fenomeno": 1,
                    "texto": "Estados Unidos desarrolla sistemas autónomos para defensa. Colombia regula el uso de estos sistemas.",
                }, ensure_ascii=False),
                json.dumps({
                    "doc_id": "F2-ESA-005",
                    "chunk_id": "F2-ESA-005_c000",
                    "fuente": "F2_Seguridad_Entorno_Espacial/ESA_Space_Debris/reportes.json",
                    "fenomeno": 2,
                    "texto": "La Fuerza Aérea coordina con la ONU para mitigar riesgos espaciales y la basura orbital.",
                }, ensure_ascii=False),
                json.dumps({
                    "doc_id": "F3-COL-010",
                    "chunk_id": "F3-COL-010_c000",
                    "fuente": "F3_Territorio_Colombia/observatorio.json",
                    "fenomeno": 3,
                    "texto": "Colombia afecta la gobernanza territorial y la seguridad regional mediante decisiones de política pública.",
                }, ensure_ascii=False),
            ]) + "\n",
            encoding="utf-8",
        )
    return demo


def build_graph(chunks_path: Path):
    out_graph = GRAPH_DIR / "grafo.graphml"
    out_triplets = GRAPH_DIR / "tripletas.jsonl"
    cmd = [
        sys.executable,
        str(BUILDER),
        str(chunks_path),
        "--output",
        str(out_graph),
        "--triplets",
        str(out_triplets),
    ]
    subprocess.run(cmd, cwd=RAIZ, check=True)
    print(f"Grafo exportado en {out_graph}")
    print(f"Tripletas exportadas en {out_triplets}")


def run_generador():
    index = INDEX_DIR / "index.faiss"
    metadata = INDEX_DIR / "metadata.jsonl"
    if not index.exists() or not metadata.exists():
        print("No se encontró el índice vectorial real; se omite la generación de resultados.jsonl.")
        return

    cmd = [sys.executable, str(GENERATOR)]
    subprocess.run(cmd, cwd=RAIZ, check=True)
    print(f"Resultados exportados en {RAIZ / 'entrega' / 'resultados.jsonl'}")
    evidencia = GRAPH_DIR / "evidencia_consultas.jsonl"
    if evidencia.exists():
        print(f"Evidencia del grafo exportada en {evidencia}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=RAIZ / "data" / "chunks.jsonl", help="Ruta a chunks.jsonl reales")
    parser.add_argument("--sin-generador", action="store_true", help="No ejecuta el generador de resultados")
    args = parser.parse_args()

    chunks_path = args.chunks
    if not chunks_path.exists():
        print(f"No se encontró {chunks_path}; usando demo_chunks.jsonl.")
        chunks_path = ensure_demo_chunks()

    build_graph(chunks_path)
    if not args.sin_generador:
        run_generador()

    print("Pipeline del grafo finalizado.")


if __name__ == "__main__":
    main()
