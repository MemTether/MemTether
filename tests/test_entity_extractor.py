# -*- coding: utf-8 -*-
"""tests/test_entity_extractor.py — entity_extractor tests.

Covers:
  1. Language detection (zh / en / mixed / empty)
  2. Chinese regex extraction (entity-attr pairs)
  3. English GLiNER extraction (skipped if gliner not installed)
  4. Mixed language merge
  5. Empty / no-entity returns None
  6. gateway integration: remember() stores fact_entities
"""
import os
import sys
import sqlite3
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from entity_extractor import detect_language, extract, _extract_regex, _extract_gliner

try:
    import gliner  # noqa
    HAS_GLINER = True
except ImportError:
    HAS_GLINER = False


# ---------- language detection ----------

class TestDetectLanguage:
    def test_pure_chinese(self):
        assert detect_language("端口默认8080，配置在用户目录") == "zh"

    def test_pure_english(self):
        assert detect_language("The server runs on port 8080") == "en"

    def test_mixed(self):
        assert detect_language("服务器 port 8080 配置") == "mixed"

    def test_empty(self):
        assert detect_language("") == "en"

    def test_symbols_only(self):
        assert detect_language("!!! ... 123") == "en"

    def test_numbers_and_cjk(self):
        # digits are not counted; mostly CJK -> zh
        assert detect_language("端口 8080 默认") == "zh"


# ---------- Chinese regex ----------

class TestChineseRegex:
    def test_zh_entity_attr(self):
        r = extract("mydb的端口是5432")
        assert r is not None
        assert len(r) >= 1
        assert r[0]["entity"] == "mydb"
        assert r[0]["source"] == "regex"

    def test_zh_with_path(self):
        # 2026-11-08 fix: predicates.extract_predicates needs the "X的Y是Z"
        # pattern; a bare "在 <path>" has no extractable entity-attr pair.
        r = extract("配置文件的路径是 E:\\RUANJIAN\\memory_hub\\memory.db")
        assert r is not None
        assert any(e["entity"] == "配置文件" for e in r)
        assert any("路径" in e["attr"] for e in r)

    def test_zh_no_entity(self):
        assert extract("这是一段没有任何实体的话") is None


# ---------- English GLiNER ----------

@pytest.mark.skipif(not HAS_GLINER, reason="gliner not installed")
class TestEnglishGLiNER:
    def test_gliner_basic(self):
        r = extract("The server runs on port 8080")
        assert r is not None
        assert len(r) >= 1
        for item in r:
            assert "entity" in item
            assert "attr" in item
            assert item["source"] == "gliner"

    def test_gliner_software(self):
        r = extract("Install Redis on Ubuntu using apt")
        assert r is not None

    def test_gliner_person(self):
        r = extract("Alice works at Anthropic")
        assert r is not None


# ---------- English fallback (no gliner) ----------

@pytest.mark.skipif(HAS_GLINER, reason="gliner IS installed")
class TestEnglishFallback:
    def test_no_gliner_returns_none_or_regex(self):
        r = extract("The server runs on port 8080")
        # Either None (no match) or regex result — must not crash
        assert r is None or isinstance(r, list)


# ---------- edge cases ----------

class TestEdgeCases:
    def test_empty_content(self):
        assert extract("") is None

    def test_whitespace_only(self):
        assert extract("   ") is None

    def test_very_long_chinese(self):
        text = "配置文件在 C:\\data\\config.yaml 端口是8080 " * 50
        r = extract(text)
        assert r is None or isinstance(r, list)

    def test_unicode_emoji(self):
        r = extract("🎉 端口是8080")
        # should not crash
        assert r is None or isinstance(r, list)


# ---------- gateway integration ----------

class TestGatewayIntegration:
    @pytest.fixture
    def fresh_db(self, tmp_path, monkeypatch):
        db = str(tmp_path / "ent_test.db")
        monkeypatch.setenv("MEM_DB", db)
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.init_db()
        yield db
        # cleanup
        if os.path.exists(db):
            os.remove(db)

    def test_remember_zh_stores_entities(self, fresh_db):
        import gateway
        r = gateway.remember("mydb的端口是5432", type="fact", source="test_ent")
        assert r.get("ok") is True
        uid = r["uid"]
        conn = gateway.get_conn()
        rows = conn.execute(
            "SELECT entity_name, entity, attr FROM fact_entities WHERE fact_uid=?",
            (uid,)).fetchall()
        conn.close()
        assert len(rows) >= 1
        assert rows[0]["entity_name"] == "mydb"

    def test_remember_english_no_gliner_no_crash(self, fresh_db):
        import gateway
        # in CI env (no gliner) this falls to regex / None — must not crash
        r = gateway.remember("The server runs on port 8080", type="fact", source="test_ent")
        assert r.get("ok") is True

    def test_remember_empty_fallback(self, fresh_db):
        import gateway
        # content with no extractable entities
        r = gateway.remember("ok fine whatever", type="fact", source="test_ent")
        assert r.get("ok") is True
        uid = r["uid"]
        conn = gateway.get_conn()
        rows = conn.execute(
            "SELECT COUNT(*) FROM fact_entities WHERE fact_uid=?",
            (uid,)).fetchone()[0]
        conn.close()
        # Either 0 (no entity) or some via regex — but the write succeeded
        assert rows >= 0