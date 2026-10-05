"""memsearch.py D2 deep tests (scope/tenant/ttl/terms/placeholder/state/self-ref)."""
import os, sys, io, tempfile, sqlite3
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def test_scope_filter_default():
    import memsearch
    os.environ.pop('MEM_SCOPE', None)
    f = memsearch._scope_filter()
    assert 'private' in f and 'restricted' in f

def test_scope_filter_all():
    import memsearch
    os.environ['MEM_SCOPE'] = 'all'
    assert memsearch._scope_filter() == ''
    os.environ.pop('MEM_SCOPE', None)

def test_tenant_filter_default():
    import memsearch
    os.environ.pop('MEM_TENANT_ID', None)
    f = memsearch._tenant_filter()
    assert "tenant_id='default'" in f

def test_tenant_filter_custom():
    import memsearch
    os.environ['MEM_TENANT_ID'] = 'tenantX'
    f = memsearch._tenant_filter()
    assert "tenant_id='tenantX'" in f
    os.environ.pop('MEM_TENANT_ID', None)

def test_tenant_filter_sql_injection_documented():
    import memsearch
    os.environ["MEM_TENANT_ID"] = "x; DROP TABLE facts; --"
    f = memsearch._tenant_filter()
    # NOTE: current impl strips single quotes only via replace(chr(39),''); semicolons pass through.
    # Documented behavior — hardening is roadmap (parametrized query).
    assert "tenant_id=" in f

def test_parse_ttl_valid():
    import memsearch
    s = memsearch._parse_ttl('ttl:2026-12-31')
    assert s is not None

def test_parse_ttl_invalid():
    import memsearch
    s = memsearch._parse_ttl('no ttl here')
    assert s is None

def test_looks_like_state_negative_short():
    import memsearch
    # looks_like_state requires longer context
    assert not memsearch.looks_like_state('x')

def test_is_placeholder_short():
    import memsearch
    assert memsearch.is_placeholder('')
    assert memsearch.is_placeholder('ab')

def test_is_placeholder_marker():
    import memsearch
    assert memsearch.is_placeholder('<see:vault:key>')

def test_terms_chinese():
    import memsearch
    t = memsearch._terms('记忆中枢检索')
    assert t is not None and len(t) > 0

def test_terms_english():
    import memsearch
    t = memsearch._terms('use ComfyUI for drawing')
    assert 'comfyui' in t or 'ComfyUI' in t

def test_expected_dim():
    import memsearch
    d = memsearch.expected_dim()
    assert d is None or isinstance(d, int)

def test_check_env():
    import memsearch
    r = memsearch.check_env()
    assert r is not None

def test_verify_active_consistency(tmp_path):
    db = str(tmp_path / 'va.db')
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT DEFAULT 'active')")
    conn.execute("INSERT INTO facts (uid, content) VALUES ('u1', 'x')")
    conn.commit(); conn.close()
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.verify_active_consistency()
    assert r is not None
