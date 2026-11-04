r"""nrem_sleep.py — shipped, dependency-light sleep-time consolidation.

Why this file exists (2026-11-04 competitor audit)
-------------------------------------------------
`memtether sleep` was advertised in README.md but its runner lived only in a
sibling development checkout (`memory_hub/nrem_consolidate.py`) and was not in
the wheel. A pip-installed user got `nrem_consolidate.py not found`.

tigerless-labs/agent-memory runs a proposal-only Manage pass with stable
proposal IDs, per-kind caps, and a Dream report. We keep that *safety shape*
without adopting their Markdown truth source: exact-duplicate clusters produce
bounded proposals; default dry-run writes nothing; `--apply` requires an
explicit flag and mutations go through the governed `gateway.retire` path.
SQLite remains the single source of truth; the SHA-256 audit chain stays
intact. No LLM, no embedding model, no semantic guesswork.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys

MAX_PROPOSALS_PER_RUN = 20


def _db_path():
    """Resolve at call time, not import time (tests re-point MEM_DB)."""
    return os.environ.get("MEM_DB") or os.path.join(
        os.path.expanduser("~"), ".memtether", "memory.db")


def _normalise(text):
    """Exact-duplicate key: whitespace-normalised content, no fuzzy matching."""
    return " ".join((text or "").split())


def _rows(conn, hours):
    cutoff = (dt.datetime.now() - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        return conn.execute(
            "SELECT uid, content, source, type, recorded_at FROM facts "
            "WHERE status='active' AND COALESCE(recorded_at, created_at, '') >= ? "
            "ORDER BY recorded_at DESC LIMIT 1000", (cutoff,)).fetchall()
    except sqlite3.Error:
        return []


def build_proposals(hours=24, cap=MAX_PROPOSALS_PER_RUN):
    """Group byte-identical active facts into bounded, human-reviewable proposals."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = _rows(conn, hours)
    finally:
        conn.close()
    groups = collections.defaultdict(list)
    for r in rows:
        key = _normalise(r["content"])
        if key:
            groups[key].append(dict(r))
    proposals = []
    for content, members in groups.items():
        if len(members) < 2:
            continue
        ids = sorted(m["uid"] for m in members)
        # Same content + same members -> same id (deterministic, replay-safe).
        pid = hashlib.sha256((content + "|" + ",".join(ids)).encode("utf-8")).hexdigest()[:12]
        proposals.append({
            "proposal_id": pid,
            "kind": "retire_exact_duplicates",
            "targets": ids,
            "canonical_uid": ids[0],
            "retire_uids": ids[1:],
            "abstract": content[:160],
            "source_count": len({m.get("source") or "" for m in members}),
            "size": len(members),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        })
    proposals.sort(key=lambda p: p["proposal_id"])
    return {"ok": True, "hours": hours, "scanned": len(rows),
            "proposals": proposals[:cap], "withheld": max(0, len(proposals) - cap),
            "cap": cap}


def apply_proposals(report, by_agent="nrem"):
    """Apply only via governed writes; never issue a raw DELETE/UPDATE here."""
    if not report.get("proposals"):
        return {"ok": True, "applied": 0, "skipped": "no_proposals"}
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gateway
    applied, failed = 0, []
    for p in report["proposals"]:
        for uid in p["retire_uids"]:
            try:
                r = gateway.retire(uid, "nrem exact duplicate of %s (proposal %s)" % (
                    p["canonical_uid"], p["proposal_id"]), by_agent=by_agent)
                if r.get("ok"):
                    applied += 1
                else:
                    failed.append({"uid": uid, "result": r})
            except Exception as exc:
                failed.append({"uid": uid, "error": str(exc)[:120]})
    return {"ok": not failed, "applied": applied, "failed": failed,
            "proposal_ids": [p["proposal_id"] for p in report["proposals"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="MemTether sleep-time consolidation (proposal-only by default)")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--apply", action="store_true", help="Explicitly apply proposals via governed writes")
    ap.add_argument("--json", action="store_true", help="Compact machine-readable output")
    ap.add_argument("--cap", type=int, default=MAX_PROPOSALS_PER_RUN)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = build_proposals(a.hours, a.cap)
    report["dry_run"] = not a.apply
    if a.apply:
        report["apply_result"] = apply_proposals(report)
    print(json.dumps(report, ensure_ascii=False, indent=None if a.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
