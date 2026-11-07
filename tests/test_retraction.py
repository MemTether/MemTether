"""Tests for Hindsight-inspired retraction tracking (2026-11-07)."""
import os
import sys
import pytest


class TestRetractionPropagation:
    """_propagate_retraction: when a fact dies, sibling facts get tagged."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "retraction.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_supersede_propagates_retracted_tag(self, tmp_path, monkeypatch):
        """When a fact is corrected (superseded), sibling facts sharing an entity get retracted tag."""
        gw = self._setup(tmp_path, monkeypatch)
        # Write facts whose content triggers predicate extraction with shared entity
        # Use Chinese-style patterns that extract_predicates handles: "X的Y是Z"
        r1 = gw.remember("nginx的配置文件是/etc/nginx/nginx.conf", source="codex")
        r2 = gw.remember("nginx的日志路径是/var/log/nginx/access.log", source="codex")
        r = gw.correct(r1["uid"], "nginx的配置文件是/etc/nginx/nginx-new.conf", reason="path changed")
        assert r["ok"], f"correct failed: {r}"
        assert r.get("retracted_siblings", 0) > 0, f"expected retracted_siblings > 0, got: {r}"
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "retraction.db"))
        tags2 = conn.execute("SELECT tags FROM facts WHERE uid=?", (r2["uid"],)).fetchone()
        conn.close()
        assert tags2 and tags2[0], "sibling fact should have tags"
        assert "retracted:" in tags2[0], f"expected retracted tag, got: {tags2[0]}"

    def test_retire_propagates_retracted_tag(self, tmp_path, monkeypatch):
        """When a fact is retired, sibling facts sharing an entity get retracted tag."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("postgres的端口是5432", source="codex")
        r2 = gw.remember("postgres的管理员是admin", source="codex")
        r = gw.retire(r1["uid"], reason="decommissioned")
        assert r["ok"], f"retire failed: {r}"
        assert r.get("retracted_siblings", 0) > 0, f"expected retracted_siblings > 0, got: {r}"
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "retraction.db"))
        tags2 = conn.execute("SELECT tags FROM facts WHERE uid=?", (r2["uid"],)).fetchone()
        conn.close()
        assert tags2 and tags2[0], "sibling fact should have tags"
        assert "retracted:" in tags2[0], f"expected retracted tag, got: {tags2[0]}"

    def test_no_false_positive_on_unrelated(self, tmp_path, monkeypatch):
        """Facts with no shared entity should NOT get retracted tag."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("nginx的配置文件是/etc/nginx/nginx.conf", source="codex")
        r2 = gw.remember("kubernetes的节点数量是3", source="codex")
        gw.correct(r1["uid"], "nginx的配置文件是/etc/nginx/nginx-new.conf", reason="path changed")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "retraction.db"))
        tags2 = conn.execute("SELECT tags FROM facts WHERE uid=?", (r2["uid"],)).fetchone()
        conn.close()
        tags_str = tags2[0] if tags2 and tags2[0] else ""
        assert "retracted:" not in tags_str, f"unrelated fact should not get retracted tag, got: {tags_str}"

    def test_search_surfaces_retracted_refs(self, tmp_path, monkeypatch):
        """search results include retracted_refs field when tag exists."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("redis的端口是6379", source="codex")
        r2 = gw.remember("redis的最大内存是256MB", source="codex")
        gw.retire(r1["uid"], reason="decommissioned")
        import memsearch
        importlib = __import__("importlib")
        importlib.reload(memsearch)
        memsearch.DB = gw.DB
        r = memsearch.search_hybrid("redis最大内存", limit=5)
        assert r["results"], "should find the sibling fact"
        hit = r["results"][0]
        assert "retracted_refs" in hit, f"hit should have retracted_refs field, keys: {list(hit.keys())[:10]}"
        assert len(hit["retracted_refs"]) > 0, "retracted_refs should be non-empty"

    def test_retracted_tag_not_duplicated(self, tmp_path, monkeypatch):
        """Correcting the same fact twice should not add duplicate retracted tags."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("docker的容器端口是3000", source="codex")
        r2 = gw.remember("docker的镜像是node18", source="codex")
        gw.correct(r1["uid"], "docker的容器端口是3001", reason="port changed")
        gw.retire(r1["uid"], reason="fully decommissioned")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "retraction.db"))
        tags2 = conn.execute("SELECT tags FROM facts WHERE uid=?", (r2["uid"],)).fetchone()
        conn.close()
        if tags2 and tags2[0]:
            count = tags2[0].count("retracted:")
            assert count <= 1, f"retracted tag should not duplicate, found {count}"
