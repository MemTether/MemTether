# -*- coding: utf-8 -*-
"""Exchange adapter import-path tests (P1-3, 2026-10-05).
Replaces the old allow_module_level skip: we verify the core exchange
schema works without any external deps (mem0/zep are optional extras).
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(prefix="mt_exch_"), "t.db")

import memtether_exchange as mx


def test_schema_constants():
    assert mx.SCHEMA_NAME == "memtether.memory_exchange"
    assert mx.SCHEMA_VERSION >= 1
    assert "latest_wins" in mx.CONFLICT_STRATEGIES


def test_export_import_roundtrip_empty():
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(db)
    import gateway
    gateway.DB = db
    gateway.init_db()
    gateway.remember("roundtrip probe unique alpha", type="fact", source="t")
    data, out = mx.export_exchange(db_path=db, out_path=None, pii_redact=False)
    assert data["schema_name"] == "memtether.memory_exchange"
    assert data["counts"]["facts"] >= 1
    assert data["sha256"]
    # import into a fresh db
    fd2, db2 = tempfile.mkstemp(suffix=".db"); os.close(fd2); os.unlink(db2)
    stats = mx.import_exchange.__wrapped__(from_path=None, db_path=db2) if hasattr(mx.import_exchange, "__wrapped__") else None
    # use file path: write data to disk first
    fp = os.path.join(tempfile.mkdtemp(), "ex.json")
    json.dump(data, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    stats = mx.import_exchange(from_path=fp, db_path=db2)
    assert stats["ok"] is True
    assert stats["facts_inserted"] >= 1
    c = sqlite3.connect(db2)
    n = c.execute("SELECT COUNT(*) FROM facts WHERE content LIKE '%roundtrip probe%'").fetchone()[0]
    c.close()
    assert n == 1
