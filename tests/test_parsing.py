"""Tests for parsing, graph store, and ingestion."""

from pathlib import Path
import sqlite3
import tempfile

import pytest

from okf_graphrag.parsing.models import Document, Link, Chunk
from okf_graphrag.parsing.okf_parser import (
    parse_frontmatter,
    extract_links,
    chunk_body,
    concept_id_from_path,
    is_concept_file,
    discover_documents,
    parse_document,
)
from okf_graphrag.graph.store import KnowledgeGraph
from okf_graphrag.ingestion import ingest_bundle

BUNDLE_ROOT = Path(__file__).parent.parent / "knowledge" / "sample"


# ── parser tests ────────────────────────────────────────────────────

class TestConceptId:
    def test_basic(self, tmp_path):
        f = tmp_path / "libraries" / "thing.md"
        f.parent.mkdir()
        f.touch()
        assert concept_id_from_path(f, tmp_path) == "libraries/thing"

    def test_nested(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.md"
        f.parent.mkdir(parents=True)
        f.touch()
        assert concept_id_from_path(f, tmp_path) == "a/b/c"


class TestIsConceptFile:
    def test_concept(self, tmp_path):
        assert is_concept_file(tmp_path / "foo.md")

    def test_index_excluded(self, tmp_path):
        assert not is_concept_file(tmp_path / "index.md")

    def test_log_excluded(self, tmp_path):
        assert not is_concept_file(tmp_path / "log.md")

    def test_non_md(self, tmp_path):
        assert not is_concept_file(tmp_path / "foo.txt")


class TestFrontmatter:
    def test_parse(self):
        text = "---\ntype: System\ntitle: Test\ntags: [a, b]\n---\n\n# Body\n"
        fm, body = parse_frontmatter(text)
        assert fm["type"] == "System"
        assert fm["title"] == "Test"
        assert fm["tags"] == ["a", "b"]
        assert "# Body" in body


class TestLinkExtractor:
    def test_absolute_link(self, tmp_path):
        body = "See the [API Gateway](/services/api-gateway.md) for details."
        links = extract_links(body, "libraries/foo", tmp_path, tmp_path / "libraries" / "foo.md")
        assert len(links) == 1
        assert links[0].target_concept_id == "services/api-gateway"

    def test_relative_link(self, tmp_path):
        body = "See [sibling](./sibling.md) here."
        links = extract_links(body, "libraries/foo", tmp_path, tmp_path / "libraries" / "foo.md")
        assert len(links) == 1
        assert links[0].target_concept_id == "libraries/sibling"

    def test_external_links_ignored(self, tmp_path):
        body = "See [Google](https://google.com/foo.md)."
        links = extract_links(body, "libraries/foo", tmp_path, tmp_path / "libraries" / "foo.md")
        assert len(links) == 0

    def test_dedup(self, tmp_path):
        body = "[A](/x.md) and [A again](/x.md)"
        links = extract_links(body, "libraries/foo", tmp_path, tmp_path / "libraries" / "foo.md")
        assert len(links) == 1

    def test_anchor_stripped(self, tmp_path):
        body = "[A](/x.md#section)"
        links = extract_links(body, "libraries/foo", tmp_path, tmp_path / "libraries" / "foo.md")
        assert links[0].target_concept_id == "x"


class TestChunker:
    def test_empty(self):
        assert chunk_body("") == []

    def test_single_section(self):
        chunks = chunk_body("# Heading\n\nSome text here.")
        assert len(chunks) == 1
        assert chunks[0].heading == "Heading"

    def test_multiple_headings(self):
        body = "# A\n\nText A\n\n# B\n\nText B"
        chunks = chunk_body(body)
        assert len(chunks) == 2
        assert chunks[0].heading == "A"
        assert chunks[1].heading == "B"

    def test_preamble(self):
        body = "Preamble text\n\n# Heading\n\nBody"
        chunks = chunk_body(body)
        assert chunks[0].heading == ""
        assert chunks[1].heading == "Heading"


# ── store tests ─────────────────────────────────────────────────────

class TestKnowledgeGraph:
    @pytest.fixture
    def store(self, tmp_path):
        with KnowledgeGraph(tmp_path / "test.db") as s:
            yield s

    def _make_doc(self, concept_id="test/a", links=None, chunks=None):
        return Document(
            concept_id=concept_id,
            file_path=Path(f"/fake/{concept_id}.md"),
            frontmatter={"type": "Tool", "title": "Test A", "tags": ["x"]},
            body="# Test\n\nBody content here.",
            links=links or [],
            chunks=chunks or [Chunk(text="Body content here.", position=0, heading="Test")],
        )

    def test_upsert_and_get(self, store):
        doc = self._make_doc()
        assert store.upsert_document(doc) is True
        node = store.get_node("test/a")
        assert node is not None
        assert node["type"] == "Tool"
        assert node["title"] == "Test A"

    def test_skip_unchanged(self, store):
        doc = self._make_doc()
        store.upsert_document(doc)
        assert store.upsert_document(doc) is False

    def test_edges_and_backlinks(self, store):
        doc_a = self._make_doc("test/a", links=[Link("test/b", "B link", 1)])
        doc_b = self._make_doc("test/b", links=[Link("test/a", "A link", 1)])
        store.upsert_document(doc_a)
        store.upsert_document(doc_b)

        neighbors = store.get_neighbors("test/a")
        assert len(neighbors["outgoing"]) == 1
        assert neighbors["outgoing"][0]["concept_id"] == "test/b"
        assert len(neighbors["incoming"]) == 1
        assert neighbors["incoming"][0]["concept_id"] == "test/b"

    def test_delete(self, store):
        doc = self._make_doc()
        store.upsert_document(doc)
        assert store.delete_node("test/a") is True
        assert store.get_node("test/a") is None

    def test_chunks_stored(self, store):
        doc = self._make_doc()
        store.upsert_document(doc)
        chunks = store.get_chunks("test/a")
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Body content here."

    def test_stats(self, store):
        store.upsert_document(self._make_doc("a"))
        store.upsert_document(self._make_doc("b", links=[Link("a", "link", 1)]))
        s = store.stats()
        assert s["nodes"] == 2
        assert s["edges"] == 1
        assert s["backlinks"] == 1


# ── integration test on real bundle ─────────────────────────────────

class TestBundleIngestion:
    @pytest.fixture
    def ingested_store(self, tmp_path):
        db_path = tmp_path / "test.db"
        with KnowledgeGraph(db_path) as store:
            ingest_bundle(BUNDLE_ROOT, store)
            yield store

    def test_node_count(self, ingested_store):
        ids = ingested_store.all_concept_ids()
        assert len(ids) == 21

    def test_api_gateway_is_hub(self, ingested_store):
        neighbors = ingested_store.get_neighbors("services/api-gateway")
        assert len(neighbors["incoming"]) >= 10

    def test_all_nodes_have_type(self, ingested_store):
        for cid in ingested_store.all_concept_ids():
            node = ingested_store.get_node(cid)
            assert node["type"], f"{cid} has no type"

    def test_all_nodes_have_chunks(self, ingested_store):
        for cid in ingested_store.all_concept_ids():
            chunks = ingested_store.get_chunks(cid)
            assert len(chunks) > 0, f"{cid} has no chunks"

    def test_bidirectional_links(self, ingested_store):
        """Verify that for each outgoing edge A->B, there's a backlink B->A."""
        rows = ingested_store.conn.execute(
            "SELECT source_id, target_id FROM edges WHERE direction='outgoing'"
        ).fetchall()
        for row in rows:
            backlink = ingested_store.conn.execute(
                "SELECT 1 FROM edges WHERE source_id=? AND target_id=? AND direction='backlink'",
                (row["target_id"], row["source_id"]),
            ).fetchone()
            assert backlink, f"Missing backlink for {row['source_id']} -> {row['target_id']}"

    def test_incremental_skip(self, ingested_store):
        result = ingest_bundle(BUNDLE_ROOT, ingested_store)
        assert result["updated"] == 0
        assert result["skipped_unchanged"] == 21

    def test_cross_project_chain(self, ingested_store):
        """Verify a multi-hop chain: sdk-client -> api-gateway -> event-bus -> notification-engine."""
        n1 = ingested_store.get_neighbors("libraries/sdk-client")
        targets_1 = {n["concept_id"] for n in n1["outgoing"]}
        assert "services/api-gateway" in targets_1

        n2 = ingested_store.get_neighbors("services/api-gateway")
        targets_2 = {n["concept_id"] for n in n2["outgoing"]}
        assert "services/event-bus" in targets_2

        n3 = ingested_store.get_neighbors("services/event-bus")
        targets_3 = {n["concept_id"] for n in n3["outgoing"]}
        assert "services/notification-engine" in targets_3
