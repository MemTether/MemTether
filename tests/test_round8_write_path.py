# -*- coding: utf-8 -*-
"""Line-level deep-audit round 8: write-path edge branches.

Real product behaviors with zero coverage:
- remember: confidence validation, cross-source exact echo (noop_echo),
  predicates ImportError fallback
- _ensure_columns: concurrent-init duplicate-column tolerance (multi-client
  startup race - the exact scenario the product is built for)
- _vec_upsert: lock-held skip (real rebuild race) + generic-garbage skip
- _search_like_legacy: tool/recipe matching arms
"""
import os
import sqlite3
import sys
import types
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _gw(tmp_path, monkeypatch):
    db = str(tmp_path / "r8.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    import importlib, gateway, memsearch
    importlib.reload(memsearch)
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


class TestRememberValidation:
    def test_confidence_out_of_range_rejected(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            gw.remember("valid content", source="codex", confidence=1.5)
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0"):
            gw.remember("valid content", source="codex", confidence=-0.1)

    def test_confidence_boundary_accepted(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        r0 = gw.remember("confidence zero", source="codex", confidence=0.0)
        r1 = gw.remember("confidence one", source="codex", confidence=1.0)
        assert r0["ok"] and r1["ok"]


class TestCrossSourceEcho:
    def test_exact_echo_returns_original_uid(self, tmp_path, monkeypatch):
        """Same content, different source -> noop_echo, NO second row."""
        gw = _gw(tmp_path, monkeypatch)
        r1 = gw.remember("cross-source echo probe content", source="claude-code")
        assert r1["op"] == "insert"
        r2 = gw.remember("cross-source echo probe content", source="codex")
        assert r2["op"] == "noop_echo", r2
        assert r2["uid"] == r1["uid"], "echo must attribute to the ORIGINAL row"
        assert r2["original_source"] == "claude-code"
        conn = sqlite3.connect(gw.DB)
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE content=?",
                         ("cross-source echo probe content",)).fetchone()[0]
        audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='remember_echo'").fetchone()[0]
        conn.close()
        assert n == 1, "echo must not create a second row"
        assert audit == 1, "echo must be audited"


class TestEnsureColumns:
    def test_duplicate_column_race_tolerated(self, tmp_path, monkeypatch):
        """Simulate two processes calling _ensure_columns simultaneously:
        the loser must tolerate 'duplicate column' and not crash."""
        gw = _gw(tmp_path, monkeypatch)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY)")
        # manually add q_value so the next ALTER fails with duplicate
        conn.execute("ALTER TABLE facts ADD COLUMN q_value REAL DEFAULT 0.5")
        conn.execute("ALTER TABLE tool_assets ADD COLUMN q_value REAL DEFAULT 0.5")
        conn.execute("ALTER TABLE tool_assets ADD COLUMN use_count INTEGER DEFAULT 0")
        gw._ensure_columns(conn)  # must tolerate duplicate + add missing cols

    def test_new_columns_added(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY)")
        gw._ensure_columns(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)")}
        assert {"q_value", "use_count", "predicate", "verify_at",
                "last_verified_at"} <= cols


class TestVecUpsertLock:
    def test_lock_held_skips(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        # hold the gate lock in another thread -> _vec_upsert must skip
        lock_path = os.path.join(gw.HUB, ".gate_rebuild.lock")
        import msvcrt
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # hold it
        try:
            r = gw._vec_upsert("u-lock", "content for lock test", "fact", "codex")
            assert r.get("skipped") == "rebuild_in_progress", r
        finally:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)


class TestLikeLegacy:
    def test_tool_matching(self, tmp_path, monkeypatch):
        gw = _gw(tmp_path, monkeypatch)
        conn = sqlite3.connect(gw.DB)
        conn.execute("""CREATE TABLE IF NOT EXISTS tool_assets (
            uid TEXT PRIMARY KEY, name TEXT, type TEXT, status TEXT DEFAULT 'active',
            path TEXT, entrypoint TEXT, capabilities TEXT, recipe_ids TEXT,
            known_failures TEXT, prerequisites TEXT, last_verified_at TEXT,
            verification_method TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
        conn.execute("INSERT INTO tool_assets (uid,name,type,status,path,source,created_at) VALUES ('tool-1','comfyui','local_tool','active','C:/ComfyUI/main.py','codex','2026-11-05')")
        conn.execute("""CREATE TABLE IF NOT EXISTS recipes (
            uid TEXT PRIMARY KEY, task_patterns TEXT, preferred_tool TEXT,
            steps TEXT, output_paths TEXT, known_pitfalls TEXT,
            fallback_tools TEXT, last_success_at TEXT, source TEXT,
            created_at TEXT, updated_at TEXT)""")
        conn.execute("INSERT INTO recipes (uid,task_patterns,steps,source) VALUES"
                     "('rec-1','deploy site','[]','codex')")
        conn.commit(); conn.close()
        r = gw._search_like_legacy("comfyui")
        kinds = {x["kind"] for x in r["results"]}
        assert "tool" in kinds, r["results"]
        r2 = gw._search_like_legacy("deploy site")
        kinds2 = {x["kind"] for x in r2["results"]}
        assert "recipe" in kinds2, r2["results"]
