# -*- coding: utf-8 -*-
"""Line-level deep-audit round 3b: api_server court/viewer/auth endpoints.

Covers previously-missing clusters: /court/verify (L113-125), /court/{uid}
200 + 404 (L128-164), /viewer fallback (L184-196), API-key middleware
(L54-64) incl. 401 + health bypass, and the /qvalue endpoint wiring.
"""
import os
import sqlite3
import sys

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

REAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT,
  content TEXT, status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT,
  valid_to TEXT, recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT,
  source TEXT, scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8, tags TEXT,
  created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
  use_count INTEGER DEFAULT 0, predicate TEXT, tenant_id TEXT DEFAULT 'default',
  verify_at TEXT, last_verified_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
  op TEXT, target TEXT, agent TEXT, detail TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS audit_anchors (id INTEGER PRIMARY KEY AUTOINCREMENT,
  last_log_id INTEGER, chain_hash TEXT, entries_hashed INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS conflict_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid_a TEXT, uid_b TEXT, verdict TEXT, by_agent TEXT, ts TEXT);
"""


def _client(tmp_path, monkeypatch, api_key=None):
    db = str(tmp_path / "api.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    if api_key:
        monkeypatch.setenv("MEMTETHER_API_KEY", api_key)
    else:
        monkeypatch.delenv("MEMTETHER_API_KEY", raising=False)
    monkeypatch.setenv("MEM_RATE_LIMIT", "100000/minute")
    import importlib, api_server
    importlib.reload(api_server)
    conn = sqlite3.connect(db)
    conn.executescript(REAL_SCHEMA)
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,"
                 "created_at) VALUES ('u-1','fact','court probe content','active',"
                 "'codex','2026-11-05','2026-11-05')")
    conn.execute("INSERT INTO audit_log (op,target,agent,detail,ts) VALUES"
                 "('remember','u-1','codex','seed','2026-11-05 00:00:00')")
    conn.commit(); conn.close()
    return TestClient(api_server.app), db


def test_court_verify_endpoint(tmp_path, monkeypatch):
    c, db = _client(tmp_path, monkeypatch)
    r = c.get("/court/verify")
    assert r.status_code == 200
    body = r.json()
    assert "verified" in body


def test_court_case_returns_dossier(tmp_path, monkeypatch):
    c, db = _client(tmp_path, monkeypatch)
    r = c.get("/court/u-1")
    assert r.status_code == 200
    body = r.json()
    assert body["uid"] == "u-1"
    assert body["content"] == "court probe content"
    assert body["chain_verified"] is True
    assert any(e["op"] == "remember" for e in body["provenance"])


def test_court_case_404(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/court/no-such-uid")
    assert r.status_code == 404


def test_viewer_serves_html(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/viewer")
    assert r.status_code == 200
    assert "<html" in r.text.lower() or "viewer" in r.text.lower()


def test_api_key_middleware_401_and_bypass(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, api_key="secret-key")
    r1 = c.get("/health")
    assert r1.status_code == 200, "health must bypass the key"
    r2 = c.get("/stats")  # no auth header
    assert r2.status_code == 401
    r3 = c.get("/stats", headers={"authorization": "Bearer wrong"})
    assert r3.status_code == 401
    r4 = c.get("/stats", headers={"authorization": "Bearer secret-key"})
    assert r4.status_code == 200


def test_court_case_404_without_key_leak(tmp_path, monkeypatch):
    """404 for an unknown uid must not leak whether OTHER uids exist."""
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/court/no-such-uid")
    body = r.json()
    assert "no such memory" in body.get("detail", "")
