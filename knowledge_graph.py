from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import networkx as nx


if not hasattr(nx.DiGraph, "write_graphml"):
    def _write_graphml(self, path):
        nx.write_graphml(self, str(path))

    nx.DiGraph.write_graphml = _write_graphml


RELATION_PATTERNS = [
    ("desarrolla", "desarrolla"),
    ("regula", "regula"),
    ("coordina", "coordina"),
    ("afecta", "afecta"),
    ("opera", "opera"),
    ("develops", "desarrolla"),
    ("develop", "desarrolla"),
    ("governs", "regula"),
    ("coordinates", "coordina"),
    ("affects", "afecta"),
    ("operates", "opera"),
]

COUNTRIES_AND_ORGS = {
    "estados unidos",
    "colombia",
    "china",
    "rusia",
    "ee.uu.",
    "onu",
    "nato",
    "unión europea",
    "union europea",
    "fuerza aérea",
    "fuerza aerea",
    "américa latina",
    "america latina",
    "latam",
    "alianza",
    "ministerio de defensa",
    "ministerio de relaciones exteriores",
}

TECH_TERMS = {
    "ia",
    "inteligencia artificial",
    "sistema autónomo",
    "sistema autonomo",
    "algoritmo",
    "algoritmos",
    "software",
    "tecnología",
    "tecnologia",
    "dron",
    "drones",
    "satélite",
    "satelite",
    "nube",
    "ciberseguridad",
    "seguridad espacial",
}

EVENT_TERMS = {
    "cumbre",
    "conferencia",
    "acuerdo",
    "cumbre de seguridad",
    "incidente",
    "guerra",
    "conflicto",
    "proliferación",
    "proliferacion",
    "cooperación",
    "cooperacion",
    "colisión",
    "colision",
}

ENTITY_STOPWORDS = {
    "la", "el", "los", "las", "de", "del", "y", "en", "para", "con",
    "por", "sobre", "como", "según", "segun", "una", "unos", "unas",
    "the", "and", "for", "with", "of", "in", "on", "to", "a", "an"
}


def normalize_entity(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" ,;:.-_()[]{}\n")
    if not text:
        return text
    return text.strip()


def _clean_object(value: str) -> str:
    text = normalize_entity(value)
    if text.lower() in ENTITY_STOPWORDS:
        return ""
    return text


def extract_entities_from_text(text: str) -> list[str]:
    """Extrae entidades mínimas en español/inglés para un grafo de conocimiento.

    El objetivo es detectar nombres de países, organizaciones, tecnologías y
    eventos, evitando ruido excesivo. No pretende reemplazar a un NER de
    producción; simplemente ofrece una capa robusta suficiente para la etapa del
    reto y para la integración gráfica con la base vectorial.
    """
    if not text:
        return []
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    candidates: list[str] = []
    lower_text = normalized.lower()

    for token in sorted(COUNTRIES_AND_ORGS | TECH_TERMS | EVENT_TERMS, key=len, reverse=True):
        if token in lower_text:
            candidates.append(token)

    phrase_pattern = re.compile(
        r"(?:[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+){0,4}|"
        r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+[a-záéíóúüñ]+){0,3})"
    )
    for match in phrase_pattern.finditer(normalized):
        chunk = normalize_entity(match.group(0))
        lower_chunk = chunk.lower()
        if not chunk:
            continue
        if lower_chunk in ENTITY_STOPWORDS:
            continue
        if any(keyword in lower_chunk for keyword in ("por", "con", "para", "y")) and len(chunk.split()) <= 2:
            continue
        if len(chunk.split()) >= 1:
            candidates.append(chunk)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_entity(candidate).lower()
        if not key or key in seen:
            continue
        if any(key == item or key in item or item in key for item in seen):
            continue
        seen.add(key)
        deduped.append(normalize_entity(candidate))

    deduped = [item for item in deduped if item and item.lower() not in ENTITY_STOPWORDS]
    return deduped


def _clean_relation_object(value: str) -> str:
    obj = normalize_entity(value)
    if not obj:
        return obj
    obj = re.sub(r"^(el|la|los|las|un|una|unos|unas)\s+", "", obj, flags=re.I)
    for separator in (" para ", " con ", " en ", " de ", " del ", " por ", " y "):
        if separator in obj.lower():
            left = obj.lower().split(separator, 1)[0]
            obj = normalize_entity(obj[: len(obj) - (len(obj) - len(left))] if False else left)
            break
    obj = obj.strip(" ,;:.-_()[]{}")
    return obj


