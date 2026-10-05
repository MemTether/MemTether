"""Tests for multi-tenant isolation (P0-2, 2026-10-05)."""
import os, sys, io, tempfile, subprocess, sqlite3
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

PY = sys.executable
CLI = r'E:\RUANJIAN\memtether\memtether.py'
CWD = r'E:\RUANJIAN\memtether'

def run(args, tenant=None, db=None):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if tenant: env['MEM_TENANT_ID'] = tenant
    if db: env['MEM_DB'] = db
    return subprocess.run([PY, CLI]+args, capture_output=True, env=env, cwd=CWD, encoding='utf-8', errors='replace', timeout=30)

def test_tenant_write_and_isolation():
    """Cross-tenant search must return 0 results."""
    db = tempfile.mktemp(suffix='.db')
    try:
        # tenant A writes
        r = run(['remember', 'alpha secret data', '--source', 'codex', '--tenant', 'tenantA'], db=db)
        assert r.returncode == 0, f'remember A failed: {r.stderr}'
        # tenant B searches — must NOT see
        r2 = run(['search', 'alpha secret', '--limit', '3', '--tenant', 'tenantB'], db=db)
        assert r2.returncode == 0
        assert 'alpha' not in (r2.stdout or ''), f'CROSS-TENANT LEAK: {r2.stdout}'
        # tenant A searches — MUST see
        r3 = run(['search', 'alpha secret', '--limit', '3', '--tenant', 'tenantA'], db=db)
        assert 'alpha' in (r3.stdout or ''), f'tenantA should see own data'
    finally:
        if os.path.exists(db): os.remove(db)

def test_tenant_default_shared():
    """Default tenant (no --tenant) uses 'default'."""
    db = tempfile.mktemp(suffix='.db')
    try:
        r = run(['remember', 'default tenant item', '--source', 'codex'], db=db)
        assert r.returncode == 0
        r2 = run(['search', 'default tenant', '--limit', '3'], db=db)
        assert 'default tenant item' in (r2.stdout or ''), f'default tenant should see own'
        # check DB column
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT tenant_id FROM facts WHERE content LIKE '%default tenant%'").fetchone()
        assert row and row[0] == 'default', f'tenant_id should be default, got {row}'
        conn.close()
    finally:
        if os.path.exists(db): os.remove(db)

def test_tenant_env_override():
    """MEM_TENANT_ID env takes precedence when --tenant absent."""
    db = tempfile.mktemp(suffix='.db')
    try:
        r = run(['remember', 'env tenant content', '--source', 'codex'], tenant='envtenant', db=db)
        assert r.returncode == 0
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT tenant_id FROM facts WHERE content LIKE '%env tenant%'").fetchone()
        assert row and row[0] == 'envtenant', f'tenant_id should be envtenant, got {row}'
        conn.close()
    finally:
        if os.path.exists(db): os.remove(db)
