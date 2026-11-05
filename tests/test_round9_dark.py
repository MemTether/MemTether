# -*- coding: utf-8 -*-
"""Line-level deep-audit round 9: predicates + trigger_detector + tool_audit
+ governance helper functions."""
import os
import sqlite3
import sys
import json
from pathlib import Path as _P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import predicates as pred
import trigger_detector as td
import governance as gov


# ------------------------------------------------------------------ predicates
class TestExtractPredicates:
    def test_x_of_y_pattern(self):
        r = pred.extract_predicates("ComfyUI的安装路径")
        assert any(p["e"] == "ComfyUI" for p in r)

    def test_support_pattern(self):
        r = pred.extract_predicates("memsearch支持混合检索")
        assert any(p["e"] == "memsearch" for p in r)

    def test_empty_content(self):
        assert pred.extract_predicates("") == []

    def test_generic_pronoun_skipped(self):
        r = pred.extract_predicates("我的配置")
        assert all(p["e"] not in ("我", "你") for p in r)

    def test_dedup_same_entity_attr(self):
        r = pred.extract_predicates("ComfyUI的安装路径。ComfyUI的安装路径。")
        pairs = [(p["e"], p["a"]) for p in r]
        assert len(pairs) == len(set(pairs))


# ------------------------------------------------------------- trigger_detector
class TestTriggerDetector:
    def test_detect_triggers_basic(self, tmp_path, monkeypatch):
        db = str(tmp_path / "td.db")
        monkeypatch.setenv("MEM_DB", db)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT,"
                     " status TEXT DEFAULT 'active')")
        conn.execute("INSERT INTO tool_assets VALUES ('t1', 'packy', 'active')")
        conn.commit(); conn.close()
        td.reset_cache()  # force reload of entity index
        r = td.detect_triggers("帮我看一下 packy 通道的状态")
        assert isinstance(r, list)

    def test_detect_no_entities(self, tmp_path, monkeypatch):
        db = str(tmp_path / "td2.db")
        monkeypatch.setenv("MEM_DB", db)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT,"
                     " status TEXT DEFAULT 'active')")
        conn.commit(); conn.close()
        td.reset_cache()
        r = td.detect_triggers("tell me a joke please")
        assert isinstance(r, list)

    def test_reset_cache_clears(self, tmp_path, monkeypatch):
        db = str(tmp_path / "td3.db")
        monkeypatch.setenv("MEM_DB", db)
        td.reset_cache()
        assert td._ENTITY_INDEX is None
        assert td._ENTITY_INDEX_TS == 0


# ------------------------------------------------------------------- governance
class TestGovHelpers:
    def test_topic_tokens_ascii(self):
        r = gov._topic_tokens("this is a MemTether test")
        assert "memtether" in r

    def test_topic_tokens_cjk(self):
        r = gov._topic_tokens("记忆中枢投影系统设计")
        assert len(r) > 0

    def test_sentences_split(self):
        r = gov._sentences("第一句；第二句；第三句")
        assert len(r) >= 3

    def test_self_contradiction_detected(self, tmp_path, monkeypatch):
        db = str(tmp_path / "sc.db")
        monkeypatch.setenv("MEM_DB", db)
        import importlib
        importlib.reload(gov)
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT,
              content TEXT, status TEXT DEFAULT 'active', superseded_by TEXT,
              valid_from TEXT, valid_to TEXT, recorded_at TEXT,
              invalidated_at TEXT, temporal_source TEXT, source TEXT,
              scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8,
              tags TEXT, created_at TEXT, updated_at TEXT,
              q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
              predicate TEXT, tenant_id TEXT DEFAULT 'default');
        """)
        conn.execute("INSERT INTO facts (uid,type,content,status,source,"
                     "recorded_at,created_at) VALUES"
                     "('sc1','fact','deepseek官方API已失效，不可用；deepseek官方API已修复，可用。',"
                     "'active','codex','2026-11-05','2026-11-05')")
        conn.commit(); conn.close()
        r = gov.find_self_contradictions()
        assert any(x["uid"] == "sc1" for x in r)


# -------------------------------------------------------------------- tool_audit
class TestToolAudit:
    def test_audit_empty_assets(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "ta.db"))
        monkeypatch.setattr("tool_audit.ASSETS", [])
        import tool_audit as ta
        importlib = __import__("importlib")
        importlib.reload(ta)
        ta.ASSETS = []
        ta.audit()
        out = capsys.readouterr().out
        assert "资产清单未配置" in out

    def test_audit_reports_ok_and_miss(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "ta2.db")
        monkeypatch.setenv("MEM_DB", db)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT,"
                     " path TEXT, status TEXT DEFAULT 'active')")
        conn.commit(); conn.close()
        import tool_audit as ta
        real_file = str(tmp_path / "exists.txt")
        open(real_file, "w").close()
        monkeypatch.setattr("tool_audit.ASSETS",
                            [("real-tool", real_file), ("missing-tool", "Z:/nope.txt")])
        monkeypatch.setattr("tool_audit.DB", db)
        ta.audit()
        out = capsys.readouterr().out
        assert "OK" in out and "MISS" in out
        assert "活 1 / 缺 1" in out
