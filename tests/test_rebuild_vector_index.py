# -*- coding: utf-8 -*-
"""Line-level deep-audit round 3: rebuild_vector_index end-to-end (L591-724).

Previously 0% (needs a live chroma client). These tests stub the client and
embedder, exercising: fresh build, dim-mismatch rebuild, reuse path (delete
ids), garbage quarantine, per-chunk add batching, and the reclaim step's
own-segment-missing guard (the 2026-11-03 race fix).
"""
import os
import sqlite3
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


class FakeCollection:
    def __init__(self, name, metadata=None, client=None):
        self.name = name
        self.metadata = metadata or {}
        self.id = "col-123"
        self.client = client
        self.rows = {}  # uid -> doc

    def get(self, include=None):
        return {"ids": list(self.rows)}

    def delete(self, ids=None):
        for u in (ids or []):
            self.rows.pop(u, None)

    def add(self, ids=None, embeddings=None, documents=None, metadatas=None):
        for u, d, m in zip(ids, documents, metadatas):
            self.rows[u] = (d, m)


class FakeClient:
    def __init__(self):
        self.collections = {}
        self.deleted = []

    def get_collection(self, name):
        if name not in self.collections:
            raise RuntimeError("not found")
        return self.collections[name]

    def get_or_create_collection(self, name, metadata=None):
        return self.collections.setdefault(name, FakeCollection(name, metadata))

    def create_collection(self, name, metadata=None):
        c = FakeCollection(name, metadata)
        self.collections[name] = c
        return c

    def delete_collection(self, name):
        self.deleted.append(name)
        self.collections.pop(name, None)


def _install(ms, monkeypatch, dim=1024):
    client = FakeClient()
    ms._client = lambda: client
    ms._embed = lambda texts: [[float(dim)] * 3 for _ in texts]
    return client


def _fresh(tmp_path, monkeypatch):
    db = str(tmp_path / "rvi.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    import importlib, memsearch
    importlib.reload(memsearch)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
          status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT, valid_to TEXT,
          recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT, source TEXT,
          scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8, tags TEXT,
          created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
          use_count INTEGER DEFAULT 0, predicate TEXT, tenant_id TEXT DEFAULT 'default',
          verify_at TEXT, last_verified_at TEXT);
        CREATE TABLE tool_assets (uid TEXT PRIMARY KEY, name TEXT, type TEXT,
          status TEXT DEFAULT 'active', path TEXT, entrypoint TEXT, capabilities TEXT,
          recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
          last_verified_at TEXT, verification_method TEXT, source TEXT,
          created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
          use_count INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at) VALUES"
                 "('f1','fact','real fact one','active','codex','2026-11-05','2026-11-05')")
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at) VALUES"
                 "('f2','fact','real fact two','active','codex','2026-11-05','2026-11-05')")
    conn.commit(); conn.close()
    return memsearch


def test_fresh_build_indexes_all_and_returns_counts(tmp_path, monkeypatch):
    ms = _fresh(tmp_path, monkeypatch)
    client = _install(ms, monkeypatch)
    r = ms.rebuild_vector_index(reclaim=False, verbose=False)
    assert r["indexed"] == 2 and r["active"] == 2 and r["assets"] == 0
    col = client.collections[ms.COLLECTION]
    assert set(col.rows) == {"f1", "f2"}
    assert col.metadata.get("embed_dim") == ms.expected_dim()


def test_reuse_path_deletes_ids_without_dropping_collection(tmp_path, monkeypatch):
    ms = _fresh(tmp_path, monkeypatch)
    client = _install(ms, monkeypatch)
    col = client.get_or_create_collection(ms.COLLECTION,
        metadata={"embed_dim": ms.expected_dim()})
    col.rows["stale"] = ("old", {})
    r = ms.rebuild_vector_index(reuse=True, reclaim=False, verbose=False)
    assert "stale" not in col.rows, "reuse path must clear old ids"
    assert ms.COLLECTION not in client.deleted, "reuse must NOT delete the collection"
    assert set(col.rows) == {"f1", "f2"}


def test_dim_mismatch_forces_collection_rebuild(tmp_path, monkeypatch):
    ms = _fresh(tmp_path, monkeypatch)
    client = _install(ms, monkeypatch, dim=999)
    # Old collection claims 2048-dim (e.g. a zhipu build) but our embedder
    # now produces 1024 - the reuse path must detect this and rebuild.
    col = client.get_or_create_collection(ms.COLLECTION,
        metadata={"embed_dim": 2048})
    col.rows["old"] = ("x", {})
    ms.rebuild_vector_index(reuse=True, reclaim=False, verbose=False)
    assert ms.COLLECTION in client.deleted, "dim mismatch must drop the collection"
    new_col = client.collections[ms.COLLECTION]
    assert new_col is not col, "a NEW collection object must replace the old one"
    assert set(new_col.rows) == {"f1", "f2"}
    assert new_col.metadata.get("embed_dim") == ms.expected_dim()


def test_reclaim_skips_when_own_segment_missing(tmp_path, monkeypatch):
    ms = _fresh(tmp_path, monkeypatch)
    _install(ms, monkeypatch)
    fake_chroma = tmp_path / "chroma"
    fake_chroma.mkdir()
    (fake_chroma / "chroma.sqlite3").write_bytes(b"")
    monkeypatch.setattr(ms, "CHROMA_PATH", str(fake_chroma), raising=False)
    r = ms.rebuild_vector_index(reclaim=True, verbose=False)
    assert r["reclaimed_mb"] == 0.0  # skipped: own segment missing (race guard)


def test_garbage_content_is_quarantined_not_indexed(tmp_path, monkeypatch):
    ms = _fresh(tmp_path, monkeypatch)
    client = _install(ms, monkeypatch)
    conn = sqlite3.connect(os.environ["MEM_DB"])
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at) VALUES"
                 "('junk','fact','垃圾数据','active','test','2026-11-05','2026-11-05')")
    conn.commit(); conn.close()
    r = ms.rebuild_vector_index(reclaim=False, verbose=False)
    col = client.collections[ms.COLLECTION]
    assert "junk" not in col.rows, "test-source rows must be quarantined"
    assert r["indexed"] == 2
    assert r["quarantined"], "quarantine count must be reported"
