# -*- coding: utf-8 -*-
"""tests/test_version.py — tests for memtether version and version --json CLI."""
import json
import os
import subprocess
import sys


def _run_cli(args, env_override=None):
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [sys.executable, "-m", "memtether"] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_version_subcommand_text():
    res = _run_cli(["version"])
    assert res.returncode == 0
    assert res.stdout.strip().startswith("memtether ")


def test_version_subcommand_json():
    res = _run_cli(["version", "--json"])
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert "version" in data
    assert "python" in data
    assert "db" in data
    assert data["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_version_flag_text():
    res = _run_cli(["--version"])
    assert res.returncode == 0
    assert res.stdout.strip().startswith("memtether ")


def test_version_flag_json():
    res = _run_cli(["--version", "--json"])
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert "version" in data
    assert "python" in data
    assert "db" in data
    assert data["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_version_json_flag_before_version():
    res = _run_cli(["--json", "--version"])
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert "version" in data
    assert "python" in data
    assert "db" in data


def test_version_json_respects_custom_mem_db(tmp_path):
    custom_db = str(tmp_path / "custom.db")
    res = _run_cli(["version", "--json"], env_override={"MEM_DB": custom_db})
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["db"] == custom_db
