from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class Link:
    target_concept_id: str
    label: str
    source_line: int


@dataclass
class Chunk:
    text: str
    position: int
    heading: str


@dataclass
class Document:
    concept_id: str
    file_path: Path
    frontmatter: dict
    body: str
    links: list[Link] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def type(self) -> str:
        return self.frontmatter.get("type", "")

    @property
    def title(self) -> str:
        return self.frontmatter.get("title", "")

    @property
    def description(self) -> str:
        return self.frontmatter.get("description", "")

    @property
    def tags(self) -> list[str]:
        return self.frontmatter.get("tags", [])

    @property
    def status(self) -> str:
        return self.frontmatter.get("status", "")
