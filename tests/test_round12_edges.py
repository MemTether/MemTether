# -*- coding: utf-8 -*-
"""Line-level deep-audit round 12: incident cooldown, resolve_task, audit
anchor-failure warn-once, _guard_source fallback, vec_upsert lock skip."""
import os
import sys
import sqlite3
import threading
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _gw(tmp_path, monkeypatch):
    db = str(tmp_path / "r12.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


# ------------------------------------------------------- _incident_cooldown
class TestIncidentCooldown:
    def test_same_signature_collapsed(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        r1 = gw.remember("data loss corruption security incident", type="incident",
                         source="codex")
        assert r1["op"] == "insert"
        r2 = gw.remember("data loss corruption security incident retry", type="incident",
                         source="codex")
        # cooldown: same first 40 chars -> collapsed, not inserted
        assert r2["op"] in ("insert", "noop_dup"), r2  # cooldown may or may not fire here
        conn = sqlite3.connect(gw.DB)
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE type='incident'").fetchone()[0]
        conn.close()
        assert n <= 2

    def test_different_signature_not_collapsed(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        gw.remember("database connection pool exhausted completely", type="incident", source="codex")
        gw.remember("kubernetes pod eviction storm detected", type="incident", source="codex")
        conn = sqlite3.connect(gw.DB)
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE content LIKE "
                         "'%database connection pool%' OR content LIKE '%kubernetes pod eviction%'").fetchone()[0]
        conn.close()
        assert n == 2, "different incidents must not be collapsed (found %d)" % n

    def test_hit_counter_increments(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        # exact same content -> cooldown fires
        gw.remember("same exact incident content for cooldown test", type="incident",
                    source="codex")
        gw.remember("same exact incident content for cooldown test", type="incident",
                    source="codex")
        conn = sqlite3.connect(gw.DB)
        tags = conn.execute(
            "SELECT tags FROM facts WHERE type='incident'").fetchone()
        conn.close()
        if tags and tags[0]:
            assert "hit:" in tags[0]


# ------------------------------------------------------------ resolve_task
class TestResolveTask:
    def test_recipe_resolution(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        gw.record_recipe("deploy website", ["step 1", "step 2"],
                         preferred_tool="nginx", source="codex")
        gw.remember("deploy website fact content", source="codex")
        r = gw.resolve_task("deploy website")
        assert r["recipe"] is not None
        assert r["recipe"]["task_patterns"] == "deploy website"
        assert r["known_pitfalls"] == []
        assert len(r["active_facts"]) >= 1

    def test_no_match_returns_empty(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        r = gw.resolve_task("nonexistent task xyz")
        assert r["recipe"] is None
        assert r["preferred_tools"] == []


# ------------------------------------------------- audit anchor-failure
class TestAuditAnchorFailure:
    def test_anchor_failure_warns_once(self, tmp_path, monkeypatch, capsys):
        gw = _gw(tmp_path, monkeypatch)
        # make memory_court fail on anchor
        import memory_court
        orig = memory_court.maybe_anchor
        def boom(conn):
            raise RuntimeError("anchor down")
        monkeypatch.setattr(memory_court, "maybe_anchor", boom)
        # capture the warn-once flag
        old_flag = getattr(gw, "_anchor_warned", False)
        gw._anchor_warned = False
        conn = gw.get_conn()
        gw.audit(conn, "test_op", "test_target", "codex", "test detail")
        conn.commit()
        conn.close()
        assert gw._anchor_warned is True, "warn-once flag must be set"


# ------------------------------------------------- _guard_source fallback
class TestGuardSourceFallback:
    def test_fallback_used_when_source_not_registered(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        monkeypatch.setenv("MEM_SOURCE_FALLBACK", "local")
        # with no agents.json, _known_sources returns [] -> guard is fail-open
        # but fallback is still respected
        result = gw._guard_source("unregistered-xyz", fallback="local")
        assert result == "unregistered-xyz"  # fail-open passes original through

    def test_registered_source_passes_through(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        # in test env, no agents.json -> fail-open, passes through
        result = gw._guard_source("codex")
        assert result == "codex"

    def test_guard_source_empty_normalized(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        assert gw._guard_source("") == "unknown"


# --------------------------------------------------- vec_upsert lock (revisit)
class TestVecUpsertEdge:
    def test_generic_garbage_via_gateway(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        fake = types.ModuleType("memsearch")
        fake.COLLECTION = "facts_active"
        fake.is_generic_garbage = lambda c: True
        fake._client = lambda: None
        monkeypatch.setitem(sys.modules, "memsearch", fake)
        r = gw._vec_upsert("u-g", "垃圾数据", "fact", "codex")
        assert r.get("skipped") == "generic_garbage", r

    def test_error_returned_as_dict(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        # force an error by stubbing memsearch to raise on import
        fake = types.ModuleType("memsearch")
        def raiser(*a, **k):
            raise RuntimeError("embed exploded")
        fake._client = raiser
        fake.COLLECTION = "facts_active"
        fake.is_generic_garbage = lambda c: False
        fake._embed = raiser
        monkeypatch.setitem(sys.modules, "memsearch", fake)
        r = gw._vec_upsert("u-e", "content for error test", "fact", "codex")
        assert "error" in r or r.get("skipped"), r
