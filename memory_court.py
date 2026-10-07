# -*- coding: utf-8 -*-
"""memory_court.py — Memory Court: tamper-evident evidence chain for audit_log

a50 D1. Appends periodic hash anchors over audit_log entries so that any
edit/removal of a logged decision (remember/correct/retire/conflict
verdict/qvalue) after its anchor was taken is detectable via
`verify_chain()`.

Design notes:
- Full-chain recomputation instead of a Merkle tree: audit volumes in the
  local-first single-SQLite scenario are small (<100K entries), where a
  single sha256 pass is faster and simpler than tree proofs, and verifiers
  need no trusted third-party anchor service.
- Anchors are themselves stored in the same DB (audit_anchors table). This
  proves records were not EDITED relative to the anchor point; it does not
  prove rows were not DELETED after the anchor (same limit as halo-record
  LIMITS.md §1 — an external checkpoint would be needed for that, which is
  out of scope for the local-first product).
"""
import hashlib
import os
import sqlite3
import time

BATCH = 10  # anchor every N audit_log entries


def _row_hash(row):
    """sha256 over the canonical text of one audit_log row."""
    # row: (id, op, target, agent, detail, ts)
    canonical = "|".join(str(x) for x in row)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_anchor_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_anchors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        last_log_id INTEGER,
        chain_hash TEXT,
        entries_hashed INTEGER,
        ts TEXT
    )""")


def maybe_anchor(conn, force=False):
    """If >= BATCH unanchored audit rows exist (or force=True), append an anchor.

    Returns the anchor id or None.
    """
    _ensure_anchor_table(conn)
    last = conn.execute(
        "SELECT COALESCE(MAX(last_log_id), 0) FROM audit_anchors").fetchone()[0]
    rows = conn.execute(
        "SELECT id, op, target, agent, detail, ts FROM audit_log "
        "WHERE id > ? ORDER BY id", (last,)).fetchall()
    if not rows or (len(rows) < BATCH and not force):
        return None
    prev = conn.execute(
        "SELECT chain_hash FROM audit_anchors ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev[0] if prev else "GENESIS"
    h = prev_hash
    for r in rows:
        h = hashlib.sha256((h + _row_hash(r)).encode("utf-8")).hexdigest()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO audit_anchors (last_log_id, chain_hash, entries_hashed, ts) "
        "VALUES (?,?,?,?)", (rows[-1][0], h, len(rows), ts))
    conn.commit()
    return cur.lastrowid


def verify_chain(conn):
    """Recompute the full chain; return (ok, detail_dict).

    Any edit to an anchored audit_log row breaks recomputation of the
    anchor that covered it. Any DELETED row shortens the chain (detected
    as id-gap mismatch against the anchor's last_log_id).
    """
    _ensure_anchor_table(conn)
    anchors = conn.execute(
        "SELECT id, last_log_id, chain_hash, entries_hashed, ts "
        "FROM audit_anchors ORDER BY id").fetchall()
    if not anchors:
        return True, {"anchors": 0, "note": "no anchors yet — nothing to verify"}

    prev_hash = "GENESIS"
    prev_last = 0
    checked = 0
    for aid, last_log_id, stored_hash, n_rows, ts in anchors:
        rows = conn.execute(
            "SELECT id, op, target, agent, detail, ts FROM audit_log "
            "WHERE id > ? AND id <= ? ORDER BY id",
            (prev_last, last_log_id)).fetchall()
        # deletion detection: row count must match the anchor's claim
        if len(rows) != n_rows:
            return False, {
                "anchor_id": aid, "expected_rows": n_rows,
                "found_rows": len(rows),
                "reason": "entries deleted after anchor was taken"}
        h = prev_hash
        for r in rows:
            h = hashlib.sha256((h + _row_hash(r)).encode("utf-8")).hexdigest()
        if h != stored_hash:
            return False, {
                "anchor_id": aid,
                "reason": "chain hash mismatch — audit rows edited after anchoring",
                "expected": stored_hash, "recomputed": h}
        prev_hash = h
        prev_last = last_log_id
        checked += n_rows

    # unanchored tail rows (not yet covered) are informational
    tail = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE id > ?", (prev_last,)).fetchone()[0]
    return True, {"anchors": len(anchors), "entries_covered": checked,
                   "unanchored_tail": tail}


def verify_row_in_anchor(conn, uid_or_log_id):
    """Prove one audit_log id is covered by the latest anchor (existence proof)."""
    _ensure_anchor_table(conn)
    last = conn.execute(
        "SELECT COALESCE(MAX(last_log_id), 0) FROM audit_anchors").fetchone()[0]
    row = conn.execute(
        "SELECT id FROM audit_log WHERE id = ?", (uid_or_log_id,)).fetchone()
    if row is None:
        return False, "no such audit_log entry"
    return row[0] <= last, f"covered by anchor (last_log_id={last})" if row[0] <= last else "not yet anchored"
