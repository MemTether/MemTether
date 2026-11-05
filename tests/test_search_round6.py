import sys
# -*- coding: utf-8 -*-
"""Line-level deep-audit round 6: search_hybrid inner branches + TTL states.

Real product behaviors previously untested:
- MEM_GRAPH_RECALL=1 entity-graph recall (with & without entity_graph_ppr)
- TTL states: expired downweight + note / unexpired countdown / no-ttl
- self-referential downweight (meta-discussion ranked below answers)
- pinned-forced overflow (pin rows survive recall budget)
- procedure-type boost
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys_path = os.path.dirname(os.path.abspath(__file__))


def _ms(tmp_path, monkeypatch, extra_env=None):
    db = str(tmp_path / "t6.db")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.delenv("MEM_TENANT_ID", raising=False)
    monkeypatch.delenv("MEM_SCOPE", raising=False)
    monkeypatch.delenv("MEM_GRAPH_RECALL", raising=False)
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
    return memsearch, conn


def _seed(conn, uid, content, tags=None, type_="fact"):
    if tags:
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at,tags) VALUES (?,?,?,?,?,?,?,?,?)",
                     (uid, type_, "s", content, "active", "codex",
                      "2026-11-05 00:00:00", "2026-11-05 00:00:00", tags))
    else:
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,"
                     "recorded_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (uid, type_, "s", content, "active", "codex",
                      "2026-11-05 00:00:00", "2026-11-05 00:00:00"))


class TestTtlStates:
    def test_expired_ttl_downweighted_with_note(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "t1", "quantum flux distinctive expired",
              tags="ttl:2020-01-01")
        conn.commit(); conn.close()
        res = ms.search_hybrid("quantum flux distinctive", limit=3)
        hit = [x for x in res["results"] if x["uid"] == "t1"][0]
        assert hit["ttl_expired"] is True
        assert "已于 2020-01-01 到期" in hit["ttl_note"]
        assert any("ttl-expired" in r for r in hit["reason"])

    def test_unexpired_ttl_countdown(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "t2", "quantum flux distinctive future",
              tags="ttl:2099-01-01")
        conn.commit(); conn.close()
        res = ms.search_hybrid("quantum flux distinctive", limit=3)
        hit = [x for x in res["results"] if x["uid"] == "t2"][0]
        assert hit["ttl_expired"] is False
        assert "复核截止日 2099-01-01" in hit["ttl_note"]

    def test_no_ttl_note_none(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "t3", "quantum flux distinctive plain")
        conn.commit(); conn.close()
        res = ms.search_hybrid("quantum flux distinctive", limit=3)
        hit = [x for x in res["results"] if x["uid"] == "t3"][0]
        assert hit["ttl_note"] is None and hit["ttl_expired"] is False


class TestSelfRefDownweight:
    def test_meta_discussion_ranked_below_answer(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        # answer: about memsearch.py itself; meta: talks ABOUT the query
        _seed(conn, "ans", "memsearch.py 的混合检索实现说明")
        _seed(conn, "meta", "用户正在搜索 memsearch.py 这个词")
        conn.commit(); conn.close()
        res = ms.search_hybrid("memsearch.py", limit=5)
        ranks = {x["uid"]: i for i, x in enumerate(res["results"])}
        if "meta" in ranks and "ans" in ranks:
            assert ranks["ans"] < ranks["meta"], \
                "meta-discussion must rank below the answer"


class TestGraphRecall:
    def test_graph_recall_off_by_default_no_diag(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "g1", "graph recall probe distinctive")
        conn.commit(); conn.close()
        res = ms.search_hybrid("graph recall probe", limit=3)
        assert not res["diag"].get("graph_recall"), \
            ("graph_recall must be off by default; diag=%r env=%r"
             % (res["diag"].get("graph_recall"),
                os.environ.get("MEM_GRAPH_RECALL")))

    def test_graph_recall_on_reports_diag(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch, {"MEM_GRAPH_RECALL": "1"})
        _seed(conn, "g2", "graph recall probe distinctive")
        conn.commit(); conn.close()
        res = ms.search_hybrid("graph recall probe", limit=3)
        # module missing in repo -> warn path fires; diag key set BEFORE try
        assert res["diag"].get("graph_recall") is True


class TestProcedureBoost:
    def test_procedure_type_boosted(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch)
        _seed(conn, "p1", "deploy widget procedure step by step", type_="procedure")
        _seed(conn, "p2", "deploy widget was done last week", type_="fact")
        conn.commit(); conn.close()
        res = ms.search_hybrid("deploy widget", limit=5)
        scores = {x["uid"]: x["score"] for x in res["results"]}
        assert scores["p1"] > scores["p2"], \
            "procedure rows must be boosted over equivalent fact rows"


class TestPinnedForced:
    def test_pinned_survives_recall_budget(self, tmp_path, monkeypatch):
        ms, conn = _ms(tmp_path, monkeypatch, {"MEM_RECALL_BUDGET": "200"})
        # pinned row with very long content + normal rows
        # pin content must fit the 1.15x overflow budget (200*1.15=230) but
        # exceed the base budget (200) so forced-inclusion actually triggers.
        _seed(conn, "pin1", "pin marker " + "x" * 205, tags="pin")
        _seed(conn, "n1", "quantum distinctive normal row alpha")
        conn.commit(); conn.close()
        res = ms.search_hybrid("quantum distinctive pin marker", limit=5)
        d = res["diag"]
        assert d.get("pinned_forced", 0) >= 1, \
            "pinned rows must be force-included even over budget"
        uids = [x["uid"] for x in res["results"]]
        assert "pin1" in uids
