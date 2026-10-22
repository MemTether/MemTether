# -*- coding: utf-8 -*-
"""tests for integrations/auto_onboard.py

Why this file exists (2026-10-22): this module WRITES into the user's client
configs (Codex marketplace/skills, OpenClaw, dsh, doubao) and had ZERO test
coverage. Its first ever test run found a real defect: a read/write round trip
silently converted CRLF files to LF, i.e. it rewrote users' line endings and
would have produced whole-file git diffs on Windows.

Covered: the inject_marked contract (idempotent / replace-in-place /
skip-when-human-equivalent / missing-file) and line-ending preservation.
"""
import io

from integrations import auto_onboard as ao


def _block(version="v1"):
    return ao.MARK_BEGIN + "\nGUIDE %s\n" % version + ao.MARK_END


def test_fresh_append(tmp_path):
    p = str(tmp_path / "AGENTS.md")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write("# Existing\n")
    rep = []
    ao.inject_marked(p, _block(), "fresh", rep)
    assert rep[-1][0] == "OK"
    assert ao.MARK_BEGIN in io.open(p, encoding="utf-8").read()


def test_rerun_is_idempotent(tmp_path):
    p = str(tmp_path / "AGENTS.md")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write("# Existing\n")
    ao.inject_marked(p, _block(), "a", [])
    first = io.open(p, "rb").read()
    rep = []
    ao.inject_marked(p, _block(), "b", rep)
    assert rep[-1][0] == "SKIP"
    assert io.open(p, "rb").read() == first


def test_block_update_replaces_in_place(tmp_path):
    p = str(tmp_path / "AGENTS.md")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write("# Existing\n")
    ao.inject_marked(p, _block("v1"), "a", [])
    rep = []
    ao.inject_marked(p, _block("v2"), "b", rep)
    t = io.open(p, encoding="utf-8").read()
    assert rep[-1][0] == "OK"
    assert t.count(ao.MARK_BEGIN) == 1
    assert "v2" in t and "v1" not in t


def test_missing_file_is_reported_not_raised(tmp_path):
    rep = []
    ao.inject_marked(str(tmp_path / "nope.md"), _block(), "x", rep)
    assert rep[-1][0] == "MISS"


def test_human_equivalent_is_left_alone(tmp_path):
    p = str(tmp_path / "AGENTS.md")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write("# mine\nmemory.db lives here, use mem.py\n")
    rep = []
    ao.inject_marked(p, _block(), "x", rep,
                     covered_test=ao._covered_by_memtether)
    assert rep[-1][0] == "SKIP"
    assert ao.MARK_BEGIN not in io.open(p, encoding="utf-8").read()


def test_crlf_is_preserved(tmp_path):
    """The regression that motivated this file: CRLF must stay CRLF.

    Before the fix, _read normalised CRLF to LF while _write wrote LF, so one
    round trip rewrote every line ending in the user's file.
    """
    p = str(tmp_path / "crlf.md")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write("a\r\nb\r\n")
    before = io.open(p, "rb").read()
    assert before.count(b"\r\n") == 2
    rep = []
    ao.inject_marked(p, _block(), "x", rep)
    after = io.open(p, "rb").read()
    assert rep[-1][0] == "OK"
    assert after.count(b"\n") == after.count(b"\r\n")
    assert after.startswith(before)
