# -*- coding: utf-8 -*-
"""tests/test_absorb_semantic.py — a46: semantic absorb (P0 bug fix)

Verifies /absorb uses embeddings (method=semantic) with digit-aware
duplicate guard, and falls back to keyword overlap when embed model
is unavailable.
"""
import os, sys, pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = os.path.join(
    __import__("tempfile").mkdtemp(prefix="mt_absorb_test_"), "test.db")

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
    # Seed via the API itself so facts land in whatever DB api_server is using
    # (module-level MEM_DB ownership belongs to whichever test file imports it first).

requires_fastapi = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


def _embed_available():
    try:
        import embed_local
        return embed_local.available()
    except Exception:
        return False


requires_embed = pytest.mark.skipif(not _embed_available(),
                                    reason="local embedding model not installed on this runner")


def _seed():
    """Seed directly into the DB that api_server will read at request time
    (os.environ['MEM_DB'] can be re-pointed by later-imported test modules,
    so we must read it at setup, not trust the module that imported first)."""
    import sqlite3, hashlib, time
    db = os.environ.get("MEM_DB")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS facts (
        uid TEXT PRIMARY KEY, type TEXT, content TEXT, source TEXT,
        status TEXT DEFAULT 'active', scope TEXT DEFAULT 'shared',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    for c in ("Clash proxy port is 7890", "user likes pizza", "STM32 delay uses SysTick timer"):
        h = hashlib.md5((c + "test").encode()).hexdigest()[:12]
        uid = "fact-%s-%s" % (time.strftime("%Y%m%d%H%M%S"), h)
        conn.execute("INSERT OR IGNORE INTO facts (uid, type, content, source) VALUES (?,?,?,?)",
                     (uid, "fact", c, "test"))
    conn.commit()
    conn.close()


@requires_fastapi
@requires_embed
class TestAbsorbSemantic:
    @classmethod
    def setup_class(cls):
        _seed()

    def test_port_change_is_update_not_duplicate(self):
        """Digit guard: 7890 vs 7897 must NOT be duplicate."""
        r = client.post("/absorb", json={
            "content": "Clash proxy port is 7897", "source": "test",
            "type": "fact", "dry_run": True})
        d = r.json()
        assert d["classification"] in ("update", "contradiction"), d
        assert d["method"] == "semantic", d

    def test_paraphrase_no_digits_is_duplicate(self):
        r = client.post("/absorb", json={
            "content": "user likes pizza very much", "source": "test",
            "type": "fact", "dry_run": True})
        d = r.json()
        assert d["classification"] == "duplicate", d

    def test_unrelated_is_new(self):
        r = client.post("/absorb", json={
            "content": "database migration finished on Friday", "source": "test",
            "type": "fact", "dry_run": True})
        d = r.json()
        assert d["classification"] in ("new", "related"), d

    def test_method_field_present(self):
        r = client.post("/absorb", json={
            "content": "hello world", "source": "test", "type": "fact",
            "dry_run": True})
        assert r.json()["method"] in ("semantic", "keyword")


@requires_fastapi
class TestAbsorbKeywordFallback:
    @classmethod
    def setup_class(cls):
        _seed()

    def test_fallback_when_model_missing(self):
        """When embed_local unavailable, degrade to keyword overlap."""
        with patch("embed_local.available", return_value=False):
            r = client.post("/absorb", json={
                "content": "Clash proxy port is 7897", "source": "test",
                "type": "fact", "dry_run": True})
            d = r.json()
            assert d["method"] == "keyword", d
            assert d["classification"] in ("update", "contradiction", "duplicate"), d
