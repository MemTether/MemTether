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
import json
import datetime
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

# ---------------------------------------------------------------------
# External anchor (2026-10-14, a81): file-system trust anchor
# ---------------------------------------------------------------------
# The in-DB anchor proves rows were not EDITED relative to the anchor,
# but an attacker who can rewrite the whole DB can also rewrite the
# audit_anchors table. The external anchor writes the head chain hash
# to a file OUTSIDE the DB (default ~/.memtether/external_anchors/),
# so a full-DB rewrite no longer matches the external checkpoint.

EXTERNAL_ANCHOR_DIR = os.path.join(
    os.environ.get('MEMTETHER_HOME', os.path.expanduser('~/.memtether')),
    'external_anchors')


def _external_path(ts=None, out_dir=None):
    d = out_dir or EXTERNAL_ANCHOR_DIR
    os.makedirs(d, exist_ok=True)
    ts = ts or datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    return os.path.join(d, 'anchor-%s.json' % ts)


def anchor_external(conn, out_dir=None):
    """Force-anchor, then write the head anchor hash to an external file.

    Returns (path, anchor_dict) or (None, reason) if there is nothing to anchor.
    """
    maybe_anchor(conn, force=True)
    row = conn.execute(
        "SELECT id, last_log_id, chain_hash, entries_hashed, ts "
        "FROM audit_anchors ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None, "no anchors exist — nothing to export"
    aid, last_log_id, chain_hash, n_rows, ts = row
    payload = {
        "anchor_id": aid,
        "last_log_id": last_log_id,
        "chain_hash": chain_hash,
        "entries_hashed": n_rows,
        "anchored_at": ts,
        "exported_at": datetime.datetime.now().isoformat(timespec='seconds'),
    }
    safe_ts = str(ts).replace(':', '').replace(' ', '-').replace('/', '')
    p = _external_path(ts=safe_ts, out_dir=out_dir)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p, payload


def verify_external(conn, out_dir=None):
    """verify_chain + compare head anchor against the newest external file.

    Returns (ok, detail). If no external file exists, falls back to
    verify_chain with a note (first-run state).
    """
    ok, detail = verify_chain(conn)
    if not ok:
        return False, detail
    d = out_dir or EXTERNAL_ANCHOR_DIR
    if not os.path.isdir(d):
        return True, {**detail, 'external': 'no external anchor file yet (run anchor-external)'}
    files = sorted(f for f in os.listdir(d) if f.startswith('anchor-') and f.endswith('.json'))
    if not files:
        return True, {**detail, 'external': 'no external anchor file yet (run anchor-external)'}
    newest = os.path.join(d, files[-1])
    try:
        ext = json.load(open(newest, encoding='utf-8'))
    except Exception as e:
        return False, {'reason': 'external anchor file unreadable: %s' % e, 'file': newest}
    head = conn.execute(
        "SELECT chain_hash FROM audit_anchors ORDER BY id DESC LIMIT 1").fetchone()
    head_hash = head[0] if head else None
    if ext.get('chain_hash') != head_hash:
        return False, {
            'reason': 'external anchor mismatch — the DB head anchor does not '
                      'match the latest external checkpoint (full-DB rewrite?)',
            'external_file': newest,
            'external_hash': ext.get('chain_hash'),
            'db_head_hash': head_hash}
    return True, {**detail, 'external': 'PASS vs %s' % os.path.basename(newest)}
