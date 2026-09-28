# -*- coding: utf-8 -*-
"""tests/test_api_server.py — S5: REST API server pytest tests (v3)"""
import os, sys, pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = ":memory:"

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    TestClient = None

import gateway
if HAS_FASTAPI:
    from api_server import app
    client = TestClient(app)

requires_fastapi = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@requires_fastapi
class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True


@requires_fastapi
class TestValidation:
    def test_remember_missing_source(self):
        r = client.post("/remember", json={"content": "test", "type": "fact"})
        assert r.status_code == 422

    def test_remember_empty_content(self):
        r = client.post("/remember", json={"content": "", "type": "fact", "source": "x"})
        assert r.status_code == 422

    def test_search_empty_query(self):
        r = client.post("/search", json={"query": ""})
        assert r.status_code == 422


@requires_fastapi
class TestEndpoints:
    def test_remember(self):
        with patch.object(gateway, "remember", return_value={"ok": True, "uid": "t1"}):
            r = client.post("/remember", json={"content": "Test", "type": "fact", "source": "api"})
            assert r.status_code == 200

    def test_search(self):
        with patch.object(gateway, "search", return_value={"results": []}):
            r = client.post("/search", json={"query": "test"})
            assert r.status_code == 200

    def test_stats(self):
        with patch.object(gateway, "stats", return_value={"facts": 100}):
            r = client.get("/stats")
            assert r.status_code == 200

    def test_correct(self):
        with patch.object(gateway, "correct", return_value={"ok": True}):
            r = client.post("/correct", json={"old_uid": "x", "new_content": "y", "source": "z"})
            assert r.status_code == 200

    def test_retire(self):
        with patch.object(gateway, "retire", return_value={"ok": True}):
            r = client.post("/retire", json={"uid": "x", "source": "z"})
            assert r.status_code == 200

    def test_qvalue(self):
        with patch.object(gateway, "bump_qvalue", return_value={"ok": True}):
            r = client.post("/qvalue", json={"uid": "x", "reward": 1.0, "source": "z"})
            assert r.status_code == 200

    def test_list(self):
        with patch.object(gateway, "get_conn") as mc:
            mc.return_value.execute.return_value.fetchall.return_value = []
            mc.return_value.close = MagicMock()
            r = client.get("/list")
            assert r.status_code == 200
