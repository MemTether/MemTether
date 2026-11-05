
# -*- coding: utf-8 -*-
"""Line-level deep-audit round (2026-11-05): cover previously 0% clusters.

Chosen from a full-suite branch-coverage run (term-missing):
- gateway.record_recipe (L1361-1402) - 0%: public write API (P0-2 roadmap).
- gateway.as_of (L1122-1178) + gateway.timeline (L1181+) - 0%: bi-temporal
  read API, the project's core differentiator.
- gateway.remember vector-dedup branches (L758-809): cross-source
  feedback-loop gate (W1.6-5), near-dup noop, ghost-vector purge - only
  reachable with a live vector client, so we stub memsearch in sys.modules.
"""
import hashlib
import json
import os
import sqlite3
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _fresh(tmp_path, monkeypatch):
    db = str(tmp_path / "line.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


# --------------------------------------------------------------- record_recipe
class TestRecordRecipe:
    def test_insert_then_update_same_uid(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        r1 = gw.record_recipe("deploy site", ["step one", "step two"],
                              preferred_tool="rsync", source="codex")
        assert r1["ok"] and r1["op"] == "insert"
        r2 = gw.record_recipe("deploy site", ["step one v2"],
                              source="codex")
        assert r2["ok"] and r2["op"] == "update"
        # stable uid = md5(primary)
        assert r1["uid"] == "recipe-" + hashlib.md5(b"deploy site").hexdigest()[:16]
        conn = sqlite3.connect(gw.DB)
        row = conn.execute("SELECT steps FROM recipes WHERE uid=?", (r1["uid"],)).fetchone()
        audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE target=?",
                             (r1["uid"],)).fetchone()[0]
        conn.close()
        assert json.loads(row[0]) == ["step one v2"]
        assert audit >= 2

    def test_validation_errors(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        assert gw.record_recipe("", ["x"], source="codex")["ok"] is False
        assert gw.record_recipe("pat", ["  ", ""], source="codex")["ok"] is False

    def test_cli_steps_must_be_json(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        env = dict(os.environ); env["MEM_DB"] = gw.DB
        env["MEM_SKIP_VECTOR"] = "1"; env["PYTHONIOENCODING"] = "utf-8"
        import subprocess
        r = subprocess.run([sys.executable, "-m", "gateway", "record_recipe",
                            "pat", "--steps", "not-json"],
                           cwd=str(ROOT), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode == 1
        assert "--steps must be JSON array" in (r.stdout or "")


# ------------------------------------------------------------- as_of / timeline
class TestAsOfTimeline:
    def test_valid_axis_basic(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        gw.remember("asof fact A valid", valid_from="2026-01-01 00:00:00",
                    source="codex")
        r = gw.as_of("2026-06-01 00:00:00", kind="valid")
        assert r["kind"] == "valid"
        uids = [x["uid"] for x in r["rows"]]
        assert any(u.startswith("fact-") for u in uids)
        assert r["active_now"] >= 1

    def test_known_axis_uses_recorded(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        gw.remember("asof fact B known", source="codex")
        r = gw.as_of("2099-01-01", kind="known")
        assert r["count"] >= 1

    def test_filters_type_source(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        gw.remember("asof dec item", type="decision", source="codex")
        r = gw.as_of("2099-01-01", kind="known", ftype="decision", source="codex")
        assert r["count"] >= 1
        r2 = gw.as_of("2099-01-01", kind="known", source="no-such-src")
        assert r2["count"] == 0

    def test_timeline_walks_chain(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        r1 = gw.remember("timeline v1", source="codex")
        r2 = gw.correct(r1["uid"], "timeline v2", reason="update")
        t = gw.timeline(r1["uid"])
        assert isinstance(t, list) and len(t) >= 1
        chain_uids = [x["uid"] for x in t]
        assert r1["uid"] in chain_uids

    def test_timeline_cycle_guard(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        # forge a 2-cycle via direct SQL: A.superseded_by=B, B.superseded_by=A
        conn = sqlite3.connect(gw.DB)
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at,superseded_by) VALUES "
                     "('cycA','fact','s','cA','active','codex','2026-11-05',"
                     "'2026-11-05','cycB')")
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at,superseded_by) VALUES "
                     "('cycB','fact','s','cB','active','codex','2026-11-05',"
                     "'2026-11-05','cycA')")
        conn.commit(); conn.close()
        t = gw.timeline("cycA", max_hops=20)
        # must terminate without infinite loop; seen-set caps the walk
        assert isinstance(t, list) and len(t) >= 1


# ------------------------------------------------- vector dedup branches (stub)
class _FakeCollection:
    def __init__(self, store):
        self._store = store  # dict uid -> (doc, meta)

    def query(self, query_embeddings, n_results=3):
        # return every stored uid; caller filters by sim threshold
        ids = list(self._store)
        return {"ids": [ids], "distances": [[0.05] * len(ids)]}  # sim 0.95

    def get(self, ids):
        uid = ids[0]
        doc, meta = self._store.get(uid, (None, None))
        return {"documents": [doc] if doc else [],
                "metadatas": [meta] if meta else []}

    def delete(self, ids):
        self._store.pop(ids[0], None)


class _FakeMemsearch(types.ModuleType):
    def __init__(self, store):
        super().__init__("memsearch")
        self.COLLECTION = "facts_active"
        self._store = store

    def _client(self):
        return type("C", (), {"get_collection": lambda self, name: _FakeCollection(self._store)})()

    def _embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def _install_stub(monkeypatch, store):
    import memsearch as real_ms  # ensure module name exists for reload semantics
    fake = _FakeMemsearch(store)
    fake._client = lambda: type("C", (), {"get_collection": lambda self, name: _FakeCollection(store)})()
    fake._embed = lambda texts: [[1.0, 0.0] for _ in texts]
    monkeypatch.setitem(sys.modules, "memsearch", fake)


class TestVectorDedupBranches:
    def test_cross_source_echo_is_feedback_loop(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        # seed existing row + vector-store copy from a DIFFERENT agent
        conn = sqlite3.connect(gw.DB)
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at) VALUES ('v1','fact','s','shared fact body',"
                     "'active','claude-code','2026-11-05','2026-11-05')")
        conn.commit(); conn.close()
        store = {"v1": ("shared fact body", {"uid": "v1", "source": "claude-code"})}
        _install_stub(monkeypatch, store)
        r = gw.remember("shared fact body (updated wording)", source="codex")
        assert r["op"] == "feedback_loop", r
        assert r["original_source"] == "claude-code"
        conn = sqlite3.connect(gw.DB)
        tags = conn.execute("SELECT tags FROM facts WHERE uid='v1'").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE content='shared fact body'").fetchone()[0]
        conn.close()
        assert "feedback_loop" in (tags or "")
        assert n == 1, "echo must not create a second row"

    def test_same_source_near_dup_is_noop(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        conn = sqlite3.connect(gw.DB)
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at) VALUES ('v2','fact','s','dup body here',"
                     "'active','codex','2026-11-05','2026-11-05')")
        conn.commit(); conn.close()
        store = {"v2": ("dup body here", {"uid": "v2", "source": "codex"})}
        _install_stub(monkeypatch, store)
        r = gw.remember("dup body here!", source="codex")
        assert r["op"] == "noop_near_dup", r

    def test_ghost_vector_is_purged(self, tmp_path, monkeypatch):
        gw = _fresh(tmp_path, monkeypatch)
        store = {"ghost-uid": ("ghost doc", {"uid": "ghost-uid", "source": "codex"})}
        _install_stub(monkeypatch, store)
        r = gw.remember("brand new fact not in store", source="codex")
        # ghost hit gets purged; the fresh fact inserts normally
        assert r["op"] == "insert", r
        assert "ghost-uid" not in store, "ghost vector must be purged"
