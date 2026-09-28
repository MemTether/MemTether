# -*- coding: utf-8 -*-
"""tests/test_api_server.py — S5: REST API server pytest tests (v2)"""
import os, sys, pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = ":memory:"

from fastapi.testclient import TestClient
from api_server import app
import gateway

client = TestClient(app)

class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True

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

class TestEndpoints:
    def test_remember(self):
        with patch.object(gateway, "remember", return_value={"ok": True, "uid": "t1", "op": "insert"}):
            r = client.post("/remember", json={"content": "Test", "type": "fact", "source": "api"})
            assert r.status_code == 200
            assert r.json()["ok"] is True

    def test_search(self):
        with patch.object(gateway, "search", return_value={"results": [{"uid": "x", "score": 0.9}]}):
            r = client.post("/search", json={"query": "test", "limit": 5})
            assert r.status_code == 200

    def test_stats(self):
        with patch.object(gateway, "stats", return_value={"facts": 100}):
            r = client.get("/stats")
            assert r.status_code == 200
            assert r.json()["facts"] == 100

    def test_correct(self):
        with patch.object(gateway, "correct", return_value={"ok": True}):
            r = client.post("/correct", json={"old_uid": "x", "new_content": "y", "source": "z"})
            assert r.status_code == 200

    def test_retire(self):
        with patch.object(gateway, "retire", return_value={"ok": True}):
            r = client.post("/retire", json={"uid": "x", "source": "z"})
            assert r.status_code == 200

    def test_qvalue(self):
        with patch.object(gateway, "bump_qvalue", return_value={"ok": True, "new_q": 0.6}):
            r = client.post("/qvalue", json={"uid": "x", "reward": 1.0, "source": "z"})
            assert r.status_code == 200

    def test_list(self):
        with patch.object(gateway, "get_conn") as mock_conn:
            mock_r = MagicMock()
            mock_r.__iter__ = iter([])
            mock_conn.return_value.execute.return_value.fetchall.return_value = []
            mock_conn.return_value.close = MagicMock()
            r = client.get("/list")
            assert r.status_code == 200
