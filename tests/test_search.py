"""Tests for embedding, vector store, BM25, and hybrid search."""

from pathlib import Path

import numpy as np
import pytest

from okf_graphrag.search.ranking import SearchResult, bm25_search, vector_search
from okf_graphrag.search.vectors import (
    embedding_stats,
    get_unembedded_chunks,
    load_all_vectors,
    store_embedding,
    store_embeddings_bulk,
)
from okf_graphrag.ingestion import ingest_bundle
from okf_graphrag.graph.store import KnowledgeGraph

BUNDLE_ROOT = Path(__file__).parent.parent / "knowledge" / "sample"


@pytest.fixture
def store_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    with KnowledgeGraph(db_path) as store:
        ingest_bundle(BUNDLE_ROOT, store)
        yield store


# ── vector store tests ──────────────────────────────────────────────

class TestVectorStore:
    def test_store_and_load(self, store_with_data):
        store = store_with_data
        chunks = store.get_chunks("services/api-gateway")
        chunk_id = chunks[0]["chunk_id"]

        fake_emb = np.random.randn(1536).astype(np.float32)
        store_embedding(store, chunk_id, fake_emb)

        chunk_ids, embeddings = load_all_vectors(store)
        assert chunk_id in chunk_ids
        idx = chunk_ids.index(chunk_id)
        np.testing.assert_allclose(embeddings[idx], fake_emb, atol=1e-6)

    def test_bulk_store(self, store_with_data):
        store = store_with_data
        chunks = store.get_chunks("services/api-gateway")[:3]
        cids = [c["chunk_id"] for c in chunks]
        embs = np.random.randn(3, 1536).astype(np.float32)

        store_embeddings_bulk(store, cids, embs)

        loaded_ids, loaded_embs = load_all_vectors(store)
        assert len(loaded_ids) == 3

    def test_unembedded_chunks(self, store_with_data):
        store = store_with_data
        unembedded = get_unembedded_chunks(store)
        stats = embedding_stats(store)

        assert len(unembedded) == stats["total_chunks"]
        assert stats["embedded"] == 0
        assert stats["remaining"] == stats["total_chunks"]

    def test_embedding_stats_after_embed(self, store_with_data):
        store = store_with_data
        chunks = store.get_chunks("services/api-gateway")[:2]
        embs = np.random.randn(2, 1536).astype(np.float32)
        store_embeddings_bulk(store, [c["chunk_id"] for c in chunks], embs)

        stats = embedding_stats(store)
        assert stats["embedded"] == 2
        assert stats["remaining"] == stats["total_chunks"] - 2


# ── vector search tests (with fake embeddings) ─────────────────────

class TestVectorSearch:
    def test_search_returns_closest(self, store_with_data):
        store = store_with_data
        all_chunks = store.conn.execute(
            "SELECT chunk_id FROM chunks ORDER BY chunk_id LIMIT 5"
        ).fetchall()
        chunk_ids = [r["chunk_id"] for r in all_chunks]

        target_emb = np.zeros(1536, dtype=np.float32)
        target_emb[0] = 1.0

        embs = np.random.randn(5, 1536).astype(np.float32) * 0.01
        embs[2] = target_emb
        store_embeddings_bulk(store, chunk_ids, embs)

        results = vector_search(target_emb, store, top_k=3)
        assert len(results) > 0
        assert results[0].chunk_id == chunk_ids[2]
        assert results[0].score > 0.9

    def test_empty_vectors(self, store_with_data):
        results = vector_search(np.zeros(1536, dtype=np.float32), store_with_data, top_k=5)
        assert results == []


# ── BM25 search tests ──────────────────────────────────────────────

class TestBM25:
    def test_basic_query(self, store_with_data):
        results = bm25_search("api gateway routing", store_with_data, top_k=5)
        assert len(results) > 0
        concept_ids = [r.concept_id for r in results]
        assert "services/api-gateway" in concept_ids

    def test_notification_query(self, store_with_data):
        results = bm25_search("notification delivery email", store_with_data, top_k=5)
        assert len(results) > 0
        concept_ids = [r.concept_id for r in results]
        assert any("notification" in cid for cid in concept_ids)

    def test_no_results(self, store_with_data):
        results = bm25_search("xyznonexistent", store_with_data, top_k=5)
        assert len(results) == 0

    def test_empty_query(self, store_with_data):
        results = bm25_search("", store_with_data, top_k=5)
        assert len(results) == 0

    def test_auth_query(self, store_with_data):
        results = bm25_search("authentication oauth tokens", store_with_data, top_k=3)
        concept_ids = [r.concept_id for r in results]
        assert any("auth" in cid for cid in concept_ids)

    def test_event_driven(self, store_with_data):
        results = bm25_search("event driven kafka message", store_with_data, top_k=3)
        concept_ids = [r.concept_id for r in results]
        assert any(
            cid in concept_ids
            for cid in ["concepts/event-driven-architecture", "services/event-bus", "workflows/event-processing"]
        )
