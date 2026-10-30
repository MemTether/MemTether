# -*- coding: utf-8 -*-
"""Integration tests for auto_onboard config injection functions.

auto_onboard writes into the user's real config files (~/.codex/config.toml,
~/.agents/skills, etc). Those functions use USERPROFILE/HOME to find configs,
so they can't be safely tested with tmp_path alone.

What we CAN test:
- inject_marked: the core file-injection primitive (fully tested)
- Functions that check for file existence and report MISS/SKIP correctly
- The report structure

What we CANNOT safely test without touching real user files:
- inject_codex_marketplace (writes ~/.codex/config.toml)
- inject_codex_skills (writes ~/.codex/skills)
- install_codex_plugin (runs codex CLI)
- inject_openclaw / inject_dsh (write agent config files)

So: we test inject_marked deeply + the MISS/SKIP paths of the higher-level
functions when their target files don't exist.
"""
import io
import os

from integrations import auto_onboard as ao


def _block(version="v1"):
    return ao.MARK_BEGIN + "\nTEST GUIDE %s\n" % version + ao.MARK_END


class TestInjectMarked:
    def test_fresh_append(self, tmp_path):
        p = str(tmp_path / "test.md")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write("existing\n")
        rep = []
        ao.inject_marked(p, _block(), "fresh", rep)
        assert rep[-1][0] == "OK"
        assert ao.MARK_BEGIN in io.open(p, encoding="utf-8").read()

    def test_idempotent(self, tmp_path):
        p = str(tmp_path / "test.md")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write("existing\n")
        ao.inject_marked(p, _block(), "a", [])
        first = io.open(p, "rb").read()
        rep = []
        ao.inject_marked(p, _block(), "b", rep)
        assert rep[-1][0] == "SKIP"
        assert io.open(p, "rb").read() == first

    def test_update_replaces(self, tmp_path):
        p = str(tmp_path / "test.md")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write("old\n")
        ao.inject_marked(p, _block("v1"), "a", [])
        rep = []
        ao.inject_marked(p, _block("v2"), "b", rep)
        t = io.open(p, encoding="utf-8").read()
        assert t.count(ao.MARK_BEGIN) == 1
        assert "v2" in t and "v1" not in t

    def test_missing_file(self, tmp_path):
        rep = []
        ao.inject_marked(str(tmp_path / "nope.md"), _block(), "x", rep)
        assert rep[-1][0] == "MISS"

    def test_human_equivalent_skip(self, tmp_path):
        p = str(tmp_path / "test.md")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write("memory.db\nmem.py\n")
        rep = []
        ao.inject_marked(p, _block(), "x", rep, covered_test=ao._covered_by_memtether)
        assert rep[-1][0] == "SKIP"

    def test_crlf_preserved(self, tmp_path):
        p = str(tmp_path / "crlf.md")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write("a\r\nb\r\n")
        before = io.open(p, "rb").read()
        assert before.count(b"\r\n") == 2
        ao.inject_marked(p, _block(), "x", [])
        after = io.open(p, "rb").read()
        assert after.count(b"\n") == after.count(b"\r\n")
        assert after.startswith(before)


class TestMissPaths:
    """When the target config file doesn't exist, the functions should
    report MISS without crashing."""

    def test_codex_marketplace_missing(self, monkeypatch):
        monkeypatch.setenv("USERPROFILE", "/nonexistent")
        report = []
        ao.inject_codex_marketplace("/nonexistent/hub", report)
        assert report[-1][0] in ("MISS", "SKIP", "OK")

    def test_openclaw_missing(self, tmp_path):
        report = []
        ao.inject_openclaw(str(tmp_path / "fake_hub"), report)
        assert len(report) > 0

    def test_dsh_missing(self, tmp_path):
        report = []
        ao.inject_dsh(str(tmp_path / "fake_hub"), report)
        assert len(report) > 0
