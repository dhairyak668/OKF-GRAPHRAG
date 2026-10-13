from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ingestion import ingest_bundle
from .ingestion.converter import convert_batch, convert_document, discover_raw_docs
from .graph.store import KnowledgeGraph

DEFAULT_DB = "okf_graphrag.db"


def cmd_ingest(args: argparse.Namespace) -> None:
    bundle_root = Path(args.bundle).resolve()
    if not bundle_root.is_dir():
        print(f"Error: {bundle_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.db)
    with KnowledgeGraph(db_path) as store:
        result = ingest_bundle(bundle_root, store)

    print(f"Ingested {bundle_root.name}/")
    print(f"  files found:       {result['files_found']}")
    print(f"  updated/inserted:  {result['updated']}")
    print(f"  skipped unchanged: {result['skipped_unchanged']}")
    print(f"  deleted:           {result['deleted']}")

    with KnowledgeGraph(db_path) as store:
        s = store.stats()
    print(f"\nDatabase totals:")
    print(f"  nodes:    {s['nodes']}")
    print(f"  edges:    {s['edges']}")
    print(f"  backlinks:{s['backlinks']}")
    print(f"  chunks:   {s['chunks']}")


def cmd_show(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    with KnowledgeGraph(db_path) as store:
        node = store.get_node(args.concept_id)
        if not node:
            print(f"Concept '{args.concept_id}' not found.", file=sys.stderr)
            available = store.all_concept_ids()
            if available:
                print(f"\nAvailable concepts ({len(available)}):")
                for cid in available:
                    print(f"  {cid}")
            sys.exit(1)

        neighbors = store.get_neighbors(args.concept_id)
        chunks = store.get_chunks(args.concept_id)

    tags = json.loads(node["tags"]) if node["tags"] else []

    print(f"=== {node['title'] or node['concept_id']} ===")
    print(f"  id:          {node['concept_id']}")
    print(f"  type:        {node['type']}")
    if node["description"]:
        print(f"  description: {node['description']}")
    if tags:
        print(f"  tags:        {', '.join(tags)}")
    if node["status"]:
        print(f"  status:      {node['status']}")

    if neighbors["outgoing"]:
        print(f"\n  Outgoing links ({len(neighbors['outgoing'])}):")
        for n in neighbors["outgoing"]:
            title = n["title"] or n["concept_id"]
            print(f"    -> {title} [{n['type'] or '?'}]")

    if neighbors["incoming"]:
        print(f"\n  Incoming links ({len(neighbors['incoming'])}):")
        for n in neighbors["incoming"]:
            title = n["title"] or n["concept_id"]
            print(f"    <- {title} [{n['type'] or '?'}]")

    if chunks:
        print(f"\n  Chunks ({len(chunks)}):")
        for c in chunks:
            preview = c["text"][:80].replace("\n", " ")
            heading = f" [{c['heading']}]" if c["heading"] else ""
            print(f"    #{c['position']}{heading}: {preview}...")


def cmd_embed(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found. Run 'ingest' first.", file=sys.stderr)
        sys.exit(1)

    from .search.embedding import embed_texts, get_client
    from .search.vectors import get_unembedded_chunks, store_embeddings_bulk, embedding_stats

    with KnowledgeGraph(db_path) as store:
        unembedded = get_unembedded_chunks(store)
        if not unembedded:
            stats = embedding_stats(store)
            print(f"All {stats['embedded']} chunks already embedded. Nothing to do.")
            return

        print(f"Embedding {len(unembedded)} chunks...")
        client = get_client()
        texts = [c["text"] for c in unembedded]
        chunk_ids = [c["chunk_id"] for c in unembedded]

        embeddings = embed_texts(texts, client=client)
        store_embeddings_bulk(store, chunk_ids, embeddings)

        stats = embedding_stats(store)
        print(f"Done. {stats['embedded']}/{stats['total_chunks']} chunks now have embeddings.")


def cmd_search(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    from .search.ranking import bm25_search, hybrid_search, vector_search
    from .search.vectors import embedding_stats

    with KnowledgeGraph(db_path) as store:
        stats = embedding_stats(store)
        has_vectors = stats["embedded"] > 0

        if args.mode == "bm25" or (args.mode == "hybrid" and not has_vectors):
            if args.mode == "hybrid" and not has_vectors:
                print("No embeddings found — falling back to BM25 keyword search.")
                print("Run 'embed' to enable vector + hybrid search.\n")
            results = bm25_search(args.query, store, top_k=args.top_k)
        elif args.mode == "vector":
            if not has_vectors:
                print("No embeddings found. Run 'embed' first.", file=sys.stderr)
                sys.exit(1)
            from .search.embedding import get_client, embed_query
            client = get_client()
            query_emb = embed_query(args.query, client=client)
            results = vector_search(query_emb, store, top_k=args.top_k)
        else:
            from .search.embedding import get_client
            client = get_client()
            results = hybrid_search(args.query, store, client=client, top_k=args.top_k)

    if not results:
        print("No results found.")
        return

    print(f"Search results for: \"{args.query}\" ({results[0].method})\n")
    seen_concepts: dict[str, float] = {}
    for r in results:
        if r.concept_id not in seen_concepts:
            seen_concepts[r.concept_id] = r.score

        heading = f" > {r.heading}" if r.heading else ""
        preview = r.text[:120].replace("\n", " ")
        print(f"  [{r.score:.3f}] {r.concept_id}{heading}")
        print(f"          {preview}...")
        print()

    unique = sorted(seen_concepts.items(), key=lambda x: x[1], reverse=True)
    print(f"Concepts matched ({len(unique)}):")
    for cid, score in unique:
        node = None
        with KnowledgeGraph(db_path) as store:
            node = store.get_node(cid)
        title = node["title"] if node else cid
        print(f"  [{score:.3f}] {title} ({cid})")


def cmd_retrieve(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    from .search.embedding import get_client
    from .retrieval.engine import retrieve

    client = get_client()

    with KnowledgeGraph(db_path) as store:
        type_filter = args.type.split(",") if args.type else None
        tag_filter = args.tags.split(",") if args.tags else None

        result = retrieve(
            args.query,
            store,
            client=client,
            top_k=args.top_k,
            max_hops=args.hops,
            type_filter=type_filter,
            tag_filter=tag_filter,
        )

    search_concepts = [c for c in result.concepts if c.source == "search"]
    graph_concepts = [c for c in result.concepts if c.source != "search"]

    print(f"=== Graph-Aware Retrieval ===")
    print(f"Query: \"{result.query}\"")
    print(f"Total concepts: {len(result.concepts)} ({len(search_concepts)} search + {len(graph_concepts)} graph-expanded)\n")

    for c in result.concepts:
        source_label = {"search": "SEARCH", "graph_1hop": "1-HOP", "graph_2hop": "2-HOP"}
        label = source_label.get(c.source, c.source)
        print(f"  [{c.score:.3f}] [{label:6s}] {c.title} ({c.type})")
        if c.source != "search":
            out_links = [n["title"] or n["concept_id"] for n in c.neighbors.get("outgoing", [])[:3]]
            in_links = [n["title"] or n["concept_id"] for n in c.neighbors.get("incoming", [])[:3]]
            connections = []
            if out_links:
                connections.append("→ " + ", ".join(out_links))
            if in_links:
                connections.append("← " + ", ".join(in_links))
            if connections:
                print(f"           connected via: {' | '.join(connections)}")

    if args.compare:
        print(f"\n=== Comparison: Layer 3 (hybrid only) vs Layer 4 (graph-aware) ===\n")
        from .search.ranking import hybrid_search
        with KnowledgeGraph(db_path) as store:
            l3_results = hybrid_search(args.query, store, client=client, top_k=args.top_k)

        l3_concepts = []
        seen = set()
        for r in l3_results:
            if r.concept_id not in seen:
                seen.add(r.concept_id)
                l3_concepts.append(r.concept_id)

        l4_concepts = [c.concept_id for c in result.concepts]

        l3_set = set(l3_concepts)
        l4_set = set(l4_concepts)
        only_l3 = l3_set - l4_set
        only_l4 = l4_set - l3_set
        both = l3_set & l4_set

        print(f"  Layer 3 found {len(l3_set)} concepts | Layer 4 found {len(l4_set)} concepts")
        print(f"  In both:      {len(both)}")
        print(f"  Only Layer 3: {len(only_l3)}{' — ' + ', '.join(only_l3) if only_l3 else ''}")
        print(f"  Only Layer 4: {len(only_l4)} (graph-expanded)")
        if only_l4:
            for cid in only_l4:
                c = next((x for x in result.concepts if x.concept_id == cid), None)
                if c:
                    print(f"    + {c.title} ({c.source})")

    if args.context:
        print(f"\n=== Context for LLM ===\n")
        print(result.context_text)


GROUND_TRUTH = [
    (
        "How do I configure the API Gateway for rate limiting?",
        {
            "services/api-gateway",
            "services/api-gateway-config",
            "services/auth-service",
            "concepts/rate-limiting",
        },
    ),
    (
        "What is the notification delivery architecture?",
        {
            "services/notification-engine",
            "services/notification-channels",
            "services/event-bus",
            "libraries/sdk-client",
        },
    ),
    (
        "How does data flow through the event-driven pipeline?",
        {
            "concepts/event-driven-architecture",
            "services/event-bus",
            "workflows/event-processing",
            "services/notification-engine",
        },
    ),
    (
        "How do I deploy a service using the CI/CD pipeline?",
        {
            "workflows/ci-cd-pipeline",
            "workflows/deployment-process",
            "services/api-gateway",
            "concepts/container-orchestration",
        },
    ),
    (
        "How does authentication and authorization work?",
        {
            "services/auth-service",
            "services/auth-tokens",
            "services/api-gateway",
            "libraries/sdk-client",
        },
    ),
]


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def cmd_compare(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    from .search.embedding import embed_query, get_client
    from .search.ranking import hybrid_search
    from .retrieval.engine import build_context, retrieve, RetrievedConcept

    client = get_client()

    with KnowledgeGraph(db_path) as store:
        # ── measure naive context (all documents) ──────────────────
        all_ids = store.all_concept_ids()
        naive_chars = 0
        for cid in all_ids:
            for chunk in store.get_chunks(cid):
                naive_chars += len(chunk["text"])
        naive_tokens = _estimate_tokens(" " * naive_chars)

        print("=" * 90)
        print("CONTEXT EFFICIENCY COMPARISON: Naive vs Layer 3 vs Layer 4")
        print("=" * 90)
        print(f"\nNaive baseline: all {len(all_ids)} concepts, {naive_chars:,} chars (~{naive_tokens:,} tokens)")
        print(f"(Stuffing everything into the prompt — no retrieval)\n")

        # ── per-query comparison ───────────────────────────────────
        top_k = args.top_k

        totals = {
            "naive": {"recall": 0, "chars": 0, "concepts": 0},
            "l3": {"recall": 0, "chars": 0, "concepts": 0},
            "l4": {"recall": 0, "chars": 0, "concepts": 0},
        }

        for query, truth in GROUND_TRUTH:
            print("-" * 90)
            print(f"Query: \"{query}\"")
            print(f"Ground truth ({len(truth)}): {', '.join(sorted(truth))}\n")

            # Naive: all docs
            naive_recall = 1.0  # by definition, all docs include all ground truth
            totals["naive"]["recall"] += naive_recall
            totals["naive"]["chars"] += naive_chars
            totals["naive"]["concepts"] += len(all_ids)

            # Layer 3: hybrid search
            l3_results = hybrid_search(query, store, client=client, top_k=top_k * 3)
            l3_concepts_seen: dict[str, float] = {}
            for r in l3_results:
                if r.concept_id not in l3_concepts_seen:
                    l3_concepts_seen[r.concept_id] = r.score
                if len(l3_concepts_seen) >= top_k:
                    break

            l3_concept_ids = set(l3_concepts_seen.keys())
            l3_chars = 0
            for cid in l3_concept_ids:
                for chunk in store.get_chunks(cid):
                    l3_chars += len(chunk["text"])
            l3_recall = len(l3_concept_ids & truth) / len(truth)
            totals["l3"]["recall"] += l3_recall
            totals["l3"]["chars"] += l3_chars
            totals["l3"]["concepts"] += len(l3_concept_ids)

            # Layer 4: graph-aware retrieval
            l4_result = retrieve(
                query, store, client=client, top_k=top_k, max_hops=args.hops,
                max_context_concepts=top_k,
            )
            l4_concept_ids = {c.concept_id for c in l4_result.concepts[:top_k]}
            l4_chars = len(l4_result.context_text)
            l4_recall = len(l4_concept_ids & truth) / len(truth)
            totals["l4"]["recall"] += l4_recall
            totals["l4"]["chars"] += l4_chars
            totals["l4"]["concepts"] += len(l4_concept_ids)

            # Print per-query results
            def _fmt(label, n_concepts, recall, chars):
                tokens = _estimate_tokens(" " * chars)
                efficiency = (recall / (tokens / 1000)) if tokens > 0 else 0
                return (
                    f"  {label:10s}: {n_concepts:2d} concepts | "
                    f"recall {recall:5.0%} | {chars:>7,} chars (~{tokens:>6,} tok) | "
                    f"efficiency: {efficiency:.2f} recall/ktok"
                )

            print(_fmt("Naive", len(all_ids), naive_recall, naive_chars))
            print(_fmt("Layer 3", len(l3_concept_ids), l3_recall, l3_chars))
            print(_fmt("Layer 4", len(l4_concept_ids), l4_recall, l4_chars))

            l3_found = sorted(l3_concept_ids & truth)
            l4_found = sorted(l4_concept_ids & truth)
            graph_added = sorted((l4_concept_ids & truth) - (l3_concept_ids & truth))
            missed = sorted(truth - l4_concept_ids)

            if graph_added:
                print(f"  Graph added to ground truth: {', '.join(graph_added)}")
            if missed:
                print(f"  Still missing: {', '.join(missed)}")
            print()

        # ── summary ────────────────────────────────────────────────
        n = len(GROUND_TRUTH)
        print("=" * 90)
        print("SUMMARY (averaged across all queries)")
        print("=" * 90)

        for label, key in [("Naive", "naive"), ("Layer 3", "l3"), ("Layer 4", "l4")]:
            avg_recall = totals[key]["recall"] / n
            avg_chars = totals[key]["chars"] / n
            avg_tokens = _estimate_tokens(" " * int(avg_chars))
            avg_concepts = totals[key]["concepts"] / n
            efficiency = (avg_recall / (avg_tokens / 1000)) if avg_tokens > 0 else 0
            reduction = (1 - avg_chars / naive_chars) * 100 if naive_chars > 0 else 0

            print(f"  {label:10s}: recall {avg_recall:5.0%} | "
                  f"~{avg_tokens:>6,} tok avg | "
                  f"{avg_concepts:4.1f} concepts | "
                  f"efficiency: {efficiency:.2f} recall/ktok | "
                  f"context reduction: {reduction:5.1f}%")

        l3_eff = (totals["l3"]["recall"] / n) / (_estimate_tokens(" " * int(totals["l3"]["chars"] / n)) / 1000)
        l4_eff = (totals["l4"]["recall"] / n) / (_estimate_tokens(" " * int(totals["l4"]["chars"] / n)) / 1000)
        if l3_eff > 0:
            print(f"\n  Layer 4 vs Layer 3 efficiency ratio: {l4_eff / l3_eff:.2f}x")
        print("=" * 90)


def cmd_ask(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    from .search.embedding import get_client
    from .generation.generator import ask

    client = get_client()
    project_root = Path(args.project_root).resolve()

    if not project_root.is_dir():
        print(f"Error: project root {project_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    type_filter = args.type.split(",") if args.type else None
    tag_filter = args.tags.split(",") if args.tags else None

    with KnowledgeGraph(db_path) as store:
        result = ask(
            args.query,
            store,
            project_root=project_root,
            client=client,
            top_k=args.top_k,
            max_hops=args.hops,
            type_filter=type_filter,
            tag_filter=tag_filter,
        )

    print(result.format())

    if args.verbose:
        print(f"\n--- Retrieval stats ---")
        concepts = result.retrieval.concepts
        search_concepts = [c for c in concepts if c.source == "search"]
        graph_concepts = [c for c in concepts if c.source != "search"]
        print(f"Concepts retrieved: {len(concepts)} ({len(search_concepts)} search + {len(graph_concepts)} graph-expanded)")
        print(f"Raw docs loaded: {result.raw_sources_used} ({result.raw_sources_chars:,} chars)")
        for c in concepts:
            source_label = {"search": "SEARCH", "graph_1hop": "1-HOP", "graph_2hop": "2-HOP"}
            label = source_label.get(c.source, c.source)
            print(f"  [{c.score:.3f}] [{label:6s}] {c.title} ({c.concept_id})")


def cmd_convert(args: argparse.Namespace) -> None:
    bundle_root = Path(args.bundle).resolve()
    if not bundle_root.is_dir():
        print(f"Error: bundle {bundle_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    raw_paths = [Path(p).resolve() for p in args.raw_docs]
    for p in raw_paths:
        if not p.exists():
            print(f"Error: {p} does not exist", file=sys.stderr)
            sys.exit(1)

    from .search.embedding import get_client
    client = get_client()

    if len(raw_paths) == 1 and raw_paths[0].is_file():
        result = convert_document(
            raw_path=raw_paths[0],
            bundle_root=bundle_root,
            output_name=args.output,
            output_subdir=args.subdir,
            client=client,
            dry_run=args.dry_run,
        )
        label = "Would write" if args.dry_run else "Wrote"
        print(f"{label}: {result.output_path}")
        print(f"  concept_id: {result.concept_id}")
        print(f"  source:     {result.source_path}")
        if result.links_to:
            print(f"  links to:   {', '.join(result.links_to)}")
        converted_count = 1
    else:
        batch = convert_batch(
            paths=raw_paths,
            bundle_root=bundle_root,
            client=client,
            dry_run=args.dry_run,
        )
        label = "Would convert" if args.dry_run else "Converted"
        print(f"{label} {len(batch.converted)} files:")
        for c in batch.converted:
            links = f" → {', '.join(c.links_to)}" if c.links_to else ""
            print(f"  {c.concept_id}{links}")
        if batch.skipped:
            print(f"\nSkipped {len(batch.skipped)}:")
            for path, reason in batch.skipped:
                print(f"  {path.name}: {reason}")
        if batch.errors:
            print(f"\nErrors {len(batch.errors)}:")
            for path, err in batch.errors:
                print(f"  {path.name}: {err}")
        converted_count = len(batch.converted)

    if args.reingest and not args.dry_run and converted_count > 0:
        print(f"\nRe-ingesting {bundle_root.name}/ into graph...")
        db_path = Path(args.db)
        with KnowledgeGraph(db_path) as store:
            ingest_result = ingest_bundle(bundle_root, store)
        print(f"  updated/inserted: {ingest_result['updated']}")
        print(f"  skipped unchanged: {ingest_result['skipped_unchanged']}")
        with KnowledgeGraph(db_path) as store:
            s = store.stats()
        print(f"  graph: {s['nodes']} nodes, {s['edges']} edges, {s['chunks']} chunks")
        print(f"\nRun 'python -m okf_graphrag embed' to generate embeddings for new chunks.")


def cmd_list(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database {db_path} not found", file=sys.stderr)
        sys.exit(1)

    with KnowledgeGraph(db_path) as store:
        ids = store.all_concept_ids()
        s = store.stats()

    print(f"Concepts ({s['nodes']} nodes, {s['edges']} edges, {s['chunks']} chunks):\n")
    for cid in ids:
        print(f"  {cid}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="okf-graphrag",
        description="Graph-aware RAG on OKF knowledge bundles",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite database")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="Ingest an OKF bundle into the graph")
    p_ingest.add_argument("bundle", help="Path to the bundle directory")

    p_show = sub.add_parser("show", help="Show a concept and its neighbors")
    p_show.add_argument("concept_id", help="Concept ID (e.g. services/api-gateway)")

    p_list = sub.add_parser("list", help="List all concepts in the database")

    p_embed = sub.add_parser("embed", help="Generate embeddings for all unembedded chunks")

    p_search = sub.add_parser("search", help="Search chunks by query")
    p_search.add_argument("query", help="Search query text")
    p_search.add_argument("--mode", choices=["hybrid", "vector", "bm25"], default="hybrid",
                          help="Search mode (default: hybrid)")
    p_search.add_argument("--top-k", type=int, default=10, help="Number of results")

    p_compare = sub.add_parser("compare", help="Compare context efficiency: naive vs L3 vs L4")
    p_compare.add_argument("--top-k", type=int, default=5, help="Concepts per approach")
    p_compare.add_argument("--hops", type=int, default=1, help="Graph expansion hops for L4")

    p_ask = sub.add_parser("ask", help="Ask a question — answers from raw source documents (Layer 6)")
    p_ask.add_argument("query", help="Your question")
    p_ask.add_argument("--project-root", default=".", help="Project root containing source_docs/ (default: .)")
    p_ask.add_argument("--top-k", type=int, default=5, help="Number of concepts to retrieve")
    p_ask.add_argument("--hops", type=int, default=1, help="Graph expansion hops (0-2)")
    p_ask.add_argument("--type", default=None, help="Filter by type (comma-separated)")
    p_ask.add_argument("--tags", default=None, help="Filter by tags (comma-separated)")
    p_ask.add_argument("--verbose", "-v", action="store_true", help="Show retrieval stats")

    p_convert = sub.add_parser("convert", help="Convert raw docs to OKF format (Layer 5)")
    p_convert.add_argument("raw_docs", nargs="+", help="Raw doc file(s) or directory to convert")
    p_convert.add_argument("--bundle", default="knowledge/sample/", help="Target bundle directory")
    p_convert.add_argument("--output", default=None, help="Output filename (single file mode only)")
    p_convert.add_argument("--subdir", default=None, help="Bundle subdirectory (e.g. services, libraries)")
    p_convert.add_argument("--reingest", action="store_true", help="Re-run graph ingestion after conversion")
    p_convert.add_argument("--dry-run", action="store_true", help="Preview without writing files")

    p_retrieve = sub.add_parser("retrieve", help="Graph-aware retrieval (Layer 4)")
    p_retrieve.add_argument("query", help="Search query text")
    p_retrieve.add_argument("--top-k", type=int, default=5, help="Number of seed concepts")
    p_retrieve.add_argument("--hops", type=int, default=2, help="Graph expansion hops (0-2)")
    p_retrieve.add_argument("--type", default=None, help="Filter by type (comma-separated)")
    p_retrieve.add_argument("--tags", default=None, help="Filter by tags (comma-separated)")
    p_retrieve.add_argument("--compare", action="store_true", help="Compare Layer 3 vs Layer 4")
    p_retrieve.add_argument("--context", action="store_true", help="Print LLM context")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "ingest": cmd_ingest,
        "show": cmd_show,
        "list": cmd_list,
        "embed": cmd_embed,
        "search": cmd_search,
        "retrieve": cmd_retrieve,
        "compare": cmd_compare,
        "ask": cmd_ask,
        "convert": cmd_convert,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
