from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from ..search.embedding import get_client
from ..parsing.okf_parser import discover_documents, parse_document, parse_frontmatter

DEFAULT_LLM_MODEL = "gpt-4.1"

OKF_TYPES = ["System", "Tool", "Process", "Playbook", "Model", "Reference", "Pattern"]

SYSTEM_PROMPT = """\
You are a technical documentation converter. Your job is to convert a raw \
documentation file into an OKF (Open Knowledge Format) markdown file.

OKF format has two parts:
1. YAML frontmatter (between --- delimiters) with these fields:
   - type: (REQUIRED) one of: {types}
   - title: short descriptive title
   - description: one-sentence summary
   - tags: [list, of, lowercase, tags]
   - status: stable | draft | deprecated
   - generated: {{ by: llm:gpt-4.1, at: {timestamp} }}
   - sources: list of source references, each with id, resource, and title

2. Markdown body:
   - Well-structured with headings (## level)
   - Includes links to related existing concepts using relative paths like \
[Label](../category/concept-name.md)
   - Preserves technical details: commands, code snippets, config values, procedures
   - Concise but complete — distill the raw doc, don't just copy it verbatim

Rules:
- Pick the most appropriate type from the allowed list
- Link to existing concepts ONLY when genuinely related — don't force links
- Use relative paths for links (e.g. ../services/api-gateway.md, ../libraries/sdk-client.md)
- The sources field must reference the raw document being converted
- Keep the body informative and well-organized
- Do NOT wrap the output in markdown code fences — output the raw OKF file directly"""


@dataclass
class ConversionResult:
    output_path: Path
    concept_id: str
    okf_content: str
    links_to: list[str]
    source_path: str


@dataclass
class BatchResult:
    converted: list[ConversionResult] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


def _build_concept_index(bundle_root: Path) -> list[dict]:
    docs = discover_documents(bundle_root)
    concepts = []
    for doc_path in docs:
        try:
            text = doc_path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            rel = doc_path.relative_to(bundle_root)
            concept_id = str(rel.with_suffix(""))
            concepts.append({
                "concept_id": concept_id,
                "path": str(rel),
                "type": fm.get("type", ""),
                "title": fm.get("title", ""),
                "description": fm.get("description", ""),
            })
        except Exception:
            continue
    return concepts


def _format_concept_list(concepts: list[dict]) -> str:
    lines = []
    for c in concepts:
        desc = f" — {c['description']}" if c['description'] else ""
        lines.append(f"- {c['concept_id']} [{c['type']}]: {c['title']}{desc}")
    return "\n".join(lines)


def _determine_output_subdir(okf_type: str) -> str:
    mapping = {
        "System": "services",
        "Tool": "libraries",
        "Process": "workflows",
        "Playbook": "workflows",
        "Model": "libraries",
        "Reference": "concepts",
        "Pattern": "concepts",
    }
    return mapping.get(okf_type, "libraries")


def _extract_type_from_response(content: str) -> str:
    match = re.search(r"^type:\s*(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_links_from_response(content: str) -> list[str]:
    link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+\.md(?:#[^)]*)?)\)")
    concept_ids = []
    for match in link_pattern.finditer(content):
        target = match.group(1).split("#")[0]
        if target.startswith("http"):
            continue
        clean = re.sub(r"^(\.\./)+", "", target)
        concept_id = clean.replace(".md", "")
        if concept_id:
            concept_ids.append(concept_id)
    return concept_ids


def _slugify(name: str, parent_dir: str = "") -> str:
    stem = Path(name).stem
    if stem.lower() in ("readme", "index") and parent_dir:
        stem = Path(parent_dir).name
    slug = stem.lower().replace("_", "-").replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def convert_document(
    raw_path: Path,
    bundle_root: Path,
    output_name: str | None = None,
    output_subdir: str | None = None,
    client: OpenAI | None = None,
    llm_model: str = DEFAULT_LLM_MODEL,
    existing_concepts: list[dict] | None = None,
    dry_run: bool = False,
) -> ConversionResult:
    if client is None:
        client = get_client()

    raw_content = raw_path.read_text(encoding="utf-8")

    if existing_concepts is None:
        existing_concepts = _build_concept_index(bundle_root)

    concept_list = _format_concept_list(existing_concepts)

    try:
        project_root = bundle_root.parent.parent
        source_resource = "/" + str(raw_path.relative_to(project_root))
    except ValueError:
        source_resource = str(raw_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    system = SYSTEM_PROMPT.format(
        types=", ".join(OKF_TYPES),
        timestamp=timestamp,
    )

    user_msg = f"""Convert the following raw document to OKF format.

Source file: {raw_path.name}
Source resource path (for the sources field): {source_resource}

Existing concepts in the bundle (link to these where relevant):
{concept_list}

The output concept will be placed in the bundle at: {bundle_root.name}/
Use relative links like ../services/api-gateway.md or ../libraries/sdk-client.md

---

Raw document content:

{raw_content}"""

    response = client.chat.completions.create(
        model=llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
    )

    okf_content = response.choices[0].message.content.strip()

    if okf_content.startswith("```"):
        lines = okf_content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        okf_content = "\n".join(lines)

    detected_type = _extract_type_from_response(okf_content)

    if output_name is None:
        output_name = _slugify(raw_path.name, parent_dir=str(raw_path.parent)) + ".md"
    if not output_name.endswith(".md"):
        output_name += ".md"

    if output_subdir is None:
        output_subdir = _determine_output_subdir(detected_type)

    output_path = bundle_root / output_subdir / output_name
    concept_id = f"{output_subdir}/{output_name.replace('.md', '')}"

    links = _extract_links_from_response(okf_content)

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(okf_content + "\n", encoding="utf-8")

    return ConversionResult(
        output_path=output_path,
        concept_id=concept_id,
        okf_content=okf_content,
        links_to=links,
        source_path=source_resource,
    )


def discover_raw_docs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        extensions = {".md", ".rst", ".txt"}
        files = sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix in extensions
        )
        return files
    return []


def convert_batch(
    paths: list[Path],
    bundle_root: Path,
    client: OpenAI | None = None,
    llm_model: str = DEFAULT_LLM_MODEL,
    dry_run: bool = False,
) -> BatchResult:
    if client is None:
        client = get_client()

    raw_files: list[Path] = []
    for p in paths:
        raw_files.extend(discover_raw_docs(p))

    if not raw_files:
        return BatchResult()

    existing_concepts = _build_concept_index(bundle_root)
    result = BatchResult()

    for raw_path in raw_files:
        try:
            content = raw_path.read_text(encoding="utf-8")
        except Exception as e:
            result.errors.append((raw_path, f"Cannot read: {e}"))
            continue

        if len(content.strip()) < 50:
            result.skipped.append((raw_path, "Too short (< 50 chars)"))
            continue

        try:
            conversion = convert_document(
                raw_path=raw_path,
                bundle_root=bundle_root,
                client=client,
                llm_model=llm_model,
                existing_concepts=existing_concepts,
                dry_run=dry_run,
            )
            result.converted.append(conversion)

            fm_text = conversion.okf_content
            try:
                fm, _ = parse_frontmatter(fm_text)
            except Exception:
                fm = {}

            existing_concepts.append({
                "concept_id": conversion.concept_id,
                "path": f"{conversion.concept_id}.md",
                "type": fm.get("type", ""),
                "title": fm.get("title", conversion.concept_id),
                "description": fm.get("description", ""),
            })

        except Exception as e:
            result.errors.append((raw_path, str(e)))

    return result
