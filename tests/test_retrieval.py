"""Tests for graph-aware retrieval."""

from pathlib import Path

import numpy as np
import pytest

from okf_graphrag.ingestion import ingest_bundle
from okf_graphrag.search.vectors import store_embeddings_bulk
from okf_graphrag.retrieval.engine import (
    RetrievalResult,
    RetrievedConcept,
    _apply_metadata_filter,
    _expand_graph,
    _filter_results,
    build_context,
    retrieve,
)
from okf_graphrag.search.ranking import SearchResult, bm25_search
from okf_graphrag.graph.store import KnowledgeGraph

BUNDLE_ROOT = Path(__file__).parent.parent / "knowledge" / "sample"


@pytest.fixture
def store_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    with KnowledgeGraph(db_path) as store:
        ingest_bundle(BUNDLE_ROOT, store)
        yield store


@pytest.fixture
def store_with_embeddings(store_with_data):
    """Store with fake but biased embeddings for deterministic vector search."""
    store = store_with_data
    rows = store.conn.execute(
        "SELECT chunk_id, concept_id, text FROM chunks ORDER BY chunk_id"
    ).fetchall()
    np.random.seed(42)
    chunk_ids = [r["chunk_id"] for r in rows]
    embeddings = np.random.randn(len(chunk_ids), 1536).astype(np.float32)

    # Bias embeddings toward domain-specific keywords
    topic_signals = {
        "gateway": 0, "api": 0, "routing": 0,
        "auth": 1, "token": 1, "oauth": 1,
        "notification": 2, "email": 2, "sms": 2,
        "event": 3, "kafka": 3, "message": 3,
        "sdk": 4, "client": 4, "library": 4,
        "deploy": 5, "kubernetes": 5, "container": 5,
        "monitor": 6, "alert": 6, "metrics": 6,
        "circuit": 7, "breaker": 7, "resilience": 7,
    }

    for i, row in enumerate(rows):
        text_lower = row["text"].lower()
        for keyword, dim in topic_signals.items():
            if keyword in text_lower:
                embeddings[i][dim] += 3.0

    store_embeddings_bulk(store, chunk_ids, embeddings)
    return store


# ── metadata filter tests ──────────────────────────────────────────

class TestMetadataFilter:
    def test_no_filter_returns_none(self, store_with_data):
        result = _apply_metadata_filter(store_with_data)
        assert result is None

    def test_type_filter(self, store_with_data):
        result = _apply_metadata_filter(store_with_data, type_filter=["System"])
        assert result is not None
        assert "services/api-gateway" in result
        assert "services/auth-service" in result
        assert "libraries/sdk-client" not in result

    def test_tag_filter(self, store_with_data):
        result = _apply_metadata_filter(store_with_data, tag_filter=["auth"])
        assert result is not None
        for cid in result:
            node = store_with_data.get_node(cid)
            import json
            tags = json.loads(node["tags"])
            assert "auth" in tags

    def test_combined_filter(self, store_with_data):
        result = _apply_metadata_filter(
            store_with_data, type_filter=["System"], status_filter=["stable"]
        )
        assert result is not None
        for cid in result:
            node = store_with_data.get_node(cid)
            assert node["type"] == "System"
            assert node["status"] == "stable"

    def test_filter_results(self):
        results = [
            SearchResult(1, "a", "text", "", 1.0, "bm25"),
            SearchResult(2, "b", "text", "", 0.5, "bm25"),
        ]
        filtered = _filter_results(results, {"a"})
        assert len(filtered) == 1
        assert filtered[0].concept_id == "a"

    def test_filter_results_none_allows_all(self):
        results = [
            SearchResult(1, "a", "text", "", 1.0, "bm25"),
            SearchResult(2, "b", "text", "", 0.5, "bm25"),
        ]
        assert len(_filter_results(results, None)) == 2


# ── graph expansion tests ──────────────────────────────────────────

