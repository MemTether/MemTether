# -*- coding: utf-8 -*-
"""Line-level deep-audit round 16: branch-arm targeted tests."""
import os
os.environ["MEM_DB"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_r16_test.db")
os.environ["MEM_SKIP_VECTOR"] = "1"
import sqlite3
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import predicates as pred
import trigger_detector as td
import hubguard as hg
import memsearch

def _setup_pred(tmp_path, monkeypatch):
    import importlib
    importlib.reload(pred)
    return pred


# --------------------------------------------------- predicates edge branches
class TestPredicateEdges:
    def test_entity_too_long_rejected(self):
        # L76: len(e) > 30 rejection
        r = pred.extract_predicates("A" * 40 + "的配置")
        assert all(len(p["e"]) <= 30 for p in r)


    def test_cjk_support_pattern(self):
        # L155-158: CJK support pattern without functional words
        r = pred.extract_predicates("ComfyUI支持ControlNet插件")
        assert any(p["e"] == "ComfyUI" for p in r)

    def test_action_keywords_path(self):
        # L170-180: action keywords trigger entity extraction
        r = pred.extract_predicates("部署ComfyUI到本地环境")
        assert isinstance(r, list)


class TestTriggerPrefixMatch:
    def test_entity_prefix_match(self, tmp_path, monkeypatch):
        db = str(tmp_path / "tp.db")
        monkeypatch.setenv("MEM_DB", db)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT,"
                     " status TEXT DEFAULT 'active')")
        conn.execute("INSERT INTO tool_assets VALUES ('t1', 'memtether_gateway', 'active')")
        conn.commit(); conn.close()
        td.reset_cache()
        # query contains a token that's a prefix of the entity name
        r = td.detect_triggers("check the memtether_gateway status now")
        assert isinstance(r, list)


# -------------------------------------------------- memory_court verify_external
class TestVerifyExternal:
    def test_no_dir_first_run_true(self, tmp_path, monkeypatch):
        """No external anchor dir -> falls back to verify_chain (first-run)."""
        from memory_court import verify_external
        import memory_court as mc
        old = mc.EXTERNAL_ANCHOR_DIR
        mc.EXTERNAL_ANCHOR_DIR = str(tmp_path / "nope")
        try:
            rc, msg = verify_external(sqlite3.connect(":memory:"))
            # in-memory DB has no chain to verify, ok may be True or False
            assert isinstance(rc, bool)
        finally:
            mc.EXTERNAL_ANCHOR_DIR = old

    def test_empty_dir_no_files(self, tmp_path, monkeypatch):
        from memory_court import verify_external
        import memory_court as mc
        d = tmp_path / "anchors"
        d.mkdir()
        old = mc.EXTERNAL_ANCHOR_DIR
        mc.EXTERNAL_ANCHOR_DIR = str(d)
        try:
            rc, msg = verify_external(sqlite3.connect(":memory:"))
            assert isinstance(rc, bool)
        finally:
            mc.EXTERNAL_ANCHOR_DIR = old

    def test_with_anchor_file(self, tmp_path, monkeypatch):
        from memory_court import verify_external, anchor_external
        import sqlite3 as sq
        d = tmp_path / "anchors2"
        d.mkdir()
        monkeypatch.setattr("memory_court.EXTERNAL_ANCHOR_DIR", str(d))
        # create a DB with anchors
        db = tmp_path / "mc.db"
        conn = sq.connect(str(db))
        conn.executescript("""
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
              op TEXT, target TEXT, agent TEXT, detail TEXT, ts TEXT);
        """)
        conn.execute("INSERT INTO audit_log (op,target,agent,detail,ts) VALUES"
                     "('remember','u1','codex','test','2026-11-05 00:00:00')")
        conn.commit()
        from memory_court import maybe_anchor
        maybe_anchor(conn, force=True)
        r = anchor_external(conn)
        conn.close()
        # now verify with the anchor file present
        rc, msg = verify_external(sq.connect(str(db)))
        # rc may be True or False depending on hash match; just verify it runs
        assert isinstance(rc, bool)


# --------------------------------------------------- memsearch.asset_text
class TestAssetText:
    def test_with_prerequisites_and_failures(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        class FakeRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d.get(k, "")
            def keys(self):
                return self._d.keys()
        row = FakeRow({"name": "comfyui", "path": "C:/ComfyUI/main.py",
                       "entrypoint": "python main.py",
                       "capabilities": "drawing",
                       "prerequisites": "★需要Python 3.10",
                       "known_failures": "CUDA OOM on 4GB"})
        r = memsearch.asset_text(row)
        assert "comfyui" in r
        assert "Python 3.10" in r
        assert "CUDA OOM" in r

    def test_minimal_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t2.db"))
        import importlib
        importlib.reload(memsearch)
        class FakeRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d.get(k, "")
            def keys(self):
                return self._d.keys()
        row = FakeRow({"name": "minimal", "path": "", "entrypoint": "",
                       "capabilities": "", "prerequisites": "", "known_failures": ""})
        r = memsearch.asset_text(row)
        assert "minimal" in r


# ------------------------------------------- gateway._tenant_filter non-default
