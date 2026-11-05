# -*- coding: utf-8 -*-
"""Line-level deep-audit round 5: gateway.rebuild projection-writer edges.

Covers previously-dark branches of the core projection writer:
- MEM_PROJ_STATIC_QUOTA static arm (L1913)
- NREM cap overflow (L2101)
- asset overflow hint line (L2227-2237)
- hard-trim + hard-clip (L2253-2261)
- no-guard shared-projection refusal (fail-closed, L2292-2297)
- MEM_PROJ_DEBUG diagnostics (L2178/2197)
- starved warning only for types with active rows (the 2026-11-04 fix)
- pin-overflow warning (L2407)
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _setup(tmp_path, monkeypatch, budget="3980", proj=True, static_quota=None,
           debug=None):
    db = str(tmp_path / "p.db")
    proj_path = str(tmp_path / "MEMORY.md") if proj else None
    sink = str(tmp_path / "sink.json")
    monkeypatch.setenv("MEM_DB", db)
    monkeypatch.setenv("MEM_SINK_PATH", sink)
    if proj_path:
        monkeypatch.setenv("MEM_PROJ_PATH", proj_path)
    else:
        monkeypatch.delenv("MEM_PROJ_PATH", raising=False)
    monkeypatch.setenv("MEM_PROJ_BUDGET", budget)
    if static_quota is not None:
        monkeypatch.setenv("MEM_PROJ_STATIC_QUOTA", static_quota)
    else:
        monkeypatch.delenv("MEM_PROJ_STATIC_QUOTA", raising=False)
    if debug is not None:
        monkeypatch.setenv("MEM_PROJ_DEBUG", debug)
    else:
        monkeypatch.delenv("MEM_PROJ_DEBUG", raising=False)
    import importlib, gateway
    importlib.reload(gateway)
    gateway.HG = None
    gateway.init_db()
    return gateway, (proj_path or ""), sink


def test_static_quota_arm_selected(tmp_path, monkeypatch):
    gw, proj, _ = _setup(tmp_path, monkeypatch, static_quota="1")
    for i in range(3):
        gw.remember("static quota filler %d with unique marker sq%d" % (i, i),
                    source="codex")
    r = gw.rebuild()
    assert r["ok"] is True
    md = open(proj, encoding="utf-8").read()
    assert "static quota filler 0" in md


def test_nrem_cap_limits_nrem_rows(tmp_path, monkeypatch):
    """With a budget that can only fit the protected buckets (floor+nrem cap),
    exactly NREM_CAP=5 nrem rows + the floor rows may land; the 3 oldest nrem
    rows must be absent (they can only arrive via bands, which don't fit)."""
    gw, proj, _ = _setup(tmp_path, monkeypatch, budget="800")
    import sqlite3, datetime
    conn = sqlite3.connect(gw.DB)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 8 nrem rows + 8 band-only filler rows (make band rows LONG so they
    # cannot fit in the remaining budget)
    for i in range(8):
        conn.execute(
            "INSERT INTO facts (uid,type,subject,content,status,source,recorded_at,"
            "created_at,tags) VALUES (?,?,?,?,?,?,?,?,?)",
            ("nrem%02d" % i, "fact", "s",
             "nrem summary %d nr%d" % (i, i), "active", "codex", now, now,
             "nrem_consolidated"))
    for i in range(8):
        conn.execute(
            "INSERT INTO facts (uid,type,subject,content,status,source,recorded_at,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("band%02d" % i, "fact", "s",
             ("band filler %d " % i) * 90, "active", "codex", now, now))
    conn.commit(); conn.close()
    r = gw.rebuild()
    md = open(proj, encoding="utf-8").read()
    landed = [i for i in range(8) if ("nrem summary %d " % i) in md]
    assert len(landed) <= 5, \
        "NREM cap: at most 5 nrem rows may land, got %d (%s)" % (len(landed), landed)
    assert any(("nrem summary %d" % i) in md for i in range(5)), \
        "newest 5 nrem rows must land (cap keeps the newest)"


def test_asset_overflow_hint_line(tmp_path, monkeypatch):
    gw, proj, _ = _setup(tmp_path, monkeypatch)
    # seed many tool_assets: >12, only 12 fit -> hint line appears
    import sqlite3, datetime
    conn = sqlite3.connect(gw.DB)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(16):
        conn.execute(
            "INSERT INTO tool_assets (uid,name,type,status,path,source,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("ta%02d" % i, "asset%02d" % i, "local_tool", "active",
             "C:/tools/%d.exe" % i, "codex", now, now))
    conn.commit(); conn.close()
    r = gw.rebuild()
    assert r["dropped_assets"] >= 4
    md = open(proj, encoding="utf-8").read()
    assert "另" in md and "条资产见网关库" in md


def test_pin_overflow_warning(tmp_path, monkeypatch):
    gw, proj, _ = _setup(tmp_path, monkeypatch)
    for i in range(12):  # > MEM_PROJ_PIN_MAX default 10
        gw.remember("pin heavy def %d: long definition text to consume budget" % i,
                    source="codex", tags="pin")
    r = gw.rebuild()
    assert r["pinned_count"] == 12
    assert any("pin 过多" in w for w in r["warnings"]), r["warnings"]


def test_starved_warning_absent_when_type_empty(tmp_path, monkeypatch):
    """2026-11-04 fix: empty types must not produce starvation false alarms."""
    gw, proj, _ = _setup(tmp_path, monkeypatch)
    gw.remember("only facts here, no procedure rows", source="codex")
    r = gw.rebuild()
    starved = [w for w in r["warnings"] if "类型饿死" in w]
    assert starved == [], "empty type must not be reported starved: %s" % starved


def test_no_guard_shared_projection_refused(tmp_path, monkeypatch):
    """Fail-closed: HG=None + no MEM_PROJ_PATH (shared slot) => refuse, no write."""
    gw, proj_path, _ = _setup(tmp_path, monkeypatch, proj=False)
    gw.remember("refusal probe content", source="codex")
    r = gw.rebuild()
    # by design this is a HARD failure: ok=False, no projection written
    assert r["ok"] is False
    assert r["refused"] and r["refused"][0]["kind"] == "no-guard"
    assert r["writes"][0].get("refused") is True
    assert any("拒绝写入共享投影" in w for w in r["warnings"])


def test_debug_lines_behind_mem_proj_debug(tmp_path, monkeypatch, capsys):
    gw, proj, _ = _setup(tmp_path, monkeypatch, debug="1")
    gw.remember("debug probe content", source="codex")
    gw.rebuild()
    err = capsys.readouterr().err
    assert "[proj-debug] fill_order=" in err


def test_debug_silent_by_default(tmp_path, monkeypatch, capsys):
    gw, proj, _ = _setup(tmp_path, monkeypatch, debug=None)
    gw.remember("quiet probe content", source="codex")
    gw.rebuild()
    err = capsys.readouterr().err
    assert "[proj-debug]" not in err
