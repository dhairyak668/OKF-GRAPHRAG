"""Tests for query agent (source resolution + grounded answering).

Tests the source resolver with real bundle data and mock the LLM call
so tests run without an API key.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from okf_graphrag.ingestion import ingest_bundle
from okf_graphrag.search.vectors import store_embeddings_bulk
from okf_graphrag.generation.sources import (
    resolve_sources,
    _get_sources_from_frontmatter,
    resource_path_to_url,
    load_repo_urls,
    ResolvedSource,
)
from okf_graphrag.generation.generator import (
    ask,
    _build_source_context,
    QueryResult,
    SourceReference,
    SYSTEM_PROMPT,
)
from okf_graphrag.graph.store import KnowledgeGraph

BUNDLE_ROOT = Path(__file__).parent.parent / "knowledge" / "sample"
PROJECT_ROOT = Path(__file__).parent.parent


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def store_with_embeddings(tmp_path_factory):
    """Ingest bundle and add fake embeddings so retrieval pipeline works."""
    tmp_path = tmp_path_factory.mktemp("query")
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
    emb = np.zeros(1536, dtype=np.float32)
    for keyword, dim in topic_signals.items():
        if keyword in query.lower():
            emb[dim] += 2.0
    rng = np.random.RandomState(hash(query) % (2**31))
    emb += rng.randn(1536).astype(np.float32) * 0.05
    return emb


# ── source resolver tests ────────────────────────────────────────

class TestSourceResolver:

    def test_get_sources_from_frontmatter(self, store_with_embeddings):
        store = store_with_embeddings
        node = store.get_node("services/api-gateway")
        sources = _get_sources_from_frontmatter(node["frontmatter"])
        assert len(sources) >= 1
        assert sources[0]["resource"] == "/source_docs/api-gateway/README.md"

    def test_resolve_sources_finds_source_docs(self, store_with_embeddings):
        store = store_with_embeddings
        resolved = resolve_sources(
            ["services/api-gateway", "services/auth-service"],
            store,
            PROJECT_ROOT,
        )
        assert len(resolved) >= 1
        gateway_src = next(s for s in resolved if "api-gateway" in s.resource_path.lower())
        assert gateway_src.exists
        assert len(gateway_src.content) > 100

    def test_resolve_sources_deduplicates(self, store_with_embeddings):
        """If two concepts reference the same raw doc, it appears once."""
        store = store_with_embeddings
        resolved = resolve_sources(
            ["services/api-gateway", "services/api-gateway-config", "services/auth-service"],
            store,
            PROJECT_ROOT,
        )
        resource_paths = [s.resource_path for s in resolved]
        assert len(resource_paths) == len(set(resource_paths))

    def test_resolve_sources_missing_raw_doc(self, store_with_embeddings, tmp_path):
        """Sources pointing to nonexistent files are marked exists=False."""
        store = store_with_embeddings
        resolved = resolve_sources(
            ["services/api-gateway"],
            store,
            tmp_path,  # wrong root -- file won't exist
        )
        for src in resolved:
            assert not src.exists
            assert src.content == ""


# ── URL resolution tests ─────────────────────────────────────────

class TestUrlResolution:

    def test_resource_path_to_url(self):
        repo_urls = {
            "api-gateway": "https://github.com/acme-platform/api-gateway/-/blob/main",
        }
        url = resource_path_to_url("/source_docs/api-gateway/configuration.md", repo_urls)
        assert url == "https://github.com/acme-platform/api-gateway/-/blob/main/configuration.md"

    def test_resource_path_to_url_readme(self):
        repo_urls = {
            "api-gateway": "https://github.com/acme-platform/api-gateway/-/blob/main",
        }
        url = resource_path_to_url("/source_docs/api-gateway/README.md", repo_urls)
        assert url == "https://github.com/acme-platform/api-gateway/-/blob/main/README.md"

    def test_resource_path_to_url_unknown_repo(self):
        repo_urls = {"api-gateway": "https://github.com/acme-platform/api-gateway/-/blob/main"}
        url = resource_path_to_url("/source_docs/unknown-repo/file.md", repo_urls)
        assert url == ""

    def test_resource_path_to_url_bad_format(self):
        url = resource_path_to_url("/something/else.md", {})
        assert url == ""

    def test_load_repo_urls_from_project(self):
        urls = load_repo_urls(PROJECT_ROOT)
        assert "api-gateway" in urls
        assert "sdk-client" in urls
        assert urls["api-gateway"].startswith("https://")


# ── context building tests ───────────────────────────────────────

class TestContextBuilding:

    def test_build_source_context_format(self, store_with_embeddings):
        store = store_with_embeddings
        resolved = resolve_sources(["services/api-gateway"], store, PROJECT_ROOT)
        available = [s for s in resolved if s.exists]
        context = _build_source_context(available)
        assert "=== Document 1:" in context
        assert "Source: /source_docs/api-gateway/README.md" in context
        assert len(context) > 100


# ── answer formatting tests ──────────────────────────────────────

class TestAnswerFormatting:

    def test_query_result_format_includes_references_with_urls(self):
        result = QueryResult(
            query="test",
            answer="The API Gateway uses Envoy Proxy for routing.",
            references=[
                SourceReference(
                    title="API Gateway README",
                    resource_path="/source_docs/api-gateway/README.md",
                    concept_id="services/api-gateway",
                    url="https://github.com/acme-platform/api-gateway/-/blob/main/README.md",
                ),
            ],
            retrieval=MagicMock(),
            raw_sources_used=1,
            raw_sources_chars=5000,
        )
        formatted = result.format()
        assert "The API Gateway uses Envoy Proxy" in formatted
        assert "---" in formatted
        assert "References (source documents):" in formatted
        assert "https://github.com/acme-platform/api-gateway/-/blob/main/README.md" in formatted

    def test_query_result_format_falls_back_to_path(self):
        result = QueryResult(
            query="test",
            answer="Answer.",
            references=[
                SourceReference(title="Some Doc", resource_path="/source_docs/repo/file.md", concept_id="x", url=""),
            ],
            retrieval=MagicMock(),
            raw_sources_used=1,
            raw_sources_chars=100,
        )
        formatted = result.format()
        assert "/source_docs/repo/file.md" in formatted

    def test_system_prompt_instructs_grounding(self):
        assert "ONLY" in SYSTEM_PROMPT
        assert "Do not invent" in SYSTEM_PROMPT


# ── end-to-end with mocked LLM ──────────────────────────────────

class TestAskEndToEnd:

    def test_ask_returns_grounded_answer(self, store_with_embeddings):
        store = store_with_embeddings

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "The API Gateway uses Envoy Proxy for L7 traffic management."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=_make_query_embedding("api gateway routing").tolist())]
        )

        result = ask(
            "How does the API Gateway handle routing?",
            store,
            project_root=PROJECT_ROOT,
            client=mock_client,
            top_k=3,
            max_hops=1,
        )

        assert "Envoy Proxy" in result.answer
        assert len(result.references) > 0
        assert result.raw_sources_used > 0
        assert any("api-gateway" in r.resource_path.lower() for r in result.references)

    def test_ask_sends_source_docs_to_llm(self, store_with_embeddings):
        """Verify the LLM receives raw doc content, not bundle summaries."""
        store = store_with_embeddings

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Answer here."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=_make_query_embedding("api gateway").tolist())]
        )

        ask(
            "How does the API Gateway work?",
            store,
            project_root=PROJECT_ROOT,
            client=mock_client,
            top_k=3,
            max_hops=1,
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[1]["content"]
        assert "Source documents:" in user_msg
        assert "/source_docs/" in user_msg

    def test_ask_no_sources_returns_fallback(self, store_with_embeddings, tmp_path):
        """When raw docs can't be found, returns a helpful fallback."""
        store = store_with_embeddings

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=_make_query_embedding("api gateway").tolist())]
        )

        result = ask(
            "How does the API Gateway work?",
            store,
            project_root=tmp_path,  # wrong root
            client=mock_client,
            top_k=3,
            max_hops=1,
        )

        assert "unavailable" in result.answer.lower() or "couldn't find" in result.answer.lower()
        assert result.raw_sources_used == 0
        assert mock_client.chat.completions.create.call_count == 0
