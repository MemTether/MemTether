"""Tests for multi-tenant isolation (P0-2, 2026-10-05)."""
import os, sys, io, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ.setdefault('MEM_SCOPE', 'all')
os.environ['PYTHONIOENCODING'] = 'utf-8'

def _run_cli(args, tenant=None, db=None):
    """Directly import gateway/memsearch in-process (no subprocess, CI-safe)."""
    if db: os.environ['MEM_DB'] = db
    if tenant: os.environ['MEM_TENANT_ID'] = tenant
    else: os.environ.pop('MEM_TENANT_ID', None)
    import importlib
    import memtether, gateway, memsearch
    importlib.reload(memtether)
    importlib.reload(gateway)
    importlib.reload(memsearch)
    # call main
    return memtether.main(args)

def test_tenant_write_and_isolation(tmp_path):
    """Cross-tenant search must return 0 results."""
    db = str(tmp_path / 't.db')
    # tenant A writes
    _run_cli(['remember', 'alpha secret data', '--source', 'codex', '--tenant', 'tenantA'], db=db)
    # tenant B searches — must NOT see
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try: _run_cli(['search', 'alpha secret', '--limit', '3', '--tenant', 'tenantB'], db=db)
        except SystemExit: pass
    out = buf.getvalue()
    assert 'alpha' not in out, f'CROSS-TENANT LEAK: {out}'
    # tenant A searches — MUST see
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        try: _run_cli(['search', 'alpha secret', '--limit', '3', '--tenant', 'tenantA'], db=db)
        except SystemExit: pass
    assert 'alpha' in buf2.getvalue(), f'tenantA should see own data'

def test_tenant_default_shared(tmp_path):
    db = str(tmp_path / 't.db')
    _run_cli(['remember', 'default tenant item', '--source', 'codex'], db=db)
    conn = sqlite3.connect(db)
    conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='facts'").fetchone()
    row = conn.execute("SELECT tenant_id FROM facts WHERE content LIKE '%default tenant%'").fetchone()
    conn.close()
    assert row and row[0] == 'default', f'tenant_id should be default, got {row}'

def test_tenant_env_override(tmp_path):
    db = str(tmp_path / 't.db')
    _run_cli(['remember', 'env tenant content', '--source', 'codex'], tenant='envtenant', db=db)
    conn = sqlite3.connect(db)
    conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='facts'").fetchone()
    row = conn.execute("SELECT tenant_id FROM facts WHERE content LIKE '%env tenant%'").fetchone()
    conn.close()
    assert row and row[0] == 'envtenant', f'tenant_id should be envtenant, got {row}'
