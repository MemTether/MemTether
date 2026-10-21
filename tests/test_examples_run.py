# -*- coding: utf-8 -*-
"""tests/test_examples_run.py -- every example must actually run.

Why: a 2026-10-21 audit ran all nine examples and found
examples/basic_remember.py crashing with
    IntegrityError: NOT NULL constraint failed: fact_entities.entity_name
i.e. the first thing a newcomer runs after `pip install memtether` died. It also
turned out the example wrote into whatever DB local_paths.json pointed at - on
this machine, the production hub - so it was both broken AND unsafe.

These tests run each example in a subprocess with MEM_DB pointed at a temp file
and assert a zero exit code. Cheap (a few seconds each) and it catches exactly
the class of failure that "the file exists and imports fine" misses.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO, 'examples')


def _example_files():
    return sorted(f for f in os.listdir(EXAMPLES)
                  if f.endswith('.py') and not f.startswith('_'))


@pytest.mark.parametrize('name', _example_files())
def test_example_runs_clean(name, tmp_path):
    env = dict(os.environ)
    env['MEM_DB'] = str(tmp_path / 'ex.db')
    env['MEM_SKIP_VECTOR'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    # make sure the example cannot reach a real hub even if it ignores MEM_DB
    env.pop('MEM_HUB_DIR', None)
    r = subprocess.run([sys.executable, os.path.join(EXAMPLES, name)],
                       cwd=EXAMPLES, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env, timeout=180)
    assert r.returncode == 0, (
        '%s exited %d\n--- stdout ---\n%s\n--- stderr ---\n%s'
        % (name, r.returncode, (r.stdout or '')[-800:], (r.stderr or '')[-800:]))


def test_examples_do_not_write_outside_their_db(tmp_path):
    """An example must not depend on - or silently adopt - an ambient DB.

    local_paths.json points this checkout at the production hub, so an example
    that forgets to isolate itself writes demo rows into real memory. Run each
    example with MEM_DB set and assert nothing lands in the hub DB.
    """
    import sqlite3
    # Protect the real hub when this checkout points at one (local_paths.json),
    # which is exactly the configuration that caused the leak.
    # NOTE: resolve via memtether_paths first, not $MEM_DB - under pytest the
    # conftest has already pointed MEM_DB at a sandbox, so reading the env var
    # would silently protect nothing.
    # Read local_paths.json directly. memtether_paths.default_db() gives $MEM_DB
    # top priority, and conftest's autouse guard has already pointed MEM_DB at a
    # sandbox by the time this test body runs - so going through it can never
    # resolve the real hub and the test would silently protect nothing.
    import json
    hub_db = ''
    lp = os.path.join(REPO, 'local_paths.json')
    try:
        with open(lp, encoding='utf-8') as f:
            hub_db = (json.load(f).get('hub_dir') or '').strip()
        if hub_db:
            hub_db = os.path.join(hub_db, 'memory.db')
    except Exception:
        hub_db = ''
    if not hub_db or not os.path.exists(hub_db):
        pytest.skip('this checkout has no local_paths.json hub to protect')
    before = sqlite3.connect(hub_db).execute(
        "SELECT COUNT(*) FROM facts WHERE source IN ('demo','example')").fetchone()[0]
    for name in _example_files():
        env = dict(os.environ)
        env['MEM_DB'] = str(tmp_path / (name + '.db'))
        env['MEM_SKIP_VECTOR'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        subprocess.run([sys.executable, os.path.join(EXAMPLES, name)],
                       cwd=EXAMPLES, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env, timeout=180)
    after = sqlite3.connect(hub_db).execute(
        "SELECT COUNT(*) FROM facts WHERE source IN ('demo','example')").fetchone()[0]
    assert after == before, 'examples leaked %d rows into %s' % (after - before, hub_db)
