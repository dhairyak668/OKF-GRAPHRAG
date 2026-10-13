from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import frontmatter

from .models import Chunk, Document, Link

RESERVED_FILENAMES = {"index.md", "log.md"}

LINK_PATTERN = re.compile(
    r"\[(?P<label>[^\]]*)\]"
    r"\((?P<target>[^)]+\.md(?:#[^)]*)?)\)"
)

DEFAULT_CHUNK_TARGET = 800
CHUNK_MIN = 200


def concept_id_from_path(file_path: Path, bundle_root: Path) -> str:
    rel = file_path.relative_to(bundle_root)
    return str(PurePosixPath(rel.with_suffix("")))


def is_concept_file(file_path: Path) -> bool:
    return file_path.suffix == ".md" and file_path.name not in RESERVED_FILENAMES


def parse_frontmatter(text: str) -> tuple[dict, str]:
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content


def extract_links(
    body: str,
    source_concept_id: str,
    bundle_root: Path,
    source_file: Path,
) -> list[Link]:
    links: list[Link] = []
    seen_targets: set[str] = set()

    for line_num, line in enumerate(body.splitlines(), start=1):
        for m in LINK_PATTERN.finditer(line):
            raw_target = m.group("target").split("#")[0]
            if raw_target.startswith("http://") or raw_target.startswith("https://"):
                continue

            resolved = _resolve_link_target(raw_target, source_file, bundle_root)
            if resolved is None:
                continue

            if resolved not in seen_targets:
                seen_targets.add(resolved)
                links.append(Link(
                    target_concept_id=resolved,
                    label=m.group("label"),
                    source_line=line_num,
                ))
    return links


def _resolve_link_target(
    raw_target: str, source_file: Path, bundle_root: Path
) -> str | None:
    if raw_target.startswith("/"):
        resolved = PurePosixPath(raw_target.lstrip("/"))
    else:
        source_dir = PurePosixPath(source_file.relative_to(bundle_root)).parent
        resolved = (source_dir / raw_target)

    parts = []
    for part in resolved.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    resolved = PurePosixPath(*parts) if parts else PurePosixPath(".")

    concept_id = str(resolved.with_suffix(""))
    if concept_id == ".":
        return None
    return concept_id


def chunk_body(body: str, target_size: int = DEFAULT_CHUNK_TARGET) -> list[Chunk]:
    if not body.strip():
        return []

    sections = _split_by_headings(body)
    chunks: list[Chunk] = []
    position = 0

    for heading, text in sections:
        text = text.strip()
        if not text:
            continue

        if len(text) <= target_size * 1.5:
            chunks.append(Chunk(text=text, position=position, heading=heading))
            position += 1
        else:
            for sub in _split_long_section(text, target_size):
                chunks.append(Chunk(text=sub, position=position, heading=heading))
                position += 1

    return chunks


def _split_by_headings(body: str) -> list[tuple[str, str]]:
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    sections: list[tuple[str, str]] = []

    matches = list(heading_pattern.finditer(body))
    if not matches:
        return [("", body)]

    if matches[0].start() > 0:
        preamble = body[: matches[0].start()]
        if preamble.strip():
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((heading, body[start:end]))

    return sections


def _split_long_section(text: str, target_size: int) -> list[str]:
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > target_size and current_len >= CHUNK_MIN:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        if chunks and current_len < CHUNK_MIN:
            chunks[-1] += "\n\n" + "\n\n".join(current)
        else:
            chunks.append("\n\n".join(current))

    return chunks


def parse_document(file_path: Path, bundle_root: Path) -> Document:
    text = file_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    concept_id = concept_id_from_path(file_path, bundle_root)

    links = extract_links(body, concept_id, bundle_root, file_path)
    chunks = chunk_body(body)

    return Document(
        concept_id=concept_id,
        file_path=file_path,
        frontmatter=fm,
        body=body,
        links=links,
        chunks=chunks,
    )


def discover_documents(bundle_root: Path) -> list[Path]:
    return sorted(
        p for p in bundle_root.rglob("*.md") if is_concept_file(p)
    )
