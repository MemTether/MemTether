# -*- coding: utf-8 -*-
"""tests/test_conflicts_qvalue.py — a46: conflicts --accept/--discard bumps Q-Value

Regression for bug fix #2 (Q-Value 52.5% stuck at 0.5) and the
sqlite conn-lock fix (review UPDATE committed before bump).
"""
import os, sys, json, sqlite3, subprocess, tempfile, pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PY = sys.executable


def _run_cli(db_path, *args):
    env = dict(os.environ)
    env["MEM_DB"] = db_path
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [PY, "-X", "utf8", "-m", "memtether"] + list(args),
        capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=REPO, timeout=30)


@pytest.fixture()
def fresh_db(tmp_path):
    """Create a scratch db with two conflicting facts + a pending review."""
    import gateway
    db = str(tmp_path / "test.db")
    os.environ["MEM_DB"] = db
    # reload gateway so DB points at the scratch db
    import importlib
    importlib.reload(gateway)
    gateway.init_db()
    r1 = gateway.remember(content="alice prefers tea over coffee", type="fact", source="qa")
    r2 = gateway.remember(content="alice hates tea and drinks only coffee", type="fact", source="qb")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS conflict_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid_a TEXT, uid_b TEXT,
        verdict TEXT, note TEXT, by_agent TEXT, ts TEXT)""")
    conn.execute("INSERT INTO conflict_reviews (uid_a, uid_b, verdict, by_agent) VALUES (?,?, 'pending','qa')",
                 (r1["uid"], r2["uid"]))
    conn.commit()
    conn.close()
    yield db, r1["uid"], r2["uid"]
    os.environ["MEM_DB"] = ""


class TestConflictsQValue:
    def test_accept_bumps_keeper(self, fresh_db):
        db, uid_a, uid_b = fresh_db
        r = _run_cli(db, "conflicts", "--accept", "1", "--by", "tester")
        assert r.returncode == 0, r.stderr
        assert "q_value bumped: 1" in r.stdout, r.stdout
        assert "q_value bump failed" not in r.stderr, r.stderr
        conn = sqlite3.connect(db)
        # keeper = the fact with the later updated_at (r2/uid_b in this fixture)
        ua = conn.execute("SELECT updated_at FROM facts WHERE uid=?", (uid_a,)).fetchone()[0]
        ub = conn.execute("SELECT updated_at FROM facts WHERE uid=?", (uid_b,)).fetchone()[0]
        keeper = uid_a if ua >= ub else uid_b
        q = conn.execute("SELECT q_value FROM facts WHERE uid=?", (keeper,)).fetchone()[0]
        conn.close()
        assert q > 0.5, f"keeper q_value should rise above 0.5, got {q}"

    def test_discard_penalises_both(self, fresh_db):
        db, uid_a, uid_b = fresh_db
        r = _run_cli(db, "conflicts", "--discard", "1", "--by", "tester")
        assert r.returncode == 0, r.stderr
        assert "q_value bumped: 1" in r.stdout or "q_value bumped: 2" in r.stdout
        conn = sqlite3.connect(db)
        q_a = conn.execute("SELECT q_value FROM facts WHERE uid=?", (uid_a,)).fetchone()[0]
        q_b = conn.execute("SELECT q_value FROM facts WHERE uid=?", (uid_b,)).fetchone()[0]
        conn.close()
        assert q_a < 0.5 and q_b < 0.5, f"both should drop below 0.5, got {q_a}/{q_b}"

    def test_audit_log_entry(self, fresh_db):
        db, uid_a, uid_b = fresh_db
        _run_cli(db, "conflicts", "--accept", "1", "--by", "audit_tester")
        conn = sqlite3.connect(db)
        # keeper = fact with later updated_at (may tie -> SQL order between the
        # two is then decided by which row the UPDATE loop hit first, so accept
        # either uid being bumped — the invariant is: SOME fact got a qvalue op)
        rows = conn.execute(
            "SELECT target, op FROM audit_log WHERE op='qvalue' AND agent='audit_tester'").fetchall()
        conn.close()
        assert len(rows) >= 1
        assert any(r[0] in (uid_a, uid_b) for r in rows)

    def test_no_lock_error(self, fresh_db):
        """Regression: review UPDATE must be committed before bump (conn-lock fix)."""
        db, _, _ = fresh_db
        r = _run_cli(db, "conflicts", "--accept", "1", "--by", "lock_test")
        assert "database is locked" not in r.stderr, r.stderr
