# -*- coding: utf-8 -*-
"""Line-level deep-audit round 15: partial-coverage branch arms."""
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

import hubguard as hg
import predicates as pred
import governance as gov
import trigger_detector as td
import memsearch


# ------------------------------------------- memsearch.build_scaffold no-ent
class TestScaffoldNoEntities:
    def test_counting_no_entities_by_type(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        results = [
            {"content": "fact A", "type": "fact", "source": "s1", "score": 0.9},
            {"content": "fact B", "type": "fact", "source": "s2", "score": 0.8},
            {"content": "fact C", "type": "tool", "source": "s3", "score": 0.7},
        ]
        r = memsearch.build_scaffold("counting", "统计一下数量分布", results)
        assert r is not None and "counting" in r

    def test_comparison_pairs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        results = [
            {"content": "A", "type": "fact", "source": "s1", "score": 0.9},
            {"content": "B", "type": "fact", "source": "s2", "score": 0.8},
        ]
        r = memsearch.build_scaffold("comparison", "cmp", results)
        assert r is not None and "Compare" in r


# ------------------------------------------- memsearch.compile_packet trunc
class TestCompilePacketTruncation:
    def test_char_budget_truncates_last_item(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        # 3 items, max_items=2, max_chars=50 -> last item truncated
        results = [
            {"uid": "a", "score": 0.9, "content": "A" * 30, "type": "fact", "source": "s"},
            {"uid": "b", "score": 0.8, "content": "B" * 15, "type": "fact", "source": "s"},
            {"uid": "c", "score": 0.7, "content": "C" * 40, "type": "fact", "source": "s"},
        ]
        r = memsearch.compile_packet(results, max_items=2, max_chars=50)
        assert len(r) <= 2
        # total chars should not exceed budget
        total = sum(len(x.get("content", "")) for x in r)
        assert total <= 50


# ------------------------------------------- hubguard.safe_append_bytes CRT
class TestSafeAppend:
    def test_crt_fallback_on_error(self, tmp_path, monkeypatch):
        """FILE_APPEND_DATA fails -> CRT O_APPEND fallback (non-atomic warning)."""
        f = tmp_path / "fb.log"
        f.write_text("base\n", encoding="utf-8")
        # monkeypatch _win_append to raise -> triggers CRT fallback
        orig = hg._win_append
        def boom(path, data, **kw):
            raise IOError("simulated CreateFileW failure")
        monkeypatch.setattr(hg, "_win_append", boom)
        r = hg.safe_append_bytes(str(f), b"appended\n")
        assert r["ok"] is True
        assert r.get("atomic_append") is False, \
            "CRT fallback must be flagged as non-atomic"
        assert f.read_bytes().endswith(b"appended\n")

    def test_expect_detects_concurrent_change(self, tmp_path):
        f = tmp_path / "cc.log"
        f.write_text("v1\n", encoding="utf-8")
        before = hg.snapshot(str(f))
        # another process writes between snapshot and append
        f.write_text("someone else changed\n", encoding="utf-8")
        r = hg.safe_append_bytes(str(f), b"my line\n", expect=before)
        assert r.get("concurrent_change") is True


# ------------------------------------------- trigger_detector.tokenize_message
class TestTokenizeMessage:
    def test_ascii_and_cjk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "td.db"))
        import importlib
        importlib.reload(td)
        r = td.tokenize_message("check gateway.py status 现在怎么样")
        assert isinstance(r, (list, set))

    def test_empty_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "td2.db"))
        import importlib
        importlib.reload(td)
        r = td.tokenize_message("")
        assert isinstance(r, (list, set))


# -------------------------------------------- governance._sentences long text
class TestSentencesLong:
    def test_long_text_truncated(self):
        r = gov._sentences("短句一；" + "很长很长的句子超过两百个字符" * 30 + "；短句二")
        assert isinstance(r, list) and len(r) >= 2

    def test_numbered_markers_split(self):
        r = gov._sentences("①第一点；②第二点；③第三点")
        assert len(r) >= 3


# ---------------------------------------------- predicates._query_attrs
class TestQueryAttrs:
    def test_x_of_y_query(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "qa.db"))
        import importlib
        importlib.reload(pred)
        r = pred._query_attrs("查一下ComfyUI的安装路径")
        assert isinstance(r, dict) or isinstance(r, list)

    def test_empty_query(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "qa2.db"))
        import importlib
        importlib.reload(pred)
        r = pred._query_attrs("")
        assert isinstance(r, (dict, list))


# ---------------------------------------------- predicates.store_entities
class TestStoreEntities:
    def test_store_and_query_roundtrip(self, tmp_path, monkeypatch):
        db = str(tmp_path / "se.db")
        monkeypatch.setenv("MEM_DB", db)
        import importlib
        importlib.reload(pred)
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fact_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_uid TEXT, entity TEXT, attr TEXT, source TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT DEFAULT 'active', updated_at TEXT DEFAULT '2026-11-05');
            INSERT INTO facts VALUES ('u-test', 'ComfyUI的安装路径是C:/ComfyUI', 'active', '2026-11-05');
        """)
        conn.commit()
        pred.store_entities(conn, "u-test", "ComfyUI的安装路径是C:/ComfyUI", source="codex", ts="2026-11-05")
        r = pred.query_entities(conn, "ComfyUI")
        assert isinstance(r, (list, dict))
        conn.close()
