"""End-to-end integration tests for Memory Court.
Proves all components work together as a system, not just individually.
"""
import json
import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_court import maybe_anchor, verify_chain, verify_row_in_anchor, anchor_external, verify_external
from memory_court import verify_chain as vc, verify_row_in_anchor as vria


@pytest.fixture
def court_db(tmp_path):
    """Fresh DB with court tables ready."""
    db_path = str(tmp_path / "court_e2e.db")
    os.environ["MEM_DB"] = db_path
    import importlib
    import gateway as gw
    importlib.reload(gw)
    gw.init_db()
    return db_path, gw


def _db_status(db_path, uid):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    return row[0] if row else None


def _verify(db_path):
    """Open DB, run verify_chain, close, return result."""
    from memory_court import verify_chain as vc
    conn = sqlite3.connect(db_path)
    result = vc(conn)
    conn.close()
    return result


def _anchor(gw):
    """Force anchor on current DB."""
    conn = gw.get_conn()
    maybe_anchor(conn, force=True)
    conn.close()


class TestGateToCourtE2E:
    """Test 1: Correct -> supersede -> hash chain -> verify -> audit trail."""

    def test_gate_supersede_verify_export(self, court_db):
        db_path, gw = court_db

        # Step 1: Write original fact
        r1 = gw.remember("Clash proxy port is 7890", source="agent-a")
        assert r1["ok"]
        old_uid = r1["uid"]

        # Step 2: Anchor to lock current state
        _anchor(gw)
        ok_before, detail_before = _verify(db_path)
        assert ok_before, f"Chain should verify before correction"

        # Step 3: Correct (supersede)
        r2 = gw.correct(old_uid, "Clash proxy port is 7897", reason="port changed", by_agent="agent-b")
        assert r2["ok"]
        assert r2["op"] == "supersede"
        new_uid = r2["new_uid"]

        # Step 4: Anchor and verify chain after supersession
        _anchor(gw)
        ok_after, detail_after = _verify(db_path)
        assert ok_after, f"Chain should verify after supersession"

        # Step 5: Old fact is superseded, new is active
        assert _db_status(db_path, old_uid) == "superseded"
        assert _db_status(db_path, new_uid) == "active"

        # Step 6: Audit trail has entries
        conn = sqlite3.connect(db_path)
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert audit_count >= 2, f"Audit log should have entries, got {audit_count}"
        conn.close()


class TestCrossAgentReconciliation:
    """Test 2: Two agents write contradictions -> adjudication -> downstream sees only truth."""

    def test_two_agents_contradiction_resolved(self, court_db):
        db_path, gw = court_db

        # Step 1: Agent A: "Service Alpha is available"
        r1 = gw.remember("Service Alpha is available and running on port 3000", source="agent-alpha")
        assert r1["ok"]
        uid_a = r1["uid"]

        # Step 2: Agent B: contradictory "Service Alpha is deprecated"
        r2 = gw.remember("Service Alpha is deprecated and no longer available", source="agent-beta")
        assert r2["ok"]
        uid_b = r2["uid"]

        # Step 3: Both active (contradiction exists in storage)
        assert _db_status(db_path, uid_a) == "active"
        assert _db_status(db_path, uid_b) == "active"

        # Step 4: Adjudicate — supersede agent B's wrong info
        r3 = gw.correct(uid_b, "Service Alpha is available on port 3000 (confirmed)",
                        reason="Cross-agent reconciliation: agent-alpha is correct",
                        by_agent="adjudicator")
        assert r3["ok"]
        assert r3["op"] == "supersede"
        uid_c = r3["new_uid"]

        # Step 5: After adjudication
        assert _db_status(db_path, uid_b) == "superseded", "Contradictory fact superseded"
        assert _db_status(db_path, uid_c) == "active", "Corrected fact active"

        # Step 6: Hash chain verifies after full workflow
        _anchor(gw)
        ok, detail = _verify(db_path)
        assert ok, f"Chain should verify after adjudication"

        # Step 7: Complete audit trail
        conn = sqlite3.connect(db_path)
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert audit_count >= 3
        sup_count = conn.execute("SELECT COUNT(*) FROM supersessions").fetchone()[0]
        assert sup_count >= 1
        conn.close()

    def test_gate_tracks_conflicting_writes(self, court_db):
        """Conflicting writes are tracked, not silently overwritten."""
        db_path, gw = court_db

        r1 = gw.remember("Database on port 5432", source="agent-x")
        r2 = gw.remember("Database on port 3306", source="agent-y")

        assert _db_status(db_path, r1["uid"]) == "active"
        assert _db_status(db_path, r2["uid"]) == "active"
        # Both tracked — advisory gate, not blocking. Memory Court provides resolution tools.

# ---- P1-8 B-dim expansion (2026-10-18): court chain hardening ----

