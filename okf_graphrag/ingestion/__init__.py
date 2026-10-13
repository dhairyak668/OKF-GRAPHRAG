from __future__ import annotations

from pathlib import Path

from ..parsing.okf_parser import concept_id_from_path, discover_documents, parse_document
from ..graph.store import KnowledgeGraph


def ingest_bundle(bundle_root: Path, store: KnowledgeGraph) -> dict:
    bundle_root = bundle_root.resolve()
    files = discover_documents(bundle_root)

    file_concept_ids = set()
    updated = 0
    skipped = 0

    for f in files:
        doc = parse_document(f, bundle_root)
        file_concept_ids.add(doc.concept_id)

        if store.upsert_document(doc):
            updated += 1
        else:
            skipped += 1

    existing_ids = set(store.all_concept_ids())
    removed_ids = existing_ids - file_concept_ids
    deleted = 0
    for cid in removed_ids:
        if store.delete_node(cid):
            deleted += 1

    return {
        "files_found": len(files),
        "updated": updated,
        "skipped_unchanged": skipped,
        "deleted": deleted,
    }
