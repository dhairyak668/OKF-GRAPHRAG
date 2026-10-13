from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..graph.store import KnowledgeGraph


@dataclass
class ResolvedSource:
    concept_id: str
    title: str
    source_id: str
    resource_path: str
    source_title: str
    content: str
    exists: bool
    url: str = ""


def _get_sources_from_frontmatter(frontmatter_json: str) -> list[dict]:
    fm = json.loads(frontmatter_json) if frontmatter_json else {}
    return fm.get("sources", [])


def load_repo_urls(project_root: Path) -> dict[str, str]:
    urls_path = project_root / "repo_urls.json"
    if not urls_path.is_file():
        return {}
    return json.loads(urls_path.read_text(encoding="utf-8"))


def resource_path_to_url(resource_path: str, repo_urls: dict[str, str]) -> str:
    parts = resource_path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "source_docs":
        return ""
    repo_name = parts[1]
    file_path = "/".join(parts[2:])
    base_url = repo_urls.get(repo_name, "")
    if not base_url:
        return ""
    return f"{base_url}/{file_path}"


def resolve_sources(
    concept_ids: list[str],
    store: KnowledgeGraph,
    project_root: Path,
    repo_urls: dict[str, str] | None = None,
) -> list[ResolvedSource]:
    if repo_urls is None:
        repo_urls = load_repo_urls(project_root)

    resolved: list[ResolvedSource] = []
    seen_resources: set[str] = set()

    for cid in concept_ids:
        node = store.get_node(cid)
        if not node:
            continue

        sources = _get_sources_from_frontmatter(node["frontmatter"])
        if not sources:
            continue

        for src in sources:
            resource = src.get("resource", "")
            if not resource or resource in seen_resources:
                continue
            seen_resources.add(resource)

            raw_path = project_root / resource.lstrip("/")
            exists = raw_path.is_file()
            content = ""
            if exists:
                content = raw_path.read_text(encoding="utf-8")

            url = resource_path_to_url(resource, repo_urls)

            resolved.append(ResolvedSource(
                concept_id=cid,
                title=node["title"] or cid,
                source_id=src.get("id", ""),
                resource_path=resource,
                source_title=src.get("title", ""),
                content=content,
                exists=exists,
                url=url,
            ))

    return resolved
