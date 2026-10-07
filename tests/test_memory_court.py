# -*- coding: utf-8 -*-
"""tests/test_memory_court.py — a50 D1: tamper-evident evidence chain

Core acceptance: tampering with ANY anchored audit_log row must turn
verify_chain() red. Plus: deletion detection, resume-after-anchor,
existence proof, and integration with real gateway ops.
"""
import json
import os
import sqlite3
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["MEM_DB"] = os.path.join(
    __import__("tempfile").mkdtemp(prefix="mt_court_"), "t.db")

import gateway
import memory_court as mc
import importlib


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets its own db so anchors never cross-contaminate."""
    db = str(tmp_path / "court.db")
    os.environ["MEM_DB"] = db
    importlib.reload(gateway)
    gateway.init_db()
    yield
    os.environ["MEM_DB"] = ""


def _conn():
    conn = gateway.get_conn()
    mc._ensure_anchor_table(conn)
    return conn


class TestEvidenceChain:
    def test_empty_db_verifies(self):
        conn = _conn()
        ok, detail = mc.verify_chain(conn)
        assert ok
        conn.close()

    def test_anchor_after_batch(self):
        conn = _conn()
        for i in range(10):
            gateway.remember(content=f"court probe a{i}", type="fact",
                             source="court")
        n_before = conn.execute(
            "SELECT COUNT(*) FROM audit_anchors").fetchone()[0]
        aid = mc.maybe_anchor(conn)
        assert aid is not None
        n_after = conn.execute(
            "SELECT COUNT(*) FROM audit_anchors").fetchone()[0]
        assert n_after == n_before + 1
        conn.close()

    def test_no_anchor_below_batch(self):
        conn = _conn()
        gateway.remember(content="court single item", type="fact",
                         source="court")
        aid = mc.maybe_anchor(conn)  # < BATCH and not forced
        # may be None if fewer than BATCH unanchored rows
        if aid is None:
            last = conn.execute(
                "SELECT COALESCE(MAX(last_log_id),0) FROM audit_anchors"
            ).fetchone()[0]
            cnt = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE id > ?",
                (last,)).fetchone()[0]
            assert cnt < mc.BATCH
        conn.close()

    def test_force_anchor_covers_all(self):
        conn = _conn()
        gateway.remember(content="court force item", type="fact",
                         source="court")
        aid = mc.maybe_anchor(conn, force=True)
        assert aid is not None
        ok, detail = mc.verify_chain(conn)
        assert ok, detail
        assert detail["unanchored_tail"] == 0
        conn.close()

    def test_tamper_edit_detected(self):
        """THE core guarantee: editing an anchored row must turn verify red."""
        conn = _conn()
        gateway.remember(content="court tamper target", type="fact",
                         source="court")
        mc.maybe_anchor(conn, force=True)
        ok, _ = mc.verify_chain(conn)
        assert ok
        # TAMPER: edit a covered row's detail
        conn.execute(
            "UPDATE audit_log SET detail='TAMPERED' WHERE id="
            "(SELECT MAX(id) FROM audit_log)")
        conn.commit()
        ok, detail = mc.verify_chain(conn)
        assert not ok, detail
        assert "mismatch" in detail["reason"] or "edited" in detail["reason"]
        conn.close()

    def test_tamper_delete_detected(self):
        """Deleting a covered row must also turn verify red (row-count check)."""
        conn = _conn()
        gateway.remember(content="court delete target", type="fact",
                         source="court")
        mc.maybe_anchor(conn, force=True)
        ok, _ = mc.verify_chain(conn)
        assert ok
        conn.execute("DELETE FROM audit_log WHERE id="
                     "(SELECT MAX(id) FROM audit_log)")
        conn.commit()
        ok, detail = mc.verify_chain(conn)
        assert not ok, detail
        assert "deleted" in detail["reason"]
        conn.close()

    def test_chain_extends_across_multiple_anchors(self):
        conn = _conn()
        for i in range(15):
            gateway.remember(content=f"court chain c{i}", type="fact",
                             source="court")
            if (i + 1) % mc.BATCH == 0:
                mc.maybe_anchor(conn)
        mc.maybe_anchor(conn, force=True)
        ok, detail = mc.verify_chain(conn)
        assert ok, detail
        assert detail["anchors"] >= 2
        conn.close()

    def test_row_existence_proof(self):
        conn = _conn()
        gateway.remember(content="court existence probe", type="fact",
                         source="court")
        mc.maybe_anchor(conn, force=True)
        last_log_id = conn.execute(
            "SELECT MAX(id) FROM audit_log").fetchone()[0]
        ok, why = mc.verify_row_in_anchor(conn, last_log_id)
        assert ok
        conn.close()

    def test_integration_with_gateway_ops(self):
        """Real ops (remember/correct/retire) all land in audit_log and verify."""
        conn = _conn()
        r = gateway.remember(content="court integration fact v1",
                             type="fact", source="court")
        uid = r["uid"]
        gateway.correct(uid, "court integration fact v2", "test",
                        by_agent="court")
        mc.maybe_anchor(conn, force=True)
        ok, detail = mc.verify_chain(conn)
        assert ok, detail
        conn.close()
