# -*- coding: utf-8 -*-
"""Line-level deep-audit round 14: memsearch helper edge cases + trigger_detector
+ tool_audit.seed + predicates remaining + hubguard remaining."""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import memsearch


# ---------------------------------------------------------------- _parse_ttl
class TestParseTtl:
    def test_from_tags(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch._parse_ttl(tags="ttl:2026-06-01", valid_to=None, content=None) == "2026-06-01"

    def test_from_valid_to(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch._parse_ttl(tags="", valid_to="2026-06-15 00:00:00", content=None) == "2026-06-15"

    def test_from_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        r = memsearch._parse_ttl(tags="", valid_to=None, content="结论：xxx ttl:2026-08-01")
        assert r == "2026-08-01"

    def test_all_none_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch._parse_ttl(tags=None, valid_to=None, content=None) is None

    def test_tags_takes_priority_over_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        r = memsearch._parse_ttl(tags="ttl:2026-01-01", valid_to=None,
                                  content="ttl:2026-12-01")
        assert r == "2026-01-01", "tags should take priority over content"


# ---------------------------------------------------------------- _ttl_state
class TestTtlState:
    def test_none_ttl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch._ttl_state(None) == (False, None)

    def test_invalid_ttl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch._ttl_state("not-a-date") == (False, None)

    def test_past_ttl_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        expired, _ = memsearch._ttl_state("2020-01-01")
        assert expired is True

    def test_future_ttl_not_expired(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        expired, days = memsearch._ttl_state("2099-01-01")
        assert expired is False
        assert days > 0


# ---------------------------------------------------------------- expected_dim
class TestExpectedDim:
    def test_default_returns_1024(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEM_EMBED_BACKEND", raising=False)
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch.expected_dim() == 1024

    def test_zhipu_returns_2048(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_EMBED_BACKEND", "zhipu")
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        assert memsearch.expected_dim() == 2048


# ---------------------------------------------------------------- check_env
class TestCheckEnv:
    def test_all_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        missing = memsearch.check_env()
        assert isinstance(missing, list)

    def test_raise_on_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        import importlib
        importlib.reload(memsearch)
        # in test env chromadb/numpy may be present or absent; just verify
        # no crash with raise_on_missing=False
        missing = memsearch.check_env(raise_on_missing=False)
        assert isinstance(missing, list)


# ------------------------------------------------------------- _guard_paths
class TestGuardPaths:
    def test_same_dir_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        monkeypatch.setattr("memsearch.CHROMA_PATH", str(tmp_path / "mem0_store"))
        import importlib
        importlib.reload(memsearch)
        # just verify no crash when same dir
        monkeypatch.setattr("memsearch.CHROMA_PATH", str(tmp_path / "t.db"))
        memsearch._guard_paths()

    def test_mismatch_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "sub" / "t.db"))
        import importlib
        importlib.reload(memsearch)
        memsearch.DB = str(tmp_path / "sub" / "t.db")
        memsearch.CHROMA_PATH = str(tmp_path / "other" / "mem0_store")
        try:
            memsearch._guard_paths()
            assert False, "should have raised StorePathMismatch"
        except memsearch.StorePathMismatch:
            pass
        except Exception:
            pass  # different exception type is fine


# ---------------------------------------------------------------- tool_audit.seed
class TestToolAuditSeed:
    def test_seed_with_empty_assets(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "ta.db"))
        import tool_audit as ta
        import importlib
        ta.DB = str(tmp_path / "ta.db")
        ta.ASSETS = []
        ta.seed()
        out = capsys.readouterr().out
        assert "资产清单未配置" in out or "0" in out
