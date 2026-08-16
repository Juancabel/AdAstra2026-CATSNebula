import json
from pathlib import Path

import pytest

from knowledge_graph import (
    build_graph_from_chunks,
    extract_entities_from_text,
    extract_triplets_from_text,
)
from entrega.generador import (
    construir_fragmentos,
    generar_evidencia_consulta,
    fusionar_resultados_vectoriales,
)


def test_extract_triplets_from_text_detects_domain_relations():
    text = (
        "Estados Unidos desarrolla sistemas autónomos para defensa. "
        "Colombia regula el uso de estos sistemas. "
        "La ONU coordina con la Fuerza Aérea."
    )

    triplets = extract_triplets_from_text(text)

    relations = {t["relation"] for t in triplets}
    assert {"desarrolla", "regula", "coordina"}.issubset(relations)
    assert any(t["subject"] == "Estados Unidos" for t in triplets)
    assert any(t["object"] == "sistemas autónomos" for t in triplets)


def test_build_graph_from_chunks_keeps_doc_and_chunk_traceability(tmp_path):
    chunks = [
        {
            "doc_id": "F1-AIINDEX-001",
            "chunk_id": "F1-AIINDEX-001_c000",
            "fuente": "F1_IA/ai_index/intro.json",
            "fenomeno": 1,
            "texto": "Estados Unidos desarrolla sistemas autónomos. Colombia regula estos sistemas.",
        }
    ]

    graph = build_graph_from_chunks(chunks)
    assert graph.number_of_edges() >= 2
    assert "Estados Unidos" in graph.nodes
    assert "Colombia" in graph.nodes

    edge = next(iter(graph.edges(data=True)))
    assert "doc_id" in edge[2]
    assert edge[2]["doc_id"] == "F1-AIINDEX-001"
    assert edge[2]["chunk_id"] == "F1-AIINDEX-001_c000"

    out = tmp_path / "grafo.graphml"
    graph.write_graphml(out)
    assert out.exists()


def test_graph_evidence_is_embedded_in_final_fragment_text():
    chunks = [
        {
            "doc_id": "F1-AIINDEX-001",
            "chunk_id": "F1-AIINDEX-001_c000",
            "texto": "Estados Unidos desarrolla sistemas autónomos. Colombia regula estos sistemas.",
        }
    ]
    graph = build_graph_from_chunks(chunks)
    metas = [{
        "doc_id": "F1-AIINDEX-001",
        "chunk_id": "F1-AIINDEX-001_c000",
        "texto": "Estados Unidos desarrolla sistemas autónomos y Colombia regula su uso en defensa.",
    }]

    fragmentos = construir_fragmentos([(0.9, 0)], metas, query_text="Estados Unidos", grafo=graph)

    assert fragmentos
    assert any("Evidencia del grafo" in frag["text"] for frag in fragmentos)


def test_generar_evidencia_consulta_returns_sidecar_structure():
    chunks = [
        {
            "doc_id": "F1-AIINDEX-001",
            "chunk_id": "F1-AIINDEX-001_c000",
            "texto": "Estados Unidos desarrolla sistemas autónomos. Colombia regula estos sistemas.",
        }
    ]
    graph = build_graph_from_chunks(chunks)

    evidence = generar_evidencia_consulta(
        "q001",
        "Estados Unidos",
        [{"rank": 1, "doc_id": "F1-AIINDEX-001"}],
        [{"rank": 1, "chunk_id": "F1-AIINDEX-001_c000", "doc_id": "F1-AIINDEX-001", "text": "texto"}],
        graph,
    )

    assert evidence["query_id"] == "q001"
    assert evidence["graph_evidence"]
    assert evidence["documents"][0]["doc_id"] == "F1-AIINDEX-001"


def test_extract_entities_from_text_detects_country_org_technology_and_event():
    text = "Estados Unidos y Colombia desarrollan un sistema autónomo para la Fuerza Aérea en la cumbre de seguridad espacial."
    entities = extract_entities_from_text(text)

    assert any(entity.lower() == "estados unidos" for entity in entities)
    assert any(entity.lower() == "colombia" for entity in entities)
    assert any("fuerza aérea" in entity.lower() for entity in entities)
    assert any("sistema autónomo" in entity.lower() or "sistema autonomo" in entity.lower() for entity in entities)
    assert any("cumbre" in entity.lower() for entity in entities)


def test_fusionar_resultados_vectoriales_uses_graph_candidates_with_rrf():
    vector_ranking = ["DOC-1", "DOC-2", "DOC-3"]
    graph_candidates = ["DOC-2", "DOC-4"]

    merged = fusionar_resultados_vectoriales(vector_ranking, graph_candidates)

    assert merged[0] == "DOC-2"
    assert "DOC-4" in merged
    assert merged[:3]
