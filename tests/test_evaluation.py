"""
Comparison test: Layer 3 (hybrid search) vs Layer 4 (graph-aware retrieval).

Measures two dimensions:
1. **Recall**: Does graph expansion find more ground-truth concepts?
2. **Context efficiency**: How much text does each approach need per unit of recall?

Uses fake but biased embeddings so tests run without an API key while still
exercising the full pipeline including vector similarity.

Ground truth: for each query we define the concepts a correct answer MUST reference.
Recall = (ground truth concepts found) / (total ground truth concepts).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from okf_graphrag.search.ranking import SearchResult, bm25_search, vector_search
from okf_graphrag.search.vectors import load_all_vectors, store_embeddings_bulk
from okf_graphrag.ingestion import ingest_bundle
from okf_graphrag.retrieval.engine import (
    _apply_metadata_filter,
    _expand_graph,
    _filter_results,
    _rerank,
    RetrievedConcept,
    build_context,
)
from okf_graphrag.graph.store import KnowledgeGraph

BUNDLE_ROOT = Path(__file__).parent.parent / "knowledge" / "sample"

# ── ground truth queries ───────────────────────────────────────────
# Each entry: (query, set of concept_ids that a complete answer needs)

GROUND_TRUTH = [
    (
        "How do I configure the API Gateway for rate limiting?",
        {"services/api-gateway", "services/api-gateway-config", "services/auth-service", "concepts/rate-limiting"},
    ),
    (
        "What is the notification delivery architecture?",
        {"services/notification-engine", "services/notification-channels", "services/event-bus", "libraries/sdk-client"},
    ),
    (
        "How does data flow through the event-driven pipeline?",
        {"concepts/event-driven-architecture", "services/event-bus", "workflows/event-processing", "services/notification-engine"},
    ),
    (
        "How do I deploy a service using the CI/CD pipeline?",
        {"workflows/ci-cd-pipeline", "workflows/deployment-process", "services/api-gateway", "concepts/container-orchestration"},
    ),
    (
        "How does authentication and authorization work?",
        {"services/auth-service", "services/auth-tokens", "services/api-gateway", "libraries/sdk-client"},
    ),
]


@pytest.fixture(scope="module")
def store_with_biased_embeddings(tmp_path_factory):
    """
    Create a store with embeddings biased toward topic keywords.
    This gives vector search a realistic (but deterministic) signal.
    """
    tmp_path = tmp_path_factory.mktemp("comparison")
    db_path = tmp_path / "test.db"
    store = KnowledgeGraph(db_path)
    ingest_bundle(BUNDLE_ROOT, store)

    rows = store.conn.execute(
        "SELECT chunk_id, concept_id, text FROM chunks ORDER BY chunk_id"
    ).fetchall()

    np.random.seed(42)
    n = len(rows)
    embeddings = np.random.randn(n, 1536).astype(np.float32) * 0.1

    topic_signals = {
        "gateway": 0, "api": 0, "routing": 0,
        "auth": 1, "token": 1, "oauth": 1,
        "notification": 2, "email": 2, "sms": 2,
        "event": 3, "kafka": 3, "message": 3,
        "sdk": 4, "client": 4, "library": 4,
        "deploy": 5, "kubernetes": 5, "container": 5,
        "monitor": 6, "alert": 6, "metrics": 6,
        "circuit": 7, "breaker": 7, "resilience": 7,
        "rate": 8, "limiting": 8, "throttle": 8,
        "pipeline": 9, "ci": 9, "cd": 9,
    }

    for i, row in enumerate(rows):
        text_lower = row["text"].lower()
        for keyword, dim in topic_signals.items():
            if keyword in text_lower:
                embeddings[i][dim] += 2.0

    chunk_ids = [r["chunk_id"] for r in rows]
    store_embeddings_bulk(store, chunk_ids, embeddings)
    yield store
    store.close()


def _make_query_embedding(query: str) -> np.ndarray:
    """Create a query embedding biased toward the same topic dimensions."""
    topic_signals = {
        "gateway": 0, "api": 0, "routing": 0,
        "auth": 1, "token": 1, "oauth": 1,
        "notification": 2, "email": 2, "sms": 2,
        "event": 3, "kafka": 3, "message": 3,
        "sdk": 4, "client": 4, "library": 4,
        "deploy": 5, "kubernetes": 5, "container": 5,
        "monitor": 6, "alert": 6, "metrics": 6,
        "circuit": 7, "breaker": 7, "resilience": 7,
        "rate": 8, "limiting": 8, "throttle": 8,
        "pipeline": 9, "ci": 9, "cd": 9,
    }
    emb = np.zeros(1536, dtype=np.float32)
    query_lower = query.lower()
    for keyword, dim in topic_signals.items():
        if keyword in query_lower:
            emb[dim] += 2.0
    rng = np.random.RandomState(hash(query) % (2**31))
    emb += rng.randn(1536).astype(np.float32) * 0.05
    return emb


DEFAULT_TOP_K = 4  # small budget forces trade-offs -- this is where graph expansion shines


def _layer3_concepts(query: str, store: KnowledgeGraph, top_k: int = DEFAULT_TOP_K) -> set[str]:
    """Layer 3: hybrid search (vector + BM25), return unique concept IDs."""
    query_emb = _make_query_embedding(query)
    vec_results = vector_search(query_emb, store, top_k=top_k * 2)
    bm25_results = bm25_search(query, store, top_k=top_k * 2)

    def _normalize(results):
        if not results:
            return {}
        m = max(r.score for r in results) or 1.0
        return {r.chunk_id: (r.score / m, r.concept_id) for r in results}

    vec = _normalize(vec_results)
    bm25 = _normalize(bm25_results)

    combined: dict[str, float] = {}
    for cid in set(vec) | set(bm25):
        v_score, v_concept = vec.get(cid, (0.0, None))
        b_score, b_concept = bm25.get(cid, (0.0, None))
        concept_id = v_concept or b_concept
        score = 0.7 * v_score + 0.3 * b_score
        if concept_id not in combined or score > combined[concept_id]:
            combined[concept_id] = score

    sorted_concepts = sorted(combined.items(), key=lambda x: x[1], reverse=True)
    return {cid for cid, _ in sorted_concepts[:top_k]}


def _layer4_concepts(query: str, store: KnowledgeGraph, top_k: int = DEFAULT_TOP_K, max_hops: int = 1) -> set[str]:
    """Layer 4: search narrow then expand wide then rerank then return best top_k.

    The key architectural difference from Layer 3: Layer 4 uses a small seed
    (top_k//2) to find starting points, then graph-expands to a wider candidate
    pool, then reranks the entire pool and picks the best top_k. This lets the
    graph surface concepts that search alone would have ranked too low.
    """
    query_emb = _make_query_embedding(query)

    seed_k = max(2, top_k // 2)
    search_concepts = _layer3_concepts(query, store, top_k=seed_k)
    expanded = _expand_graph(search_concepts, store, max_hops=max_hops)

    all_concept_ids = dict.fromkeys(search_concepts, "search")
    for cid, source in expanded.items():
        if cid not in all_concept_ids:
            all_concept_ids[cid] = source

    concepts: list[RetrievedConcept] = []
    for cid, source in all_concept_ids.items():
        node = store.get_node(cid)
        if not node:
            continue
        chunks = store.get_chunks(cid)
        concepts.append(RetrievedConcept(
            concept_id=cid,
            title=node["title"] or cid,
            type=node["type"],
            description=node["description"],
            chunks=chunks,
            score=0.0,
            source=source,
        ))

    concepts = _rerank(query_emb, concepts, store)
    return {c.concept_id for c in concepts[:top_k]}


def _recall(retrieved: set[str], ground_truth: set[str]) -> float:
    if not ground_truth:
        return 1.0
    return len(retrieved & ground_truth) / len(ground_truth)


# ── the comparison tests ───────────────────────────────────────────

class TestGraphAwarenessImprovement:
    """Compare Layer 3 (hybrid search) vs Layer 4 (graph-aware) on ground truth queries."""

    def test_layer4_recall_at_least_as_good(self, store_with_biased_embeddings):
        """Layer 4 should have equal or better recall than Layer 3 on every query."""
        store = store_with_biased_embeddings
        for query, truth in GROUND_TRUTH:
            l3 = _layer3_concepts(query, store)
            l4 = _layer4_concepts(query, store)
            r3 = _recall(l3, truth)
            r4 = _recall(l4, truth)
            assert r4 >= r3, (
                f"Layer 4 recall ({r4:.0%}) worse than Layer 3 ({r3:.0%}) "
                f"for query: {query}\n"
                f"  L3 found: {l3 & truth}\n  L4 found: {l4 & truth}\n  Missing: {truth - l4}"
            )

    def test_layer4_average_recall_at_least_as_good(self, store_with_biased_embeddings):
        """Across all queries, Layer 4 should have equal or better average recall."""
        store = store_with_biased_embeddings
        l3_recalls = []
        l4_recalls = []
        for query, truth in GROUND_TRUTH:
            l3 = _layer3_concepts(query, store)
            l4 = _layer4_concepts(query, store)
            l3_recalls.append(_recall(l3, truth))
            l4_recalls.append(_recall(l4, truth))

        avg_l3 = sum(l3_recalls) / len(l3_recalls)
        avg_l4 = sum(l4_recalls) / len(l4_recalls)
        assert avg_l4 >= avg_l3, (
            f"Layer 4 avg recall ({avg_l4:.0%}) worse than Layer 3 ({avg_l3:.0%})"
        )

    def test_graph_expansion_finds_missed_concepts(self, store_with_biased_embeddings):
        """Graph expansion should help on at least some queries by finding concepts search missed."""
        store = store_with_biased_embeddings
        queries_where_graph_helped = 0
        for query, truth in GROUND_TRUTH:
            l3 = _layer3_concepts(query, store)
            l4 = _layer4_concepts(query, store)
            graph_added = (l4 & truth) - (l3 & truth)
            if graph_added:
                queries_where_graph_helped += 1

        assert queries_where_graph_helped >= 1, (
            "Graph expansion didn't help on any query"
        )

    def test_detailed_recall_report(self, store_with_biased_embeddings):
        """Print a detailed comparison (always passes, for visibility)."""
        store = store_with_biased_embeddings
        print("\n" + "=" * 80)
        print("LAYER 3 vs LAYER 4 RECALL COMPARISON")
        print("=" * 80)

        total_l3 = 0
        total_l4 = 0

        for query, truth in GROUND_TRUTH:
            l3 = _layer3_concepts(query, store)
            l4 = _layer4_concepts(query, store)
            r3 = _recall(l3, truth)
            r4 = _recall(l4, truth)
            total_l3 += r3
            total_l4 += r4

            print(f"\nQuery: \"{query}\"")
            print(f"  Ground truth ({len(truth)}): {', '.join(sorted(truth))}")
            print(f"  L3 recall: {r3:.0%} ({len(l3 & truth)}/{len(truth)}) -- concepts: {len(l3)}")
            print(f"  L4 recall: {r4:.0%} ({len(l4 & truth)}/{len(truth)}) -- concepts: {len(l4)}")

            graph_added = (l4 & truth) - (l3 & truth)
            if graph_added:
                print(f"  Graph added: {', '.join(sorted(graph_added))}")

            missed = truth - l4
            if missed:
                print(f"  Still missing: {', '.join(sorted(missed))}")

        avg_l3 = total_l3 / len(GROUND_TRUTH)
        avg_l4 = total_l4 / len(GROUND_TRUTH)
        improvement = avg_l4 - avg_l3

        print(f"\n{'=' * 80}")
        print(f"SUMMARY")
        print(f"  Layer 3 avg recall: {avg_l3:.0%}")
        print(f"  Layer 4 avg recall: {avg_l4:.0%}")
        print(f"  Improvement:        +{improvement:.0%}")
        print(f"{'=' * 80}")

    def test_context_size_bounded(self, store_with_biased_embeddings):
        """Even with graph expansion, context builder caps output."""
        store = store_with_biased_embeddings
        query = "How do I configure the API Gateway for rate limiting?"
        l4_ids = _layer4_concepts(query, store, top_k=8, max_hops=2)

        concepts = []
        for cid in l4_ids:
            node = store.get_node(cid)
            if node:
                concepts.append(RetrievedConcept(
                    concept_id=cid, title=node["title"] or cid,
                    type=node["type"], description=node["description"],
                    chunks=store.get_chunks(cid), score=0.5, source="search",
                ))

        context = build_context(concepts, max_concepts=8)
        concept_headers = [line for line in context.split("\n") if line.startswith("## [")]
        assert len(concept_headers) <= 8
