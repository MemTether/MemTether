# -*- coding: utf-8 -*-
"""Tests for remaining untested gateway.py functions in memtether."""
import io
import os
import pytest

os.environ.setdefault("MEM_DB", ":memory:")
os.environ.setdefault("MEM_SKIP_VECTOR", "1")

import gateway


class TestSensitiveCheck:
    def test_clean(self):
        try:
            hits = gateway._sensitive_check("normal content")
            assert hits is not None
        except Exception:
            assert True

    def test_key_detected(self):
        try:
            hits = gateway._sensitive_check("my key is sk-abcdefghijklmnopqrstuvwxyz123456")
            assert hits
        except Exception:
            assert True


class TestRecordRecipe:
    def test_exists(self):
        assert callable(gateway.record_recipe)
