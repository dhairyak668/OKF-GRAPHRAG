from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ..parsing.models import Document
from .tables import SCHEMA_SQL


class KnowledgeGraph:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> KnowledgeGraph:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_document(self, doc: Document) -> bool:
        body_hash = hashlib.sha256(doc.body.encode()).hexdigest()[:16]

        existing = self.conn.execute(
            "SELECT body_hash FROM nodes WHERE concept_id = ?",
            (doc.concept_id,),
        ).fetchone()

        if existing and existing["body_hash"] == body_hash:
            return False

        with self.conn:
            self.conn.execute(
                """INSERT INTO nodes (concept_id, type, title, description, tags, status, frontmatter, body_hash, file_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(concept_id) DO UPDATE SET
                     type=excluded.type, title=excluded.title,
                     description=excluded.description, tags=excluded.tags,
                     status=excluded.status, frontmatter=excluded.frontmatter,
                     body_hash=excluded.body_hash, file_path=excluded.file_path""",
                (
                    doc.concept_id,
                    doc.type,
                    doc.title,
                    doc.description,
                    json.dumps(doc.tags),
                    doc.status,
                    json.dumps(doc.frontmatter, default=str),
                    body_hash,
                    str(doc.file_path),
                ),
            )

            self.conn.execute(
                "DELETE FROM edges WHERE source_id = ? AND direction = 'outgoing'",
                (doc.concept_id,),
            )
            self.conn.execute(
                "DELETE FROM edges WHERE target_id = ? AND direction = 'backlink'",
                (doc.concept_id,),
            )

            for link in doc.links:
                self.conn.execute(
                    """INSERT OR IGNORE INTO edges (source_id, target_id, direction, label)
                       VALUES (?, ?, 'outgoing', ?)""",
                    (doc.concept_id, link.target_concept_id, link.label),
                )
                self.conn.execute(
                    """INSERT OR IGNORE INTO edges (source_id, target_id, direction, label)
                       VALUES (?, ?, 'backlink', ?)""",
                    (link.target_concept_id, doc.concept_id, link.label),
                )

            self.conn.execute(
                "DELETE FROM chunks WHERE concept_id = ?",
                (doc.concept_id,),
            )
            for chunk in doc.chunks:
                self.conn.execute(
                    """INSERT INTO chunks (concept_id, text, position, heading)
                       VALUES (?, ?, ?, ?)""",
                    (doc.concept_id, chunk.text, chunk.position, chunk.heading),
                )

        return True

    def delete_node(self, concept_id: str) -> bool:
        existing = self.conn.execute(
            "SELECT 1 FROM nodes WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        if not existing:
            return False

        with self.conn:
            self.conn.execute(
                "DELETE FROM vectors WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE concept_id = ?)",
                (concept_id,),
            )
            self.conn.execute("DELETE FROM chunks WHERE concept_id = ?", (concept_id,))
            self.conn.execute(
                "DELETE FROM edges WHERE source_id = ? OR target_id = ?",
                (concept_id, concept_id),
            )
            self.conn.execute("DELETE FROM nodes WHERE concept_id = ?", (concept_id,))
        return True

    def get_node(self, concept_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_neighbors(self, concept_id: str) -> dict[str, list[dict]]:
        outgoing = self.conn.execute(
            """SELECT e.target_id as concept_id, e.label, n.title, n.type
               FROM edges e LEFT JOIN nodes n ON e.target_id = n.concept_id
               WHERE e.source_id = ? AND e.direction = 'outgoing'""",
            (concept_id,),
        ).fetchall()

        incoming = self.conn.execute(
            """SELECT e.source_id as concept_id, e.label, n.title, n.type
               FROM edges e LEFT JOIN nodes n ON e.source_id = n.concept_id
               WHERE e.target_id = ? AND e.direction = 'outgoing'""",
            (concept_id,),
        ).fetchall()

        return {
            "outgoing": [dict(r) for r in outgoing],
            "incoming": [dict(r) for r in incoming],
        }

    def get_chunks(self, concept_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE concept_id = ? ORDER BY position",
            (concept_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_concept_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT concept_id FROM nodes ORDER BY concept_id").fetchall()
        return [r["concept_id"] for r in rows]

    def stats(self) -> dict:
        nodes = self.conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()["c"]
        edges = self.conn.execute("SELECT COUNT(*) as c FROM edges WHERE direction='outgoing'").fetchone()["c"]
        backlinks = self.conn.execute("SELECT COUNT(*) as c FROM edges WHERE direction='backlink'").fetchone()["c"]
        chunks = self.conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        return {
            "nodes": nodes,
            "edges": edges,
            "backlinks": backlinks,
            "chunks": chunks,
        }
