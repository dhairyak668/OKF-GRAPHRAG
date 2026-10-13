from __future__ import annotations

import numpy as np

from ..graph.store import KnowledgeGraph


def store_embedding(store: KnowledgeGraph, chunk_id: int, embedding: np.ndarray) -> None:
    blob = embedding.astype(np.float32).tobytes()
    store.conn.execute(
        "INSERT OR REPLACE INTO vectors (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, blob),
    )
    store.conn.commit()


def store_embeddings_bulk(
    store: KnowledgeGraph, chunk_ids: list[int], embeddings: np.ndarray
) -> None:
    with store.conn:
        for cid, emb in zip(chunk_ids, embeddings):
            blob = emb.astype(np.float32).tobytes()
            store.conn.execute(
                "INSERT OR REPLACE INTO vectors (chunk_id, embedding) VALUES (?, ?)",
                (cid, blob),
            )


def load_all_vectors(store: KnowledgeGraph) -> tuple[list[int], np.ndarray]:
    rows = store.conn.execute(
        "SELECT chunk_id, embedding FROM vectors WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return [], np.array([], dtype=np.float32)

    chunk_ids = [r["chunk_id"] for r in rows]
    embeddings = np.stack(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    )
    return chunk_ids, embeddings


def get_unembedded_chunks(store: KnowledgeGraph) -> list[dict]:
    rows = store.conn.execute(
        """SELECT c.chunk_id, c.concept_id, c.text, c.heading
           FROM chunks c LEFT JOIN vectors v ON c.chunk_id = v.chunk_id
           WHERE v.chunk_id IS NULL
           ORDER BY c.concept_id, c.position"""
    ).fetchall()
    return [dict(r) for r in rows]


def embedding_stats(store: KnowledgeGraph) -> dict:
    total_chunks = store.conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
    embedded = store.conn.execute("SELECT COUNT(*) as c FROM vectors").fetchone()["c"]
    return {"total_chunks": total_chunks, "embedded": embedded, "remaining": total_chunks - embedded}
