from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from ..search.embedding import embed_query
from ..search.ranking import SearchResult, bm25_search, hybrid_search, vector_search
from ..search.vectors import load_all_vectors
from ..graph.store import KnowledgeGraph


@dataclass
class RetrievedConcept:
    concept_id: str
    title: str
    type: str
    description: str
    chunks: list[dict]
    score: float
    source: str
    neighbors: dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    concepts: list[RetrievedConcept]
    context_text: str


def _apply_metadata_filter(
    store: KnowledgeGraph,
    type_filter: list[str] | None = None,
    tag_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
) -> set[str] | None:
    if not type_filter and not tag_filter and not status_filter:
        return None

    conditions = []
    params: list[str] = []

    if type_filter:
        placeholders = ",".join("?" for _ in type_filter)
        conditions.append(f"type IN ({placeholders})")
        params.extend(type_filter)

    if status_filter:
        placeholders = ",".join("?" for _ in status_filter)
        conditions.append(f"status IN ({placeholders})")
        params.extend(status_filter)

    where = " AND ".join(conditions) if conditions else "1=1"
    rows = store.conn.execute(
        f"SELECT concept_id, tags FROM nodes WHERE {where}", params
    ).fetchall()

    matching = set()
    for row in rows:
        if tag_filter:
            node_tags = json.loads(row["tags"]) if row["tags"] else []
            if not any(t in node_tags for t in tag_filter):
                continue
        matching.add(row["concept_id"])

    return matching


def _filter_results(
    results: list[SearchResult], allowed: set[str] | None
) -> list[SearchResult]:
    if allowed is None:
        return results
    return [r for r in results if r.concept_id in allowed]


def _expand_graph(
    seed_concept_ids: set[str],
    store: KnowledgeGraph,
    max_hops: int = 2,
) -> dict[str, str]:
    expanded: dict[str, str] = {}
    frontier = seed_concept_ids.copy()

    for hop in range(1, max_hops + 1):
        next_frontier: set[str] = set()
        for cid in frontier:
            neighbors = store.get_neighbors(cid)
            for direction in ("outgoing", "incoming"):
                for n in neighbors[direction]:
                    nid = n["concept_id"]
                    if nid not in seed_concept_ids and nid not in expanded:
                        expanded[nid] = f"graph_{hop}hop"
                        next_frontier.add(nid)
        frontier = next_frontier

    return expanded


def _rerank(
    query_embedding: np.ndarray,
    concepts: list[RetrievedConcept],
    store: KnowledgeGraph,
) -> list[RetrievedConcept]:
    chunk_ids_all, embeddings_all = load_all_vectors(store)
    if len(chunk_ids_all) == 0:
        return concepts

    chunk_id_to_idx = {cid: i for i, cid in enumerate(chunk_ids_all)}
    query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-10)

    for concept in concepts:
        chunk_scores = []
        for chunk in concept.chunks:
            idx = chunk_id_to_idx.get(chunk["chunk_id"])
            if idx is not None:
                emb = embeddings_all[idx]
                emb_norm = emb / (np.linalg.norm(emb) + 1e-10)
                sim = float(emb_norm @ query_norm)
                chunk_scores.append(sim)

        if chunk_scores:
            best_chunk_sim = max(chunk_scores)
        else:
            best_chunk_sim = 0.0

        source_bonus = {"search": 0.3, "graph_1hop": 0.15, "graph_2hop": 0.05}
        concept.score = best_chunk_sim + source_bonus.get(concept.source, 0.0)

    concepts.sort(key=lambda c: c.score, reverse=True)
    return concepts


def build_context(concepts: list[RetrievedConcept], max_concepts: int = 8) -> str:
    parts: list[str] = []
    parts.append("# Retrieved Knowledge Context\n")

    for i, c in enumerate(concepts[:max_concepts]):
        hop_label = {"search": "direct match", "graph_1hop": "1-hop neighbor", "graph_2hop": "2-hop neighbor"}
        parts.append(f"## [{i+1}] {c.title} ({c.type})")
        parts.append(f"**Source**: {c.concept_id} | **Relevance**: {hop_label.get(c.source, c.source)} | **Score**: {c.score:.3f}")
        if c.description:
            parts.append(f"**Summary**: {c.description}")

        if c.neighbors.get("outgoing") or c.neighbors.get("incoming"):
            links = []
            for n in c.neighbors.get("outgoing", [])[:5]:
                links.append(f"→ {n['title'] or n['concept_id']}")
            for n in c.neighbors.get("incoming", [])[:5]:
                links.append(f"← {n['title'] or n['concept_id']}")
            parts.append(f"**Graph connections**: {' | '.join(links)}")

        for chunk in c.chunks[:3]:
            heading = f" ({chunk['heading']})" if chunk.get("heading") else ""
            parts.append(f"\n### Content{heading}\n{chunk['text']}")

        parts.append("")

    return "\n".join(parts)


def retrieve(
    query: str,
    store: KnowledgeGraph,
    client=None,
    top_k: int = 5,
    max_hops: int = 2,
    max_context_concepts: int = 8,
    type_filter: list[str] | None = None,
    tag_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
) -> RetrievalResult:
    allowed = _apply_metadata_filter(store, type_filter, tag_filter, status_filter)

    query_emb = embed_query(query, client=client)

    vec_results = vector_search(query_emb, store, top_k=top_k * 3)
    bm25_results = bm25_search(query, store, top_k=top_k * 3)
    vec_results = _filter_results(vec_results, allowed)
    bm25_results = _filter_results(bm25_results, allowed)

    def _normalize(results: list[SearchResult]) -> dict[int, float]:
        if not results:
            return {}
        max_score = max(r.score for r in results) or 1.0
        return {r.chunk_id: r.score / max_score for r in results}

    vec_scores = _normalize(vec_results)
    bm25_scores = _normalize(bm25_results)
    all_chunk_ids = set(vec_scores) | set(bm25_scores)
    combined: dict[int, float] = {}
    for cid in all_chunk_ids:
        combined[cid] = 0.7 * vec_scores.get(cid, 0.0) + 0.3 * bm25_scores.get(cid, 0.0)

    chunk_lookup = {r.chunk_id: r for r in vec_results + bm25_results}
    sorted_chunks = sorted(combined.items(), key=lambda x: x[1], reverse=True)

    seed_concepts: dict[str, float] = {}
    for chunk_id, score in sorted_chunks:
        r = chunk_lookup[chunk_id]
        if r.concept_id not in seed_concepts:
            seed_concepts[r.concept_id] = score
        if len(seed_concepts) >= top_k:
            break

    expanded = _expand_graph(set(seed_concepts), store, max_hops=max_hops)

    all_concept_ids = {cid: "search" for cid in seed_concepts}
    for cid, source in expanded.items():
        if cid not in all_concept_ids:
            if allowed is None or cid in allowed:
                all_concept_ids[cid] = source

    concepts: list[RetrievedConcept] = []
    for cid, source in all_concept_ids.items():
        node = store.get_node(cid)
        if not node:
            continue
        chunks = store.get_chunks(cid)
        neighbors = store.get_neighbors(cid)

        concepts.append(RetrievedConcept(
            concept_id=cid,
            title=node["title"] or cid,
            type=node["type"],
            description=node["description"],
            chunks=chunks,
            score=seed_concepts.get(cid, 0.0),
            source=source,
            neighbors=neighbors,
        ))

    concepts = _rerank(query_emb, concepts, store)
    context_text = build_context(concepts, max_concepts=max_context_concepts)

    return RetrievalResult(
        query=query,
        concepts=concepts,
        context_text=context_text,
    )
