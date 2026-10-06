# -*- coding: utf-8 -*-
"""Regression: search_hybrid must never leak sqlite connections when the
target schema is incomplete (P0-1, 2026-10-06).

Root cause chain found in the 500Q bench: _tenant_filter() referenced
tenant_id on a schema lacking it; three separate code paths (active-dict,
multi-hop, qvalue bump) leaked live connections on that error. Each leaked
read/write txn then locked the bench scratch db for the REST of the process
('database is locked' at the next question's DROP TABLE). All three paths now
close in finally, and build_scratch_db closes its own conn unconditionally.
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MEM_SKIP_VECTOR", "1")

import memsearch  # noqa: E402


def _make_legacy_db(tmp_path):
    """A facts table WITHOUT tenant_id/temporal_source (pre-P0-2 schema)."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE, type TEXT, subject TEXT, content TEXT,
            status TEXT DEFAULT 'active', superseded_by TEXT,
            valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT,
            confidence REAL, tags TEXT, created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0);
        CREATE TABLE tool_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE, name TEXT, aliases TEXT, type TEXT, status TEXT,
            path TEXT, entrypoint TEXT, capabilities TEXT, known_failures TEXT,
            source TEXT, created_at TEXT, updated_at TEXT, content TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0);
        INSERT INTO facts (uid, content, type, source, status) VALUES
            ('fact-1', 'deploy to port 9090 with retry', 'fact', 't', 'active');
    """)
    conn.commit()
    conn.close()
    return db


def test_search_on_legacy_schema_leaks_no_connections(tmp_path):
    db = _make_legacy_db(tmp_path)
    saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    memsearch.DB = db
    try:
        try:
            memsearch.search_hybrid("deploy port", limit=3, decay=False)
        except Exception:
            pass  # a search failure is fine — the point is what it leaves behind
        # after the call, the db must be writable by a fresh connection
        probe = sqlite3.connect(db, timeout=5)
        probe.execute("DROP TABLE IF EXISTS facts")
        probe.execute("CREATE TABLE facts (uid TEXT)")
        probe.close()
    finally:
        memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = saved
