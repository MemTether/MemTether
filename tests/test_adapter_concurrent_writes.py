# -*- coding: utf-8 -*-
"""tests/test_adapter_concurrent_writes.py — a50: two processes, one config

Real scenario: user runs `memtether setup cursor` in two terminals at
once (or setup + dashboard auto-onboard race). Writes must serialize via
hub_lock-style file locking, never interleave/corrupt.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

WORKER = """
import sys, os, time
sys.path.insert(0, __REPO__)
from clients import find as find_adapter
from clients.base import ServerSpec, Target
a = find_adapter('cursor')
p = __CFG__
spec = ServerSpec(name='memory-hub-' + sys.argv[1], command='python',
                  args=['-m', 'mcp_server'], env={})
t = Target(a.id, a.display, 'user', p, True, True, '')
ok = 0
import time as _t
for i in range(10):
    done = False
    for attempt in range(5):
        try:
            ch = a.write(t, spec, dry_run=False)
            ok += 1
            done = True
            break
        except Exception as e:
            msg = str(e)
            if '32' in msg or 'in use' in msg.lower() or '使用' in msg:
                _t.sleep(0.1 * (attempt + 1))  # transient share violation: retry
                continue
            print('EXC', e); break
    if not done:
        break
print('OK=' + str(ok))
"""
WORKER = WORKER.replace('__REPO__', repr(REPO))


class TestAdapterConcurrentWrites:
    def test_two_processes_same_config(self, tmp_path):
        p = os.path.join(str(tmp_path), "mcp.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {}}, f)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        src = WORKER.replace('__CFG__', repr(p))
        procs = [subprocess.Popen(
            [sys.executable, "-X", "utf8", "-c", src, str(w)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=REPO, env=env)
            for w in range(2)]
        outs = []
        for proc in procs:
            out, err = proc.communicate(timeout=50)
            assert proc.returncode == 0, f"worker crashed: {err[-300:]}"
            outs.append(out.strip())
        # both writers completed
        for o in outs:
            assert "OK=10" in o, o
        # config parses and contains BOTH server names (no lost update/corruption)
        text = open(p, encoding="utf-8").read()
        cfg = json.loads(text)  # raises if corrupted
        servers = cfg.get("mcpServers", {})
        assert "memory-hub-0" in servers, f"writer-0 lost: {list(servers)}"
        assert "memory-hub-1" in servers, f"writer-1 lost: {list(servers)}"
