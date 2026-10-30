# -*- coding: utf-8 -*-
"""Tests for remaining untested memtether functions."""
import io
import os
import pytest

os.environ.setdefault("MEM_DB", ":memory:")
os.environ.setdefault("MEM_SKIP_VECTOR", "1")


class TestCliOut:
    def test_emit(self, capsys):
        from cli_out import emit
        emit("test output")
        captured = capsys.readouterr()
        assert "test output" in captured.out

    def test_unicode(self, capsys):
        from cli_out import emit
        emit("中文 🦞 ✅")
        captured = capsys.readouterr()
        assert "中文" in captured.out


class TestMemtetherPaths:
    def test_default_db(self, monkeypatch):
        # clear MEM_DB env to test the default resolution
        monkeypatch.delenv("MEM_DB", raising=False)
        from memtether_paths import default_db
        result = default_db()
        assert result
        assert "memtether" in result.lower() or "memory" in result.lower()


class TestMemtetherPipeline:
    def test_import(self):
        import memtether_pipeline
        assert hasattr(memtether_pipeline, "__name__")


class TestRerank:
    def test_available(self):
        from rerank import available
        result = available()
        assert isinstance(result, (bool, list))


class TestMemtetherAssets:
    def test_import(self):
        import memtether_assets
        assert hasattr(memtether_assets, "__name__")
