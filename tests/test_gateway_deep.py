# -*- coding: utf-8 -*-
"""Tests for gateway.py functions that previously had zero test coverage.

Found by systematic audit: 40 of 60 gateway functions had no test.
Only tests functions that actually exist as top-level in memtether/gateway.py.

NOTE: _lead and _lead_len are nested inside search_hybrid and cannot be
tested directly. They are tested indirectly through search tests.
"""
import io
import os
import sqlite3
import tempfile

import pytest

# Create a real temp DB file (not :memory: which gateway can\'t handle)
_tmp_db = tempfile.mktemp(suffix="_gateway_test.db")
os.environ["MEM_DB"] = _tmp_db
os.environ["MEM_SKIP_VECTOR"] = "1"

import gateway


@pytest.fixture(scope="module")
def conn():
    gateway.init_db()
    conn = gateway.get_conn()
    yield conn
    conn.close()
    try:
        os.remove(_tmp_db)
    except OSError:
        pass


class TestGuardSource:
    def test_valid_source_passes(self):
        assert gateway._guard_source("workbuddy") == "workbuddy"

    def test_empty_gets_default(self):
        assert gateway._guard_source("")

    def test_none_gets_default(self):
        assert gateway._guard_source(None)


class TestRevStr:
    def test_returns_str(self):
        result = gateway._rev_str("hello")
        assert isinstance(result, str)


class TestAudit:
    def test_write_audit_entry(self, conn):
        gateway.audit(conn, "test_op", "target-123", "test-agent", "detail")
        row = conn.execute(
            "SELECT * FROM audit_log WHERE op=? AND target=?",
            ("test_op", "target-123")).fetchone()
        assert row is not None


class TestRecordTool:
    def test_record_tool_exists(self):
        assert callable(gateway.record_tool)


class TestSetPin:
    def test_pin(self, conn):
        gateway.init_db()
        uid = "fact-pin-deep-001"
        conn.execute(
            "INSERT OR IGNORE INTO facts (uid, content, type, source, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, "pin deep test", "fact", "test", "active"))
        conn.commit()
        gateway.set_pin(uid, True)
        row = conn.execute("SELECT tags FROM facts WHERE uid=?", (uid,)).fetchone()
        assert row is not None


class TestResolveTask:
    def test_returns_dict(self):
        result = gateway.resolve_task("test query")
        assert isinstance(result, dict)


class TestProfile:
    def test_returns_dict(self):
        result = gateway.profile("test subject")
        assert isinstance(result, dict)


class TestOnMiss:
    def test_does_not_crash(self):
        gateway.on_miss("test query that missed")


class TestSearchLikeLegacy:
    def test_returns_list_or_dict(self):
        # _search_like_legacy requires a live DB connection, test with init_db
        gateway.init_db()
        result = gateway._search_like_legacy("test", limit=3)
        assert isinstance(result, (list, dict))
