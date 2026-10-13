SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    concept_id   TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT '',
    frontmatter  TEXT NOT NULL DEFAULT '{}',
    body_hash    TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS edges (
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    direction    TEXT NOT NULL CHECK(direction IN ('outgoing', 'backlink')),
    label        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, target_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id   TEXT NOT NULL,
    text         TEXT NOT NULL,
    position     INTEGER NOT NULL,
    heading      TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (concept_id) REFERENCES nodes(concept_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_concept ON chunks(concept_id);

CREATE TABLE IF NOT EXISTS vectors (
    chunk_id     INTEGER PRIMARY KEY,
    embedding    BLOB,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
