"""Tests for Hindsight-inspired entity trend computation (2026-11-07)."""
import os
import pytest


class TestEntityTrends:
    """compute_entity_trends: STABLE/STRENGTHENING/WEAKENING/NEW/STALE from timestamps."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "trend.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_new_entity(self, tmp_path, monkeypatch):
        """Entity with all facts created recently = NEW."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("testsvr的端口是8080", source="codex")
        gw.remember("testsvr的路径是/srv/test", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "trend.db"))
        trends = gw.compute_entity_trends(conn)
        conn.close()
        assert "testsvr" in trends, f"expected testsvr in trends, got: {list(trends.keys())}"
        assert trends["testsvr"]["trend"] == "NEW"
        assert trends["testsvr"]["proof_count"] >= 2

    def test_stale_entity(self, tmp_path, monkeypatch):
        """Entity with no recent facts = STALE. Need to backdate facts."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("oldsvr的端口是3000", source="codex")
        r2 = gw.remember("oldsvr的路径是/srv/old", source="codex")
        # Backdate created_at to 200+ days ago
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "trend.db"))
        conn.execute("UPDATE facts SET created_at = '2026-01-01 00:00:00' WHERE uid IN (?, ?)",
                      (r1["uid"], r2["uid"]))
        conn.commit()
        trends = gw.compute_entity_trends(conn)
        conn.close()
        assert "oldsvr" in trends, f"expected oldsvr in trends"
        assert trends["oldsvr"]["trend"] == "STALE", f"expected STALE, got: {trends['oldsvr']['trend']}"

    def test_single_fact_entity_excluded(self, tmp_path, monkeypatch):
        """Entity with only 1 fact (HAVING COUNT(*) >= 2) should be excluded."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("lonely的端口是8080", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "trend.db"))
        trends = gw.compute_entity_trends(conn)
        conn.close()
        assert "lonely" not in trends, "single-fact entity should be excluded"

    def test_proof_count(self, tmp_path, monkeypatch):
        """proof_count reflects total active facts for the entity."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("multi的端口是8080", source="codex")
        gw.remember("multi的路径是/srv/multi", source="codex")
        gw.remember("multi的管理员是admin", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "trend.db"))
        trends = gw.compute_entity_trends(conn)
        conn.close()
        assert "multi" in trends
        assert trends["multi"]["proof_count"] >= 3, f"expected >= 3, got: {trends['multi']['proof_count']}"

    def test_search_diag_has_trends(self, tmp_path, monkeypatch):
        """search_hybrid diag includes entity_trends when available."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("webdb的端口是5432", source="codex")
        gw.remember("webdb的路径是/var/webdb", source="codex")
        import memsearch
        importlib = __import__("importlib")
        importlib.reload(memsearch)
        memsearch.DB = gw.DB
        r = memsearch.search_hybrid("webdb端口", limit=5)
        assert "entity_trends" in r.get("diag", {}), f"diag should have entity_trends, diag keys: {list(r.get('diag', {}).keys())}"
        trends = r["diag"]["entity_trends"]
        assert "webdb" in trends, f"expected webdb in trends: {list(trends.keys())}"
