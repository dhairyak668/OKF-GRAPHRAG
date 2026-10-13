from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from ..graph.store import KnowledgeGraph
from .embedding import embed_query
from .vectors import load_all_vectors


@dataclass
class SearchResult:
    chunk_id: int
    concept_id: str
    text: str
    heading: str
    score: float
    method: str


def vector_search(
    query_embedding: np.ndarray,
    store: KnowledgeGraph,
    top_k: int = 10,
) -> list[SearchResult]:
    chunk_ids, embeddings = load_all_vectors(store)
    if len(chunk_ids) == 0:
        return []

    query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-10)
    emb_norms = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10)
    similarities = emb_norms @ query_norm

    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        cid = chunk_ids[idx]
        chunk = store.conn.execute(
            "SELECT concept_id, text, heading FROM chunks WHERE chunk_id = ?",
            (cid,),
        ).fetchone()
        if chunk:
            results.append(SearchResult(
                chunk_id=cid,
                concept_id=chunk["concept_id"],
                text=chunk["text"],
                heading=chunk["heading"],
                score=float(similarities[idx]),
                method="vector",
            ))
    return results


_TOKENIZE_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or the "
    "to was were with that this".split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKENIZE_RE.findall(text.lower()) if t not in _STOPWORDS]


def bm25_search(
    query: str,
    store: KnowledgeGraph,
    top_k: int = 10,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[SearchResult]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    rows = store.conn.execute(
        "SELECT chunk_id, concept_id, text, heading FROM chunks"
    ).fetchall()
    if not rows:
        return []

    docs: list[dict] = [dict(r) for r in rows]
    doc_lengths = [len(_tokenize(d["text"])) for d in docs]
    avg_dl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1
    n_docs = len(docs)

    df: Counter[str] = Counter()
    doc_tfs: list[Counter[str]] = []
    for d in docs:
        tokens = _tokenize(d["text"])
        tf = Counter(tokens)
        doc_tfs.append(tf)
        df.update(set(tokens))

    scores = []
    for i, (doc, tf, dl) in enumerate(zip(docs, doc_tfs, doc_lengths)):
        score = 0.0
        for qt in query_tokens:
            if qt not in df:
                continue
            idf = math.log((n_docs - df[qt] + 0.5) / (df[qt] + 0.5) + 1)
            tf_val = tf.get(qt, 0)
            numerator = tf_val * (k1 + 1)
            denominator = tf_val + k1 * (1 - b + b * dl / avg_dl)
            score += idf * numerator / denominator
        scores.append((score, i))

    scores.sort(reverse=True)
    results = []
    for score, i in scores[:top_k]:
        if score <= 0:
            break
        d = docs[i]
        results.append(SearchResult(
            chunk_id=d["chunk_id"],
            concept_id=d["concept_id"],
            text=d["text"],
            heading=d["heading"],
            score=score,
            method="bm25",
        ))
    return results


def hybrid_search(
    query: str,
    store: KnowledgeGraph,
    client=None,
    top_k: int = 10,
    vector_weight: float = 0.7,
    bm25_weight: float = 0.3,
) -> list[SearchResult]:
    bm25_results = bm25_search(query, store, top_k=top_k * 2)

    query_emb = embed_query(query, client=client)
    vec_results = vector_search(query_emb, store, top_k=top_k * 2)

    def _normalize(results: list[SearchResult]) -> dict[int, float]:
        if not results:
            return {}
        max_score = max(r.score for r in results) or 1.0
        return {r.chunk_id: r.score / max_score for r in results}

    vec_scores = _normalize(vec_results)
    bm25_scores = _normalize(bm25_results)

    all_chunk_ids = set(vec_scores) | set(bm25_scores)
    combined: list[tuple[int, float]] = []
    for cid in all_chunk_ids:
        score = (
            vector_weight * vec_scores.get(cid, 0.0)
            + bm25_weight * bm25_scores.get(cid, 0.0)
        )
        combined.append((cid, score))

    combined.sort(key=lambda x: x[1], reverse=True)

    chunk_lookup = {r.chunk_id: r for r in vec_results + bm25_results}
    results = []
    for cid, score in combined[:top_k]:
        base = chunk_lookup[cid]
        results.append(SearchResult(
            chunk_id=cid,
            concept_id=base.concept_id,
            text=base.text,
            heading=base.heading,
            score=score,
            method="hybrid",
        ))
    return results
