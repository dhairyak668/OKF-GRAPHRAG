from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from ..search.embedding import get_client
from ..retrieval.engine import RetrievalResult, retrieve
from ..graph.store import KnowledgeGraph
from .sources import ResolvedSource, resolve_sources

DEFAULT_LLM_MODEL = "gpt-4.1"

SYSTEM_PROMPT = """\
You are a technical documentation assistant. Your job is to answer the user's \
question using ONLY the source documents provided below. These documents are \
the ground truth.

Rules:
- Answer strictly from the provided documents. Do not invent information.
- If the documents do not contain enough information to fully answer, say so \
explicitly and answer what you can.
- Be specific: include commands, configuration values, file paths, and code \
snippets from the documents when relevant.
- Keep answers concise but complete.
- Do NOT add a references section — the system appends that automatically."""


@dataclass
class SourceReference:
    title: str
    resource_path: str
    concept_id: str
    url: str = ""


@dataclass
class QueryResult:
    query: str
    answer: str
    references: list[SourceReference]
    retrieval: RetrievalResult
    raw_sources_used: int
    raw_sources_chars: int

    def format(self) -> str:
        parts = [self.answer, "", "---", "References (source documents):"]
        for i, ref in enumerate(self.references, 1):
            if ref.url:
                parts.append(f"  {i}. {ref.title}")
                parts.append(f"     {ref.url}")
            else:
                parts.append(f"  {i}. {ref.title} — {ref.resource_path}")
        return "\n".join(parts)


def _build_source_context(sources: list[ResolvedSource]) -> str:
    parts: list[str] = []
    for i, src in enumerate(sources, 1):
        parts.append(f"=== Document {i}: {src.source_title or src.title} ===")
        parts.append(f"Source: {src.resource_path}")
        parts.append(f"Related concept: {src.concept_id}")
        parts.append("")
        parts.append(src.content)
        parts.append("")
    return "\n".join(parts)


def ask(
    query: str,
    store: KnowledgeGraph,
    project_root: Path,
    client: OpenAI | None = None,
    top_k: int = 5,
    max_hops: int = 1,
    llm_model: str = DEFAULT_LLM_MODEL,
    type_filter: list[str] | None = None,
    tag_filter: list[str] | None = None,
) -> QueryResult:
    if client is None:
        client = get_client()

    retrieval = retrieve(
        query, store, client=client, top_k=top_k, max_hops=max_hops,
        type_filter=type_filter, tag_filter=tag_filter,
    )

    concept_ids = [c.concept_id for c in retrieval.concepts]
    sources = resolve_sources(concept_ids, store, project_root)
    available_sources = [s for s in sources if s.exists and s.content]

    if not available_sources:
        return QueryResult(
            query=query,
            answer="I couldn't find any source documents to answer this question. "
                   "The retrieval found matching concepts but their source documents are unavailable.",
            references=[],
            retrieval=retrieval,
            raw_sources_used=0,
            raw_sources_chars=0,
        )

    source_context = _build_source_context(available_sources)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Source documents:\n\n{source_context}\n\nQuestion: {query}"},
    ]

    response = client.chat.completions.create(
        model=llm_model,
        messages=messages,
        temperature=0.1,
    )

    answer_text = response.choices[0].message.content

    references = [
        SourceReference(
            title=s.source_title or s.title,
            resource_path=s.resource_path,
            concept_id=s.concept_id,
            url=s.url,
        )
        for s in available_sources
    ]

    return QueryResult(
        query=query,
        answer=answer_text,
        references=references,
        retrieval=retrieval,
        raw_sources_used=len(available_sources),
        raw_sources_chars=sum(len(s.content) for s in available_sources),
    )