def extract_triplets_from_text(text: str, doc_id: str | None = None, chunk_id: str | None = None) -> list[dict]:
    """Extrae tripletas sujeto-relacion-objeto usando entidades mínimas y patrones de relación.

    El enfoque es más sólido que una simple heurística lineal: primero se detectan
    entidades relevantes dentro de la oración; luego se busca si una relación
    verbal se produce entre dos de ellas. Esto mejora la extracción y facilita la
    trazabilidad documental del grafo.
    """
    triplets: list[dict] = []
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for sentence in sentences:
        if not sentence:
            continue
        entities = extract_entities_from_text(sentence)
        if len(entities) < 2:
            continue
        for relation, relation_label in RELATION_PATTERNS:
            rel_pattern = re.escape(relation)
            for i, subject in enumerate(entities):
                for j, obj in enumerate(entities):
                    if i == j:
                        continue
                    if re.search(rf"\b{re.escape(subject)}\b\s+{rel_pattern}\s+\b{re.escape(obj)}\b", sentence, flags=re.I):
                        triplets.append({
                            "subject": normalize_entity(subject),
                            "relation": relation_label,
                            "object": normalize_entity(obj),
                            "doc_id": doc_id,
                            "chunk_id": chunk_id,
                            "evidence": sentence[:300],
                        })
    # Fallback conservador: si no se pudieron casar entidades, volver a la
    # heurística original para no perder relaciones claramente expresadas.
    if not triplets:
        for sentence in sentences:
            if not sentence:
                continue
            for relation, relation_label in RELATION_PATTERNS:
                pattern = re.compile(rf"(?P<subject>[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ .-]+?)\s+(?P<keyword>{re.escape(relation)})\s+(?P<object>[^.;!?]+)", re.I)
                match = pattern.search(sentence)
                if not match:
                    continue
                subject = normalize_entity(match.group("subject"))
                object_text = match.group("object").strip()
                object_text = object_text.rstrip(".,;: ")
                object_text = re.sub(r"\s+(?:para|con|en|de|del|por|y)\s+.*$", "", object_text, flags=re.I)
                object_text = re.sub(r"^(?:el|la|los|las|un|una|unos|unas)\s+", "", object_text, flags=re.I)
                object_text = normalize_entity(object_text)
                if not subject or not object_text:
                    continue
                triplets.append({
                    "subject": subject,
                    "relation": relation_label,
                    "object": object_text,
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                    "evidence": sentence[:300],
                })
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for triplet in triplets:
        key = (triplet["subject"].lower(), triplet["relation"].lower(), triplet["object"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(triplet)
    return deduped


def build_graph_from_chunks(chunks: Iterable[dict]) -> nx.DiGraph:
    """Construye un grafo a partir de chunks ya segmentados.

    Cada arista lleva la evidencia textual y la trazabilidad al chunk original.
    """
    graph = nx.DiGraph()
    for chunk in chunks:
        text = str(chunk.get("texto") or chunk.get("text") or "")
        doc_id = chunk.get("doc_id")
        chunk_id = chunk.get("chunk_id")
        if not text:
            continue
        for triplet in extract_triplets_from_text(text, doc_id=doc_id, chunk_id=chunk_id):
            s = triplet["subject"]
            o = triplet["object"]
            r = triplet["relation"]
            if not s or not o:
                continue
            if not graph.has_node(s):
                graph.add_node(s, type="entity")
            if not graph.has_node(o):
                graph.add_node(o, type="entity")
            if graph.has_edge(s, o):
                prior = graph[s][o]
                prior["evidence"] = (prior.get("evidence", "") + " || " + triplet["evidence"]).strip(" |")
                if doc_id:
                    prior["doc_ids"] = (prior.get("doc_ids", "") + (";" if prior.get("doc_ids") else "") + str(doc_id)).strip(";")
                if chunk_id:
                    prior["chunk_ids"] = (prior.get("chunk_ids", "") + (";" if prior.get("chunk_ids") else "") + str(chunk_id)).strip(";")
                rels = prior.get("relations", "")
                if r not in (rels.split(";") if rels else []):
                    prior["relations"] = (rels + (";" if rels else "") + str(r)).strip(";")
                continue
            graph.add_edge(
                s,
                o,
                relation=r,
                relations=str(r),
                evidence=str(triplet["evidence"]),
                doc_id=doc_id,
                chunk_id=chunk_id,
                doc_ids=str(doc_id) if doc_id else "",
                chunk_ids=str(chunk_id) if chunk_id else "",
            )
    return graph


def load_chunks_from_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def save_graph(graph: nx.DiGraph, output_path: str | Path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    graph.write_graphml(output)


def load_graph(path: str | Path) -> nx.DiGraph | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return nx.read_graphml(file_path)
    except Exception:
        return None


def graph_related_doc_ids(graph: nx.DiGraph | None, query: str) -> set[str]:
    if graph is None:
        return set()
    query = (query or "").lower()
    clues = set(re.findall(r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ .-]{2,}", query))
    if not clues and not query:
        return set()
    candidates = {c.strip() for c in clues}
    docs: set[str] = set()
    for node in graph.nodes:
        node_text = str(node).lower()
        if any(c.lower() in node_text or node_text in c.lower() for c in (c.lower() for c in candidates)):
            for _, _, data in graph.edges(node, data=True):
                for value in (data.get("doc_ids", ""), data.get("doc_id", "")):
                    if value:
                        docs.update(str(value).split(";"))
    return {d for d in docs if d}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Genera un grafo de conocimiento a partir de chunks JSONL.")
    parser.add_argument("chunks", type=Path, help="Ruta al archivo chunks.jsonl")
    parser.add_argument("--output", type=Path, default=Path("entrega/base_vectorial/grafo/grafo.graphml"), help="Ruta de salida del grafo")
    args = parser.parse_args()

    chunks = load_chunks_from_jsonl(args.chunks)
    graph = build_graph_from_chunks(chunks)
    save_graph(graph, args.output)
    print(f"Grafo construido: {graph.number_of_nodes()} nodos, {graph.number_of_edges()} aristas")
