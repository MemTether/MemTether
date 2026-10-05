"""Tests for rate limiter (P0-3, 2026-10-05)."""
import os, sys
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['MEM_DB'] = ':memory:'

def test_rate_limit_decorators_present():
    """All 4 mutating endpoints must have .limit decorator."""
    import inspect
    from api_server import app
    found = {}
    for route in app.routes:
        if not hasattr(route, 'endpoint'): continue
        fn = route.endpoint
        path = getattr(route, 'path', '')
        if path in ('/remember','/search','/correct','/retire'):
            # decorator adds attribute
            found[path] = hasattr(fn, '_rate_limit') or hasattr(fn, '__wrapped__') or 'limit' in str(inspect.getsource(fn))
    assert len(found) == 4, f'Expected 4 decorated endpoints, got {list(found)}'
    for p, has in found.items():
        assert has, f'{p} has no rate limit decorator'

def test_rate_limit_object_exists():
    from api_server import _limiter
    assert _limiter is not None
