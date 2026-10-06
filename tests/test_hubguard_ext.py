"""hubguard.py extended unit tests (P0-C coverage)."""
import os, sys, io, tempfile, sqlite3, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

def test_atomic_write(tmp_path):
    import hubguard
    target = tmp_path / 'aw.txt'
    hubguard.atomic_write(str(target), 'hello world')
    assert target.read_text(encoding='utf-8') == 'hello world'

def test_atomic_write_overwrite(tmp_path):
    import hubguard
    target = tmp_path / 'aw2.txt'
    hubguard.atomic_write(str(target), 'first')
    hubguard.atomic_write(str(target), 'second')
    assert target.read_text(encoding='utf-8') == 'second'

def test_safe_append(tmp_path):
    import hubguard
    target = tmp_path / 'sa.txt'
    hubguard.safe_append(str(target), 'line1\n')
    hubguard.safe_append(str(target), 'line2\n')
    assert 'line1' in target.read_text(encoding='utf-8')
    assert 'line2' in target.read_text(encoding='utf-8')

def test_format_fact_line_roundtrip():
    import hubguard
    line = hubguard.format_fact_line('2026-10-05', 'fact', 'codex', 'test content here')
    assert line is not None
    parsed = hubguard.parse_fact_line(line)
    assert parsed is not None

def test_pid_alive_current():
    import hubguard, os
    assert hubguard.pid_alive(os.getpid())
def test_pid_alive_bogus():
    import hubguard
    assert not hubguard.pid_alive(999999)

def test_db_fingerprint(tmp_path):
    import hubguard, os
    db = tmp_path / 'fp.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit(); conn.close()
    os.environ['MEM_DB'] = str(db)
    fp1 = hubguard.db_fingerprint(db=str(db))
    fp2 = hubguard.db_fingerprint(db=str(db))
    assert fp1 == fp2  # same db -> same fingerprint

def test_snapshot(tmp_path):
    import hubguard
    db = tmp_path / 'sn.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit(); conn.close()
    snap = hubguard.snapshot(str(db))
    assert snap is not None

def test_status(tmp_path):
    import hubguard, os
    db = tmp_path / 'st.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit(); conn.close()
    os.environ['MEM_DB'] = str(db)
    s = hubguard.status(db=str(db))
    assert s is not None

def test_lock_full_cycle(tmp_path):
    import hubguard
    db = tmp_path / 'lk.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t (x INTEGER)')
    conn.commit(); conn.close()
    lp = hubguard.lock_path(str(db))
    ok = hubguard.lock_acquire(timeout=1.0, agent='test', purpose='unittest')
    assert ok
    hubguard.lock_release(lp)
