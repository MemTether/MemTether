"""Tests for bge-m3 semantic entity linking (2026-11-07)."""
import os
import pytest

try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False


class TestSemanticEntityLink:
    pytestmark = pytest.mark.skipif(not HAS_CHROMA, reason='chromadb not installed')
    """_semantic_entity_link: inherit entities from semantically similar facts."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "semantic.db"))
        monkeypatch.setenv("MEM_DEFAULT_SOURCE", "codex")
        monkeypatch.delenv("MEM_SKIP_VECTOR", raising=False)  # we NEED vectors
        monkeypatch.setenv("MEM_SINK_PATH", str(tmp_path / "sink.json"))
        monkeypatch.setenv("MEM_PROJ_PATH", str(tmp_path / "MEMORY.md"))
        monkeypatch.setenv("MEM_WORKBUDDY_DIR", str(tmp_path))
        monkeypatch.setenv("MEM_CHROMA_PATH", str(tmp_path / "chroma"))
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_english_fact_inherits_chinese_entity(self, tmp_path, monkeypatch):
        """English fact about same topic inherits entity from Chinese fact."""
        gw = self._setup(tmp_path, monkeypatch)
        # First: write a Chinese fact that gets regex-extracted properly
        r1 = gw.remember("mydb的端口是5432", source="codex")
        # Then: write an English fact about the same topic
        r2 = gw.remember("The mydb server is running on port 5432 for production", source="codex")
        
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "semantic.db"))
        conn.row_factory = sqlite3.Row
        # Check if r2 got entity "mydb" (either from regex or semantic link)
        entities = conn.execute(
            "SELECT entity FROM fact_entities WHERE fact_uid=?", 
            (r2["uid"],)).fetchall()
        conn.close()
        entity_names = [e["entity"] for e in entities]
        # The English fact should have inherited "mydb" via semantic similarity
        assert "mydb" in entity_names, \
            f"English fact should inherit entity 'mydb', got: {entity_names}"

    def test_unrelated_fact_no_inherit(self, tmp_path, monkeypatch):
        """Fact about a completely different topic should NOT inherit entities."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("mydb的端口是5432", source="codex")
        r2 = gw.remember("今天天气很好我们去公园散步吧", source="codex")
        
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "semantic.db"))
        conn.row_factory = sqlite3.Row
        entities = conn.execute(
            "SELECT entity FROM fact_entities WHERE fact_uid=?", 
            (r2["uid"],)).fetchall()
        conn.close()
        entity_names = [e["entity"] for e in entities]
        assert "mydb" not in entity_names, \
            f"unrelated fact should not inherit 'mydb', got: {entity_names}"
