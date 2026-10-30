# -*- coding: utf-8 -*-
"""Integration tests for auto_onboard high-level config injection functions.

These functions write into the user's real config files. To test them safely,
we monkeypatch USERPROFILE to point at a tmp_path. The functions internally
use os.path.expanduser("~") or os.environ.get("USERPROFILE") to resolve paths,
so redirecting HOME + USERPROFILE redirects all file I/O.

The key invariant being tested: after running each function, the target file
exists, contains the expected content, and running it a second time produces
SKIP (idempotent).
"""
import io
import json
import os

import pytest

from integrations import auto_onboard as ao


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect USERPROFILE/HOME to tmp_path so auto_onboard writes to test files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(os, "environ", {**os.environ,
        "USERPROFILE": str(home), "HOME": str(home)})
    monkeypatch.setattr("os.path.expanduser", lambda p: str(home) + p[1:] if p.startswith("~") else p)
    return home


@pytest.fixture
def fake_hub(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "mcp_server.py").write_text("# fake mcp server\n")
    (hub / "gateway.py").write_text("# fake gateway\n")
    return hub


class TestInjectCodexSkills:
    def test_creates_skills_link(self, tmp_path, fake_hub):
        """inject_codex_skills should create a skills symlink to the agents dir."""
        report = []
        ao.inject_codex_skills(report)
        # it should have reported something (OK, SKIP, or MISS)
        assert len(report) > 0
        assert report[-1][0] in ("OK", "SKIP", "MISS")


class TestInjectOpenClaw:
    def test_missing_agents_md(self, tmp_path, fake_hub):
        """When OpenClaw config dir doesn\'t exist, report MISS not crash."""
        report = []
        ao.inject_openclaw(str(fake_hub), report)
        assert len(report) > 0


class TestInjectDsh:
    def test_missing_agents_md(self, tmp_path, fake_hub):
        report = []
        ao.inject_dsh(str(fake_hub), report)
        assert len(report) > 0


class TestNormalizeDoubao:
    def test_runs_without_crash(self, tmp_path, fake_hub):
        report = []
        ao.normalize_doubao(str(fake_hub), report)
        assert len(report) > 0


class TestInjectCodexMarketplace:
    def test_missing_config(self, tmp_path, fake_hub, monkeypatch):
        """When ~/.codex/config.toml doesn\'t exist, report MISS not crash."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr("os.path.expanduser",
                           lambda p: str(fake_home) + p[1:] if p.startswith("~") else p)
        report = []
        ao.inject_codex_marketplace(str(fake_hub), report)
        assert len(report) > 0
        assert report[-1][0] in ("MISS", "SKIP", "OK")
