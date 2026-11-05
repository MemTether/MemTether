# -*- coding: utf-8 -*-
"""Line-level deep-audit round 11: remaining real-logic dark functions."""
import os
import sys
import sqlite3
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import tempfile as _tf
os.environ.setdefault("MEM_DB", _tf.mkdtemp(prefix="r11_") + "/r11.db")

import hubguard as hg
from memsearch import _quoted_query_like, build_scaffold


def _setup_gateway(tmp_path, monkeypatch):
    db = str(tmp_path / "r11.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


# ------------------------------------------------------- _quoted_query_like
class TestQuotedQueryLike:
    def test_search_context_quote_extracted(self):
        r = _quoted_query_like('我搜索了"DeepSeek 官方 API"的结果')
        assert "DeepSeek 官方 API" in r

    def test_no_search_context_no_match(self):
        r = _quoted_query_like('他说"你好"然后走了')
        assert "你好" not in r  # no search context before the quote

    def test_empty_quote_skipped(self):
        r = _quoted_query_like('搜索了""的结果')
        assert r == []

    def test_multiple_quotes(self):
        r = _quoted_query_like('检索"query one"然后查询"query two"')
        assert len(r) >= 1


# ----------------------------------------------------- gateway._known_sources


class TestProjBudget:
    def test_default_budget(self):
        r = hg.proj_budget()
        assert isinstance(r, dict)

    def test_custom_budget(self, monkeypatch):
        monkeypatch.setenv("MEM_PROJ_BUDGET", "2000")
        r = hg.proj_budget()
        assert isinstance(r, dict)


# ------------------------------------------------------- rerank metadata
class TestRerankInfo:
    def test_info_returns_dict(self):
        import rerank
        r = rerank.info()
        assert isinstance(r, dict)
        assert "model" in r and "available" in r

    def test_available_returns_bool(self):
        import rerank
        r = rerank.available()
        assert isinstance(r, bool)
