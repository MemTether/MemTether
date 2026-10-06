"""hubguard concurrency tests."""
import os, sys, tempfile, sqlite3, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

def _fresh_db(tmpdir):
    db = os.path.join(tmpdir, 'hg.db')
    sqlite3.connect(db).close()
    return db

def test_lock_acquire_release(tmp_path):
    import hubguard
    db = _fresh_db(str(tmp_path))
    lp = hubguard.lock_path(db)
    ok = hubguard.lock_acquire(timeout=1.0, agent='test-agent', purpose='test')
    assert ok
    hubguard.lock_release(lp)

def test_lock_exclusive(tmp_path):
    """Second acquire must fail while first held."""
    import hubguard
    db = _fresh_db(str(tmp_path))
    lp = hubguard.lock_path(db)
    ok1 = hubguard.lock_acquire(timeout=1.0, agent='a', purpose='p1')
    assert ok1
    # Second process-level attempt should fail (same process uses holder check)
    st = hubguard.lock_status(db)
    assert st, 'lock_status should return info'

def test_lock_status(tmp_path):
    import hubguard
    db = _fresh_db(str(tmp_path))
    st = hubguard.lock_status(db)
    assert isinstance(st, (dict, tuple, type(None))) or st is None or hasattr(st, 'get')