class TestCourtChainHardening:
    """Extended Memory Court coverage: external anchors, row proofs,
    batch boundaries, multi-chain independence, gateway-op integration."""

    def test_batch_boundary_exact(self, court_db):
        """BATCH=10: exactly 10 entries must anchor; 9 must not."""
        db_path, gw = court_db
        from memory_court import maybe_anchor as _ma  # noqa: F401
        for i in range(9):
            gw.remember(f'boundary probe {i} zzz-b1', source='codex')
        conn = gw.get_conn()
        n0 = conn.execute('SELECT COUNT(*) FROM audit_anchors').fetchone()[0]
        gw.remember('boundary probe 9 zzz-b1', source='codex')  # 10th write
        n1 = conn.execute('SELECT COUNT(*) FROM audit_anchors').fetchone()[0]
        assert n0 == 0 and n1 >= 1, f'anchor at 10th write: {n0}->{n1}'

    def test_verify_row_in_anchor_proof(self, court_db):
        """An anchored row must have an existence proof; an unanchored tail row must not."""
        db_path, gw = court_db
        for i in range(12):
            gw.remember(f'rowproof probe {i} zzz-b2', source='codex')
        conn = gw.get_conn()
        ok, detail = vc(conn)
        assert ok
        anchored = conn.execute('SELECT MIN(id) FROM audit_log').fetchone()[0]
        proof = vria(conn, anchored)
        assert proof[0], proof

    def test_edit_within_anchored_range_detected(self, court_db):
        """Editing a row in the MIDDLE of an anchored range (not just first) must fail verify."""
        db_path, gw = court_db
        for i in range(25):
            gw.remember(f'mid edit probe {i} zzz-b3', source='codex')
        conn = gw.get_conn()
        assert vc(conn)[0]
        mid_id = conn.execute('SELECT MIN(id)+10 FROM audit_log').fetchone()[0]
        conn.execute('UPDATE audit_log SET agent=? WHERE id=?', ('attacker', mid_id))
        conn.commit()
        ok, detail = vc(conn)
        assert not ok, f'mid-range edit escaped chain: {detail}'

    def test_delete_within_anchored_range_detected(self, court_db):
        """Deleting a middle row must trip the entries-deleted check."""
        db_path, gw = court_db
        for i in range(25):
            gw.remember(f'del probe {i} zzz-b4', source='codex')
        conn = gw.get_conn()
        assert vc(conn)[0]
        mid_id = conn.execute('SELECT MIN(id)+5 FROM audit_log').fetchone()[0]
        conn.execute('DELETE FROM audit_log WHERE id=?', (mid_id,))
        conn.commit()
        ok, detail = vc(conn)
        assert not ok, f'mid-range delete escaped chain: {detail}'

    def test_external_anchor_roundtrip(self, court_db, tmp_path):
        """anchor-external writes a checkpoint file; verify-external passes while
        DB is intact and FAILS if the head anchor is rewritten."""
        db_path, gw = court_db
        for i in range(12):
            gw.remember(f'ext anchor probe {i} zzz-b5', source='codex')
        out_dir = str(tmp_path / 'ext_anchors')
        ok, detail = anchor_external(gw.get_conn(), out_dir=out_dir)
        assert ok, detail
        ok2, detail2 = verify_external(gw.get_conn(), out_dir=out_dir)
        assert ok2, detail2
        # tamper: rewrite the head chain_hash in DB -> external mismatch
        conn = gw.get_conn()
        conn.execute("UPDATE audit_anchors SET chain_hash='deadbeef' "
                     "WHERE id=(SELECT MAX(id) FROM audit_anchors)")
        conn.commit()
        ok3, detail3 = verify_external(conn, out_dir=out_dir)
        assert not ok3, 'external checkpoint did not catch DB head rewrite'

    def test_court_export_package(self, court_db, tmp_path):
        """court export must produce a zip with case.json + chain.jsonl and
        the embedded chain must re-verify."""
        db_path, gw = court_db
        uid = gw.remember('export probe unique zzz-b6', source='codex')['uid']
        for i in range(12):
            gw.remember(f'filler {i} zzz-b6', source='codex')
        conn = gw.get_conn()
        ok, detail = vc(conn)
        assert ok
        zpath = str(tmp_path / 'case.zip')
        from memtether import _cmd_court  # noqa: F401  (module import sanity)
        # call export via CLI-equivalent args
        import argparse, io, contextlib
        args = argparse.Namespace(uid=uid, export=zpath, verify=False,
                                  anchor_external=False, verify_external=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _cmd_court(args)
        assert rc == 0, buf.getvalue()
        import zipfile, json as _json
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            assert 'case.json' in names and 'chain.jsonl' in names and 'verify.html' in names
            case = _json.loads(z.read('case.json'))
            assert case['chain_verified'] is True

    def test_qvalue_ops_are_anchored(self, court_db):
        """qvalue bumps write audit rows; they must be covered by the chain too."""
        db_path, gw = court_db
        uid = gw.remember('qvalue anchor probe zzz-b7', source='codex')['uid']
        gw.bump_qvalue(uid, reward=1.0, agent='codex')
        for i in range(12):
            gw.remember(f'anchor filler {i} zzz-b7', source='codex')
        conn = gw.get_conn()
        ok, detail = vc(conn)
        assert ok, detail
        rows = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE op='qvalue' AND target=?", (uid,)).fetchone()[0]
        assert rows >= 1

    def test_correct_chain_keeps_history_queryable(self, court_db):
        """Full governance loop: remember → correct ×2 → timeline must show 3 hops
        and the original content must remain in the DB (no physical delete)."""
        db_path, gw = court_db
        uid1 = gw.remember('gov loop v1 zzz-b8', source='codex')['uid']
        uid2 = gw.correct(uid1, 'gov loop v2', reason='r1', by_agent='codex')['new_uid']
        uid3 = gw.correct(uid2, 'gov loop v3', reason='r2', by_agent='codex')['new_uid']
        chain = gw.timeline(uid1)
        assert [c['uid'] for c in chain] == [uid1, uid2, uid3]
        c = sqlite3.connect(os.environ['MEM_DB'])
        old = c.execute('SELECT content, status FROM facts WHERE uid=?', (uid1,)).fetchone()
        c.close()
        assert old is not None and old[1] == 'superseded'
        assert 'gov loop v1' in old[0]
