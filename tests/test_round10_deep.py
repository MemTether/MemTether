# -*- coding: utf-8 -*-
"""Line-level deep-audit round 10: verify_active_consistency + compile_packet
+ build_scaffold + verify_chain edge cases."""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _ms(tmp_path, monkeypatch):
    db = str(tmp_path / "r10.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    import importlib, memsearch
    importlib.reload(memsearch)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
          status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT, valid_to TEXT,
          recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT, source TEXT,
          scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8, tags TEXT,
          created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
          use_count INTEGER DEFAULT 0, predicate TEXT, tenant_id TEXT DEFAULT 'default',
          verify_at TEXT, last_verified_at TEXT);
        CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT, type TEXT,
          status TEXT DEFAULT 'active', path TEXT, entrypoint TEXT, capabilities TEXT,
          recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
          last_verified_at TEXT, verification_method TEXT, source TEXT,
          created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
          use_count INTEGER DEFAULT 0);
    """)
    return memsearch, conn


def _seed(conn, uid, content, type_="fact", source="codex"):
    conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                 "recorded_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (uid, type_, "s", content, "active", source,
                  "2026-11-05 00:00:00", "2026-11-05 00:00:00"))
    conn.commit()


class TestVerifyActiveConsistency:
    def test_ok_when_counts_match(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "vc1", "consistency probe content")
        conn.close()
        # stub vector client to report matching count
        class FakeCol:
            def count(self): return 1
        class FakeClient:
            def get_collection(self, name): return FakeCol()
        monkeypatch.setattr(ms, "_client", lambda: FakeClient())
        r = ms.verify_active_consistency()
        assert r["ok"] is True
        assert r["expected"] == 1 and r["vector_count"] == 1

    def test_not_ok_when_drift(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "vc2", "consistency probe two")
        conn.close()
        class FakeCol:
            def count(self): return 5  # drift!
        class FakeClient:
            def get_collection(self, name): return FakeCol()
        monkeypatch.setattr(ms, "_client", lambda: FakeClient())
        r = ms.verify_active_consistency()
        assert r["ok"] is False
        assert r["expected"] == 1 and r["vector_count"] == 5

    def test_garbage_excluded_from_expected(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "vc3", "real content here")
        _seed(conn, "vc4", "test garbage entry", source="test")  # test-source excluded
        conn.close()
        class FakeCol:
            def count(self): return 1
        class FakeClient:
            def get_collection(self, name): return FakeCol()
        monkeypatch.setattr(ms, "_client", lambda: FakeClient())
        r = ms.verify_active_consistency()
        assert r["expected"] == 1  # garbage excluded

    def test_error_when_db_missing(self, tmp_path, monkeypatch):
        ms, _ = _ms(tmp_path, monkeypatch)
        monkeypatch.setattr(ms, "DB", "Z:/nonexistent/no.db")
        r = ms.verify_active_consistency()
        assert r["ok"] is False and r["error"]


class TestCompilePacket:
    def test_short_input_passthrough(self):
        from memsearch import compile_packet
        results = [{"uid": "a", "score": 0.9, "content": "short", "type": "fact", "source": "s"}]
        assert compile_packet(results, max_items=16) is results

    def test_diversity_fill(self):
        from memsearch import compile_packet
        # 20 facts + 5 tools -> max_items=8: phase1 takes 4 top facts,
        # phase2 fills with 4 diversity-bonused (tools get priority)
        results = []
        for i in range(12):
            results.append({"uid": "f%d" % i, "score": 0.9 - i * 0.01,
                           "content": "fact content %d" % i, "type": "fact", "source": "src"})
        for i in range(5):
            results.append({"uid": "t%d" % i, "score": 0.5,
                           "content": "tool content %d" % i, "type": "tool",
                           "source": "tool_src%d" % i})
        r = compile_packet(results, max_items=16, max_chars=12000)
        types = {x.get("type") for x in r}
        assert "tool" in types, \
            "diversity fill must include tool type: %s" % types


class TestBuildScaffold:
    def test_counting_with_entities(self):
        from memsearch import build_scaffold, detect_question_type
        results = [
            {"content": "packy is a tool", "type": "fact", "source": "s", "score": 0.9, "updated_at": "2026-11-05"},
            {"content": "packy is fast", "type": "fact", "source": "s", "score": 0.8, "updated_at": "2026-11-04"},
        ]
        r = build_scaffold("counting", "packy count", results)
        assert r is not None and "counting" in r

    def test_temporal_scaffold(self):
        from memsearch import build_scaffold
        results = [
            {"content": "old event", "type": "fact", "source": "s", "score": 0.8, "updated_at": "2026-01-01"},
            {"content": "new event", "type": "fact", "source": "s", "score": 0.9, "updated_at": "2026-11-01"},
        ]
        r = build_scaffold("temporal", "when", results)
        assert r is not None and "temporal" in r

    def test_comparison_scaffold(self):
        from memsearch import build_scaffold
        results = [
            {"content": "option A", "type": "fact", "source": "s1", "score": 0.9, "updated_at": "2026-11-05"},
            {"content": "option B", "type": "fact", "source": "s2", "score": 0.8, "updated_at": "2026-11-04"},
            {"content": "option C", "type": "fact", "source": "s3", "score": 0.7, "updated_at": "2026-11-03"},
        ]
        r = build_scaffold("comparison", "compare", results)
        assert r is not None and "comparison" in r and "Compare" in r

    def test_empty_results_none(self):
        from memsearch import build_scaffold
        assert build_scaffold("counting", "q", []) is None


class TestMemoryCourtEdge:
    def test_verify_row_in_anchor_roundtrip(self, tmp_path, monkeypatch):
        from memory_court import maybe_anchor, verify_row_in_anchor, _ensure_anchor_table
        db = str(tmp_path / "mc.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
              op TEXT, target TEXT, agent TEXT, detail TEXT, ts TEXT);
        """)
        conn.execute("INSERT INTO audit_log (op,target,agent,detail,ts) VALUES"
                     "('remember','u1','codex','test detail','2026-11-05 00:00:00')")
        conn.commit()
        maybe_anchor(conn, force=True)
        log_id = conn.execute("SELECT id FROM audit_log WHERE target='u1'").fetchone()[0]
        ok, detail = verify_row_in_anchor(conn, log_id)
        assert ok is True
        # tamper: add more rows so the original is no longer the last one
        conn.execute("INSERT INTO audit_log (op,target,agent,detail,ts) VALUES"
                     "('remember','u2','codex','test','2026-11-05 01:00:00')")
        conn.commit()
        maybe_anchor(conn, force=True)
        ok2, detail2 = verify_row_in_anchor(conn, log_id)
        assert ok2 is True, "old log_id still covered by new anchor"
        conn.close()
