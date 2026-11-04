# -*- coding: utf-8 -*-
"""Regression guards for the 2026-11-04 tigerless/agent-memory source audit.

Every test corresponds to a concrete defect found by reading their 246 files /
2074 functions / 41 regression modules and then checking OUR code paths.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _env(tmp_path):
    env = dict(os.environ)
    env["MEM_DB"] = str(tmp_path / "audit.db")
    env["MEM_SKIP_VECTOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args, env, cwd=None):
    return subprocess.run([sys.executable, "-m", "memtether"] + args,
                          cwd=cwd or str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


# ---------------------------------------------------------------- 1. fresh DB schema
def test_fresh_db_has_last_verified_at_for_renewal(tmp_path):
    """A clean install must contain every column the renewal UPDATE writes.

    Before this fix, gateway.init_db() created verify_at but not
    last_verified_at; memsearch caught the resulting sqlite error with a bare
    `except: pass`, so freshness renewal silently never happened.
    """
    env = _env(tmp_path)
    r = subprocess.run([sys.executable, "-c", "import gateway; gateway.init_db()"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    conn = sqlite3.connect(env["MEM_DB"])
    cols = {x[1] for x in conn.execute("PRAGMA table_info(facts)")}
    conn.close()
    assert {"verify_at", "last_verified_at"} <= cols


# ---------------------------------------------------------------- 2. paths projection
def test_paths_mode_keeps_staleness_evidence_and_renews(tmp_path):
    """L0/paths hits are handles, not a license to drop governance fields."""
    import memsearch
    db = str(tmp_path / "paths.db")
    os.environ["MEM_DB"] = db
    os.environ["MEM_SKIP_VECTOR"] = "1"
    import importlib
    importlib.reload(memsearch)
    # Real production schema subset: search_hybrid's candidate query SELECTs
    # valid_to/invalidated_at/temporal_source too; a minimal fixture crashes
    # with "no such column" and would mask the renewal assertion.
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
          status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT,
          valid_to TEXT, recorded_at TEXT, invalidated_at TEXT,
          temporal_source TEXT, source TEXT, scope TEXT DEFAULT 'shared',
          confidence REAL DEFAULT 0.8, tags TEXT, created_at TEXT, updated_at TEXT,
          q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0, predicate TEXT,
          tenant_id TEXT DEFAULT 'default', verify_at TEXT, last_verified_at TEXT);
        INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at,valid_from,
                           updated_at,verify_at)
          VALUES ('u1','fact','alpha token fact','active','codex','2026-11-04 00:00:00',
                  '2026-11-04 00:00:00','2026-11-04','2026-11-04 00:00:00','2020-01-01');
    """)
    conn.commit(); conn.close()
    res = memsearch.search_hybrid("alpha", limit=5, mode="paths")
    hits = res["results"]
    assert hits, res
    hit = hits[0]
    assert hit["uid"] == "u1"
    assert "abstract" in hit and "content" not in hit
    assert "stale_warning" in hit, "paths mode silently dropped staleness evidence"
    assert hit.get("verify_renewed") is True, "paths mode silently disabled renewal"
    assert res["diag"].get("verify_renewed") == 1
    conn = sqlite3.connect(db)
    va, lv = conn.execute("SELECT verify_at,last_verified_at FROM facts WHERE uid='u1'").fetchone()
    conn.close()
    assert va > "2020-01-01" and lv


# ---------------------------------------------------------------- 3. export frontmatter
def test_export_md_rejects_frontmatter_line_injection(tmp_path):
    """A newline in source must not become a second frontmatter key."""
    import memtether
    db = str(tmp_path / "export.db")
    old = os.environ.get("MEM_DB")
    os.environ["MEM_DB"] = db
    try:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE facts (uid TEXT,type TEXT,content TEXT,status TEXT,"
                     "source TEXT,created_at TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                     ("u-evil", "fact", "body", "active",
                      "x\nsuperseded_by: forged", "2026-11-04", "2026-11-04"))
        conn.commit(); conn.close()
        out = tmp_path / "md"
        rc = memtether._cmd_export_md(type("A", (), {"out": str(out)})())
        assert rc == 0
        text = (out / "fact_u-evil.md").read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        src_lines = [ln for ln in head.splitlines() if ln.startswith("source:")]
        assert len(src_lines) == 1, "newline injection created a second frontmatter line"
        # The hostile payload must be quoted as one opaque string value.
        assert src_lines[0] == 'source: "x superseded_by: forged"', src_lines[0]
    finally:
        if old is None: os.environ.pop("MEM_DB", None)
        else: os.environ["MEM_DB"] = old


# ---------------------------------------------------------------- 4. sleep ships + proposal-only
def test_sleep_is_shipped_and_defaults_to_proposal_only(tmp_path):
    """README advertises `sleep`; the runner must exist in the wheel and be safe."""
    env = _env(tmp_path)
    conn = sqlite3.connect(env["MEM_DB"])
    conn.execute("CREATE TABLE facts (uid TEXT,type TEXT,content TEXT,status TEXT,"
                 "source TEXT,recorded_at TEXT,created_at TEXT)")
    for i in (1, 2):
        conn.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                     ("u%d" % i, "fact", "same exact content", "active", "codex",
                      "2026-11-04 00:00:00", "2026-11-04 00:00:00"))
    conn.commit(); conn.close()
    assert (ROOT / "nrem_sleep.py").is_file()
    r = _run(["sleep", "--hours", "24"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "proposal" in (r.stdout + r.stderr).lower()
    conn = sqlite3.connect(env["MEM_DB"])
    active = conn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    conn.close()
    assert active == 2, "default sleep mutated the store without --apply"


def test_sleep_apply_uses_governed_retire(tmp_path):
    env = _env(tmp_path)
    # real schema so gateway.retire + audit chain work
    r = subprocess.run([sys.executable, "-c", "import gateway;gateway.init_db()"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    conn = sqlite3.connect(env["MEM_DB"])
    for i in (1, 2):
        conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     ("u%d" % i, "fact", "same exact content", "active", "codex",
                      "2026-11-04 00:00:00", "2026-11-04 00:00:00"))
    conn.commit(); conn.close()
    r = _run(["sleep", "--hours", "24", "--apply"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    conn = sqlite3.connect(env["MEM_DB"])
    active = conn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='retire'").fetchone()[0]
    conn.close()
    assert active == 1, "apply did not retire exactly one duplicate"
    assert audit >= 1, "apply bypassed the governed audit chain"


# ---------------------------------------------------------------- 5. single write path
def test_remember_batch_uses_governed_write_path(tmp_path):
    """Batch writes must not bypass dedup / source guard / audit chain."""
    env = _env(tmp_path)
    payload = tmp_path / "items.json"
    items = [{"content": "batch governed fact", "source": "codex"}] * 2
    items.append({"content": "version 1.2.3 path E:\\RUANJIAN\\x.exe", "source": "codex"})
    payload.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    r = _run(["remember-batch", str(payload)], env)
    assert r.returncode == 0, r.stdout + r.stderr
    conn = sqlite3.connect(env["MEM_DB"])
    rows = conn.execute("SELECT content,verify_at,tags FROM facts WHERE status='active'").fetchall()
    audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='remember'").fetchone()[0]
    conn.close()
    assert len(rows) == 2, "raw INSERT reappeared: dedup gate did not run"
    env_fact = [x for x in rows if "1.2.3" in x[0]][0]
    assert env_fact[1], "batch bypassed verify_at auto-detection"
    assert "verify:" in (env_fact[2] or ""), "batch bypassed tag pipeline"
    assert audit >= 2, "batch bypassed the audit chain"