class TestGraphExpansion:
    def test_1hop_from_api_gateway(self, store_with_data):
        expanded = _expand_graph({"services/api-gateway"}, store_with_data, max_hops=1)
        assert len(expanded) > 0
        assert all(v == "graph_1hop" for v in expanded.values())
        assert "services/auth-service" in expanded
        assert "services/api-gateway" not in expanded

    def test_2hop_reaches_more(self, store_with_data):
        exp_1 = _expand_graph({"services/api-gateway"}, store_with_data, max_hops=1)
        exp_2 = _expand_graph({"services/api-gateway"}, store_with_data, max_hops=2)
        assert len(exp_2) > len(exp_1)
        hop2_only = {k for k, v in exp_2.items() if v == "graph_2hop"}
        assert len(hop2_only) > 0

    def test_0hops_returns_empty(self, store_with_data):
        expanded = _expand_graph({"services/api-gateway"}, store_with_data, max_hops=0)
        assert len(expanded) == 0

    def test_expansion_excludes_seeds(self, store_with_data):
        seeds = {"services/api-gateway", "services/auth-service"}
        expanded = _expand_graph(seeds, store_with_data, max_hops=1)
        assert "services/api-gateway" not in expanded
        assert "services/auth-service" not in expanded


# ── context builder tests ──────────────────────────────────────────

class TestContextBuilder:
    def test_produces_markdown(self, store_with_data):
        concepts = [RetrievedConcept(
            concept_id="test/a",
            title="Test A",
            type="Tool",
            description="A test concept",
            chunks=[{"text": "Some content here", "heading": "Overview", "chunk_id": 1}],
            score=0.9,
            source="search",
            neighbors={"outgoing": [], "incoming": []},
        )]
        context = build_context(concepts)
        assert "# Retrieved Knowledge Context" in context
        assert "Test A" in context
        assert "Some content here" in context

    def test_respects_max_concepts(self, store_with_data):
        concepts = [
            RetrievedConcept(f"test/{i}", f"Concept {i}", "Tool", "", [], 0.5, "search")
            for i in range(20)
        ]
        context = build_context(concepts, max_concepts=3)
        assert "Concept 0" in context
        assert "Concept 2" in context
        assert "Concept 3" not in context


# ── integration: retrieval pipeline tests ────────────────────────────

class TestRetrievalPipeline:
    def test_graph_expands_beyond_search(self, store_with_embeddings):
        """Graph-aware retrieval finds more concepts than search alone."""
        bm25_results = bm25_search("api gateway routing", store_with_embeddings, top_k=3)
        search_concepts = set()
        for r in bm25_results:
            search_concepts.add(r.concept_id)

        expanded = _expand_graph(search_concepts, store_with_embeddings, max_hops=1)
        total = search_concepts | set(expanded.keys())
        assert len(total) > len(search_concepts)

    def test_type_filter_narrows_results(self, store_with_embeddings):
        all_types = _apply_metadata_filter(store_with_embeddings)
        system_only = _apply_metadata_filter(store_with_embeddings, type_filter=["System"])
        assert all_types is None
        assert system_only is not None
        assert len(system_only) < 21

    def test_graph_expansion_finds_related(self, store_with_data):
        """Starting from sdk-client, graph should reach api-gateway."""
        expanded = _expand_graph({"libraries/sdk-client"}, store_with_data, max_hops=1)
        assert "services/api-gateway" in expanded

    def test_bidirectional_expansion(self, store_with_data):
        """Expansion follows both outgoing and incoming edges."""
        expanded = _expand_graph({"services/api-gateway"}, store_with_data, max_hops=1)
        neighbors = store_with_data.get_neighbors("services/api-gateway")
        outgoing_ids = {n["concept_id"] for n in neighbors["outgoing"]}
        incoming_ids = {n["concept_id"] for n in neighbors["incoming"]}
        all_neighbor_ids = outgoing_ids | incoming_ids
        for nid in all_neighbor_ids:
            assert nid in expanded, f"{nid} should be in expansion"
