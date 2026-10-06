# -*- coding: utf-8 -*-
"""Adapter write-path tests (P0-2, 2026-10-06).

Ports tether_connect.do_selftest() 16 assertions into pytest so the three
config-layout families (plain JSON / JSONC-preserving / TOML) are exercised
in CI, not just on the maintainer's machine. Network-free, tmp_path-based.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from clients import all_adapters, find as find_adapter  # noqa: E402
from clients.jsonc import loads as jsonc_loads  # noqa: E402
from clients.base import ServerSpec, Target  # noqa: E402


# ---------------------------------------------------------------- registry
def test_adapter_registry():
    ads = all_adapters()
    assert len(ads) >= 20
    assert all(a.id and a.display for a in ads)
    assert len({a.id for a in ads}) == len(ads)
    assert all(isinstance(a.candidates(), list) for a in ads)


def _find(cid):
    a = find_adapter(cid)
    assert a is not None, f"adapter {cid} not found"
    return a


def _target(a, path, exists):
    return Target(a.id, a.display, "user", path, exists, True, "")


# ------------------------------------------------- plain-JSON family (cursor)
def test_json_adapter_create_idempotent_readback(tmp_path):
    a = _find("workbuddy-cn")
    spec = ServerSpec(name="memory-hub", command="C:\\py.exe", args=["mcp_server.py"], env={})
    p = str(tmp_path / "mcp.json")
    t = _target(a, p, exists=False)
    ch = a.write(t, spec, dry_run=False)
    assert ch.action == "create"
    assert os.path.exists(p)

    t = _target(a, p, exists=True)
    ch2 = a.write(t, spec, dry_run=False)
    assert ch2.action == "noop"

    ok, _ = a.read_back(t, spec)
    assert ok


def test_json_adapter_preserves_existing_servers(tmp_path):
    a = _find("workbuddy-cn")
    spec = ServerSpec(name="memory-hub", command="C:\\py.exe", args=["mcp_server.py"], env={})
    p = str(tmp_path / "mcp.json")
    t = _target(a, p, exists=False)
    a.write(t, spec, dry_run=False)
    t = _target(a, p, exists=True)

    spec2 = ServerSpec(name="other", command="C:\\py2.exe", args=[], env={})
    ch = a.write(t, spec2, dry_run=False)
    data = jsonc_loads(open(p, encoding="utf-8").read())
    assert ch.action == "insert"
    assert "memory-hub" in data["mcpServers"] and "other" in data["mcpServers"]


def test_json_adapter_fails_closed_on_missing_root(tmp_path):
    a = _find("zcode")
    p = str(tmp_path / "config.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{\n  "other": 1\n}\n')
    t = _target(a, p, exists=True)
    ch = a.write(t, ServerSpec(name="memory-hub", command="c", args=[], env={}), dry_run=False)
    assert ch.action == "error"


def test_json_adapter_never_overwrites_broken_json(tmp_path):
    a = _find("workbuddy-cn")
    p = str(tmp_path / "mcp.json")
    broken = '{"mcpServers": {"a": }}}'
    with open(p, "w", encoding="utf-8") as f:
        f.write(broken)
    t = _target(a, p, exists=True)
    ch = a.write(t, ServerSpec(name="x", command="c", args=[], env={}), dry_run=False)
    assert ch.action == "error"
    assert open(p, encoding="utf-8").read() == broken


def test_json_adapter_semantic_equivalence_is_noop(tmp_path):
    a = _find("workbuddy-cn")
    p = str(tmp_path / "mcp.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write('{\n  "mcpServers": {\n    "x": {\n      "command": "C:/a/b/py.exe",\n'
                '      "args": ["C:/a/b/s.py"]\n    }\n  }\n}\n')
    before = open(p, encoding="utf-8").read()
    t = _target(a, p, exists=True)
    spec = ServerSpec(name="x", command=r"C:\a\b\py.exe", args=[r"C:\a\b\s.py"])
    ch = a.write(t, spec, dry_run=False)
    assert ch.action == "noop"
    assert open(p, encoding="utf-8").read() == before
    ok, _ = a.read_back(t, spec)
    assert ok


def test_json_adapter_install_markers_block_uninstalled_client(tmp_path):
    a = _find("claude-code")
    p = str(tmp_path / "x.json")
    t = _target(a, p, exists=False)
    installed, reason = a.probe("user", p)
    assert a.install_markers, "claude-code must declare install markers"
    assert not installed and "未发现安装痕迹" in reason


# ---------------------------------------------------- TOML family (codex)
def test_toml_adapter_recognizes_native_layout(tmp_path):
    a = _find("codex")
    spec = ServerSpec(name="memory-hub", command=r"E:\hub\py.exe", args=[r"E:\hub\mcp.py"])
    p = str(tmp_path / "config.toml")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write('[mcp_servers.memory-hub]\ncommand = "E:\\\\hub\\\\py.exe"\n'
                'args = ["E:\\\\hub\\\\mcp.py"]\n')
    before = open(p, encoding="utf-8").read()
    t = _target(a, p, exists=True)
    ch = a.write(t, spec, dry_run=False)
    assert ch.action == "noop"
    assert open(p, encoding="utf-8").read() == before
    ok, _ = a.read_back(t, spec)
    assert ok


def test_toml_adapter_fails_closed_on_conflicting_table(tmp_path):
    a = _find("codex")
    spec = ServerSpec(name="memory-hub", command=r"E:\hub\py.exe", args=[r"E:\hub\mcp.py"])
    p = str(tmp_path / "dup.toml")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write('[mcp_servers.memory-hub]\ncommand = "other.exe"\nargs = []\n')
    t = _target(a, p, exists=True)
    ch = a.write(t, spec, dry_run=False)
    assert ch.action == "error"
    assert open(p, encoding="utf-8").read().count("[mcp_servers") == 1
