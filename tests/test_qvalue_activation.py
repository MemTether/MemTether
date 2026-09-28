# -*- coding: utf-8 -*-
"""tests/test_qvalue_activation.py — A3: Q-Value activation tests"""
import os, sys, sqlite3, tempfile, pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use a real temp file, not :memory: (hubguard can't handle ":memory:" in lock paths)
_temp_dir = tempfile.mkdtemp(prefix="qvalue_test_")
os.environ["MEM_DB"] = os.path.join(_temp_dir, "test.db")

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
    from api_server import app
    client = TestClient(app)
except ImportError:
    HAS_FASTAPI = False

import gateway

requires_fastapi = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@requires_fastapi
class TestQValue:
    def _setup_db(self):
        """Create a fresh temp DB, patch gateway.DB, return db_path."""
        fd, db_path = tempfile.mkstemp(suffix=".db", dir=_temp_dir)
        os.close(fd)
        os.unlink(db_path)
        self._old_db = gateway.DB
        gateway.DB = db_path
        gateway.init_db()
        return db_path

    def _teardown_db(self, db_path):
        gateway.DB = self._old_db
        try:
            os.unlink(db_path)
        except:
            pass

    def test_bump_qvalue_changes_q_value(self):
        """Real bump_qvalue call should change q_value from default 0.5."""
        db_path = self._setup_db()
        try:
            r = gateway.remember("Test fact for QValue", type="fact", source="qvalue_test")
            uid = r.get("uid")
            assert uid is not None

            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT q_value FROM facts WHERE uid=?", (uid,)).fetchone()
            conn.close()
            assert row is not None
            initial_q = row[0]

            result = gateway.bump_qvalue(uid, reward=1.0, agent="test")
            assert result is not None

            conn = sqlite3.connect(db_path)
            row2 = conn.execute("SELECT q_value FROM facts WHERE uid=?", (uid,)).fetchone()
            conn.close()
            assert row2 is not None
            assert row2[0] != initial_q, f"q should change: {initial_q} -> {row2[0]}"
            assert row2[0] > initial_q, "q should increase with reward=1.0"
        finally:
            self._teardown_db(db_path)

    def test_default_q_is_half(self):
        db_path = self._setup_db()
        try:
            r = gateway.remember("Default Q test", type="fact", source="test")
            uid = r.get("uid")
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT q_value FROM facts WHERE uid=?", (uid,)).fetchone()
            conn.close()
            assert row is not None
            assert abs(row[0] - 0.5) < 0.01
        finally:
            self._teardown_db(db_path)

    def test_bump_returns_dict(self):
        db_path = self._setup_db()
        try:
            r = gateway.remember("Return test", type="fact", source="test")
            uid = r.get("uid")
            result = gateway.bump_qvalue(uid, reward=1.0, agent="test")
            assert isinstance(result, dict)
        finally:
            self._teardown_db(db_path)
