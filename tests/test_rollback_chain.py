# -*- coding: utf-8 -*-
"""tests/test_rollback_chain.py — a50: apply -> rollback restores original

The safety net itself was never tested: tether_connect backup manifest
+ do_rollback must restore the pre-apply content of every touched file.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from clients import find as find_adapter
from clients.base import ServerSpec, Target, backup, write_manifest, BACKUP_ROOT
import tether_connect


SPEC = ServerSpec(name="memory-hub", command="python", args=["-m", "mcp_server"], env={})
ORIGINAL = '{"mcpServers": {"user-own-tool": {"command": "node", "args": ["keep.js"]}}}'


@pytest.fixture()
def isolated_backup_root(tmp_path, monkeypatch):
    broot = os.path.join(str(tmp_path), "backups")
    os.makedirs(broot, exist_ok=True)
    monkeypatch.setattr("clients.base.BACKUP_ROOT", broot)
    monkeypatch.setattr(tether_connect, "BACKUP_ROOT", broot)
    yield broot


def _apply_to_real_layout(a, tmp_path, monkeypatch):
    """apply = write() with backup() called first (as tether_connect apply does)."""
    import time
    fname = os.path.basename(a.candidates()[0][1].replace("\\", "/"))
    p = os.path.join(str(tmp_path), "home", fname)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(ORIGINAL)
    t = Target(a.id, a.display, "user", p, True, True, "")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bpath = backup(p, stamp, a.id)
    a.write(t, SPEC, dry_run=False)
    # record the change like tether_connect.apply does
    ch = a.read_back(t, SPEC)
    from clients.base import Change
    changes = [Change(t, "merge", "test")]
    changes[0].backup = bpath
    write_manifest(stamp, changes)
    return p, stamp


class TestRollbackChain:
    def test_apply_then_rollback_restores_original(self, tmp_path, isolated_backup_root, monkeypatch):
        a = find_adapter("cursor")
        p, stamp = _apply_to_real_layout(a, tmp_path, monkeypatch)
        # after apply, our server is present and user tool preserved
        after_apply = open(p, encoding="utf-8").read()
        assert "memory-hub" in after_apply
        assert "user-own-tool" in after_apply
        # rollback
        rc = tether_connect.do_rollback(stamp)
        assert rc == 0
        restored = open(p, encoding="utf-8").read()
        assert restored == ORIGINAL, f"rollback did not restore original:\n{restored}"

    def test_rollback_with_no_backup_skips_gracefully(self, tmp_path, isolated_backup_root, monkeypatch):
        """Files that didn't exist pre-apply have no backup — must be skipped, not crash."""
        a = find_adapter("cursor")
        import time
        fname = os.path.basename(a.candidates()[0][1].replace("\\", "/"))
        p = os.path.join(str(tmp_path), "home", fname)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"memory-hub": {"command": "python"}}}, f)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        from clients.base import Change
        ch = Change(Target(a.id, a.display, "user", p, True, True, ""),
                    "create", "test")
        ch.backup = ""
        write_manifest(stamp, [ch])
        rc = tether_connect.do_rollback(stamp)
        assert rc == 0

    def test_rollback_bad_manifest_lists_files(self, tmp_path, isolated_backup_root, capsys):
        import time
        stamp = time.strftime("%Y%m%d-%H%M%S")
        d = os.path.join(isolated_backup_root, stamp)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "some.bak"), "w").write("x")
        rc = tether_connect.do_rollback(stamp)
        assert rc == 1  # no manifest -> manual listing, non-fatal rc
        out = capsys.readouterr().out
        assert "manifest" in out or "manifest.json" in out
