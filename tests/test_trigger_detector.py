"""Tests for Proactive Memory Recall trigger_detector."""
import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trigger_detector import detect_triggers, auto_context


@pytest.fixture
def entity_db(tmp_path):
    """Create a test DB with known entities + facts table for auto_context."""
    db = str(tmp_path / "test_trigger.db")
    # NOTE (2026-10-20): the schema below mirrors gateway.py's real SCHEMA.
    # The previous version created fact_entities WITH an entity_name column,
    # but the real table is EAV (entity/attr/value/fact_uid). Because
    # _load_entity_index selects entity_name and swallows the resulting
    # OperationalError, the facts-side entity index was silently dead in
    # production while these tests stayed green on their own fake table.
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE fact_entities (id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, attr TEXT, value TEXT, fact_uid TEXT NOT NULL, source TEXT, ts TEXT, valid_from TEXT);
        CREATE TABLE tool_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, name TEXT, aliases TEXT, type TEXT, status TEXT DEFAULT 'active', path TEXT, entrypoint TEXT, capabilities TEXT, recipe_ids TEXT, known_failures TEXT, prerequisites TEXT, last_verified_at TEXT, verification_method TEXT, source TEXT, created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0);
        CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, type TEXT, subject TEXT, content TEXT NOT NULL, status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT, valid_to TEXT, recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT, source TEXT, scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8, tags TEXT, created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0);
    """)
    for entity in ["packy", "clash", "comfyui", "memtether"]:
        conn.execute("INSERT INTO fact_entities (entity, fact_uid, source, ts) "
                     "VALUES (?, ?, 'test', '2026-10-20 10:00:00')",
                     (entity, 'fact-probe-' + entity))
    for tool in ["gateway.py", "memsearch.py"]:
        conn.execute("INSERT INTO tool_assets (uid, name, status) VALUES (?, ?, 'active')",
                     ('tool-probe-' + tool, tool))
    for content in ["packy is a proxy service", "clash proxy on port 7890", "comfyui is a diffusion UI"]:
        conn.execute("INSERT INTO facts (uid, content, status, source) VALUES (?,?,?,?)",
                     ("test-" + content[:10], content, "active", "test"))
        # tool_assets 的 name 列也要有值，_load_entity_index 第二条路查的是它
    conn.commit()
    conn.close()
    return db


class TestDetectTriggers:
    def test_true_positive_packy(self, entity_db):
        triggers = detect_triggers("what is packy status?", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "packy" in [e.lower() for e in entities]

    def test_true_positive_clash(self, entity_db):
        triggers = detect_triggers("clash proxy settings", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "clash" in [e.lower() for e in entities]

    def test_false_positive_france(self, entity_db):
        triggers = detect_triggers("I went to France last week", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "france" not in [e.lower() for e in entities]

    def test_false_positive_joke(self, entity_db):
        triggers = detect_triggers("tell me a joke", entity_db)
        assert len(triggers) == 0

    def test_empty_message(self, entity_db):
        assert detect_triggers("", entity_db) == []


class TestAutoContext:
    def test_context_contains_relevant_fact(self, entity_db):
        triggers = detect_triggers("what about packy?", entity_db)
        assert triggers, "should have triggered"
        ctx = auto_context(triggers, entity_db)
        assert isinstance(ctx, str)
        assert len(ctx) > 0
        assert "packy" in ctx.lower()

    def test_context_type_string(self, entity_db):
        triggers = detect_triggers("clash settings", entity_db)
        ctx = auto_context(triggers, entity_db)
        assert isinstance(ctx, str)


class TestIntegration:
    def test_latency_under_5ms(self, entity_db):
        import time
        t0 = time.perf_counter()
        detect_triggers("what is packy and clash and comfyui?", entity_db)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 5, f"Trigger detection took {elapsed_ms:.1f}ms, expected <5ms"
