# -*- coding: utf-8 -*-
"""Line-level deep-audit round 4b: search_hybrid inner branches.

Env-driven product behaviors that had no direct tests:
- MEM_BUMP write-back (use_count += 1 on retrieval)
- MEM_MULTI_ROUND recursion + score-merge
- supersession dedup layer (R7) end-to-end in search (not just unit level)
- token-layer application via gateway.search for L0/L1
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _ms(tmp_path, monkeypatch, extra_env=None):
    db = str(tmp_path / "sb.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
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
    conn.commit(); conn.close()
    return memsearch, db


def _seed(conn, uid, content, superseded_by=None):
    cols = ["uid","type","subject","content","status","source","recorded_at","created_at"]
    vals = [uid, "fact", "s", content, "active", "codex",
            "2026-11-05 00:00:00", "2026-11-05 00:00:00"]
    if superseded_by:
        cols.append("superseded_by"); vals.append(superseded_by)
    q = "INSERT INTO facts (%s) VALUES (%s)" % (",".join(cols), ",".join("?"*len(cols)))
    conn.execute(q, vals)



class TestBumpOnRetrieval:
    def test_use_count_bumped_once_per_hit(self, tmp_path, monkeypatch):
        ms, db = _ms(tmp_path, monkeypatch, {"MEM_BUMP": "1"})
        conn = sqlite3.connect(db)
        _seed(conn, "b1", "bump me distinctive alpha")
        conn.commit(); conn.close()
        ms.search_hybrid("bump me distinctive alpha", limit=5)
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT use_count FROM facts WHERE uid='b1'").fetchone()[0]
        conn.close()
        assert n == 1, "retrieval must write back use_count"

    def test_bump_disabled(self, tmp_path, monkeypatch):
        ms, db = _ms(tmp_path, monkeypatch, {"MEM_BUMP": "0"})
        conn = sqlite3.connect(db)
        _seed(conn, "b2", "no bump distinctive beta")
        conn.commit(); conn.close()
        ms.search_hybrid("no bump distinctive beta", limit=5)
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT use_count FROM facts WHERE uid='b2'").fetchone()[0]
        conn.close()
        assert n == 0


class TestMultiRound:
    def test_multi_round_merges_second_query(self, tmp_path, monkeypatch):
        ms, db = _ms(tmp_path, monkeypatch,
                     {"MEM_MULTI_ROUND": "1", "MEM_MULTI_ROUND_THR": "0.9"})
        conn = sqlite3.connect(db)
        _seed(conn, "m1", "quantum flux capacitor calibration")
        conn.commit(); conn.close()
        # low-info first query -> score below thr -> recursion with _q2
        # multi-word query with stopwords: _q2 strips to core term -> differs
        # from q -> recursion fires; merged results include the seeded fact.
        res = ms.search_hybrid("how to calibration", limit=5)
        d = res["diag"]
        assert d.get("multi_round") is True, d
        assert any("quantum" in x["content"] for x in res["results"])

    def test_multi_round_off_by_default(self, tmp_path, monkeypatch):
        ms, db = _ms(tmp_path, monkeypatch)
        conn = sqlite3.connect(db)
        _seed(conn, "m2", "multi round off check")
        conn.commit(); conn.close()
        res = ms.search_hybrid("multi round off check", limit=5)
        assert res["diag"].get("multi_round") is None


class TestSupersessionDedup:
    def test_old_version_removed_when_newest_present(self, tmp_path, monkeypatch):
        ms, db = _ms(tmp_path, monkeypatch)
        conn = sqlite3.connect(db)
        _seed(conn, "old-1", "alpha config uses port A", superseded_by="new-1")
        _seed(conn, "new-1", "alpha config uses port B")
        conn.commit(); conn.close()
        res = ms.search_hybrid("alpha config uses port", limit=5)
        uids = [x["uid"] for x in res["results"]]
        assert "new-1" in uids and "old-1" not in uids
        assert res["diag"].get("supersession_dedup", 0) >= 1


class TestGatewayLayers:
    def test_gateway_layer_l0(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "lay.db"))
        monkeypatch.delenv("MEM_TENANT_ID", raising=False)
        import importlib, gateway
        importlib.reload(gateway)
        gateway.init_db()
        gateway.remember("layer L0 probe 首句结论。第二句细节补充。",
                         source="codex")
        r = gateway.search("layer L0 probe", layer="L0")
        assert r["ok"] and r["results"]
        hit = r["results"][0]
        assert hit.get("layers", {}).get("L0"), hit
        # L0 content must be shorter than full text
        assert len(hit["content"]) < len("layer L0 probe 首句结论。第二句细节补充。"), \
            "L0 content must be the first sentence only"
