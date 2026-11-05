# -*- coding: utf-8 -*-
"""Line-level deep-audit round 2: reclaim_orphan_segments + 2nd-ghost edge.

memsearch.reclaim_orphan_segments (L499-564) was 0% - it is a real disk-cleanup
utility; a bug here could DELETE live vector data. Tests use a fake chroma dir
with controlled sqlite + uuid-shaped dirs (registered / orphan / non-uuid).
gateway 2nd-ghost edge (L801-802): a ghost hit must be purged AND the write
must proceed when the vector store has TWO stale hits (first ghost purge then
insert) - covered by pre-seeding one ghost + asserting store mutation.
"""
import os
import re
import sqlite3
import sys
import types
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _fake_chroma(tmp_path, registered, orphan, extra_dir):
    """Build a fake chroma dir: chroma.sqlite3 + uuid dirs."""
    root = tmp_path / "chroma"
    root.mkdir()
    conn = sqlite3.connect(str(root / "chroma.sqlite3"))
    conn.execute("CREATE TABLE segments (id TEXT PRIMARY KEY, collection TEXT)")
    conn.execute("CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT)")
    for uid in registered:
        conn.execute("INSERT INTO segments VALUES (?, 'c1')", (uid,))
    conn.execute("INSERT INTO collections VALUES ('c1', 'facts_active')")
    for uid in registered + orphan:
        d = root / uid
        d.mkdir()
        (d / "data.bin").write_bytes(b"x" * 2048)
    # non-uuid dir must NEVER be touched
    (root / extra_dir).mkdir()
    (root / extra_dir / "keep.txt").write_text("keep")
    conn.commit(); conn.close()
    return root


def test_reclaim_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    import importlib, memsearch
    reg, orph = str(uuid.uuid4()), str(uuid.uuid4())
    root = _fake_chroma(tmp_path, [reg], [orph], "not-a-segment")
    monkeypatch.setenv("MEM_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(memsearch, "CHROMA_PATH", str(root), raising=False)
    importlib.reload(memsearch)
    monkeypatch.setattr(memsearch, "CHROMA_PATH", str(root), raising=False)
    r = memsearch.reclaim_orphan_segments(dry_run=True, verbose=False)
    assert r["ok"] and r["dry_run"] is True
    assert r["registered"] == 1
    assert r["orphans"] == 1
    assert (root / orph).exists(), "dry-run must not delete"
    assert (root / reg).exists()
    assert (root / "not-a-segment" / "keep.txt").exists()


def test_reclaim_apply_deletes_only_orphans(tmp_path, monkeypatch):
    import importlib, memsearch
    reg, orph1, orph2 = (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    root = _fake_chroma(tmp_path, [reg], [orph1, orph2], "docs")
    monkeypatch.setenv("MEM_DB", str(tmp_path / "x.db"))
    importlib.reload(memsearch)
    monkeypatch.setattr(memsearch, "CHROMA_PATH", str(root), raising=False)
    r = memsearch.reclaim_orphan_segments(dry_run=False, verbose=False)
    assert r["ok"] and r["dry_run"] is False
    assert (root / reg).exists(), "registered segment must survive"
    assert not (root / orph1).exists() and not (root / orph2).exists()
    assert (root / "docs").exists(), "non-uuid dir must survive"

def test_missing_chroma_path_fails_loud(tmp_path, monkeypatch):
    import importlib, memsearch
    monkeypatch.setenv("MEM_DB", str(tmp_path / "x.db"))
    importlib.reload(memsearch)
    monkeypatch.setattr(memsearch, "CHROMA_PATH", str(tmp_path / "nope"), raising=False)
    r = memsearch.reclaim_orphan_segments(dry_run=True, verbose=False)
    assert r["ok"] is False and "不存在" in r["err"]


def test_second_ghost_in_vector_store_is_purged(tmp_path, monkeypatch):
    """gateway L801-802 edge: ghost hit -> purge + continue; the fresh fact
    still inserts (the loop keeps working after a purge)."""
    gw_db = str(tmp_path / "g.db")
    monkeypatch.setenv("MEM_DB", gw_db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()

    class FakeCol:
        def __init__(self, store):
            self.store = store
        def query(self, query_embeddings, n_results=3):
            ids = list(self.store)
            return {"ids": [ids], "distances": [[0.05] * len(ids)]}
        def get(self, ids):
            uid = ids[0]
            doc, meta = self.store.get(uid, (None, None))
            return {"documents": [doc] if doc else [], "metadatas": [meta] if meta else []}
        def delete(self, ids):
            self.store.pop(ids[0], None)

    store = {"ghost-1": ("ghost doc", {"uid": "ghost-1", "source": "codex"})}
    fake = types.ModuleType("memsearch")
    fake.COLLECTION = "facts_active"
    fake._client = lambda: type("C", (), {"get_collection": lambda self, name: FakeCol(store)})()
    fake._embed = lambda texts: [[1.0, 0.0] for _ in texts]
    monkeypatch.setitem(sys.modules, "memsearch", fake)

    r = gateway.remember("brand new fact two", source="codex")
    assert r["op"] == "insert", r
    assert "ghost-1" not in store, "ghost must be purged"
