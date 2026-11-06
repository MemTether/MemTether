# -*- coding: utf-8 -*-
"""2026-11-06 (Persome audit): P0 changes regression tests.

P0#1 - PII scrub expanded from 6 to 12 patterns (gateway._sensitive_check)
P0#2 - search hits carry age_days + confidence (memsearch.search_hybrid)
P0#3 - MCP initialize response carries routing instructions (mcp_server)
P0#4 - rebuild() fail-soft returns carry a health block (gateway.rebuild)
P0#5 - correct() refuses ok=true when the audit receipt failed (gateway.correct)
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

os.environ.setdefault("MEM_DB", os.path.join(
    os.environ.get("TEMP", "/tmp"), "memtether_p0_test.db"))
os.environ.setdefault("MEM_SKIP_VECTOR", "1")

import gateway  # noqa: E402


# ---------------------------------------------------------------- P0#1: PII
class TestPiiScrubExpanded:
    """Persome privacy/scrub.py patterns ported into _sensitive_check."""

    def test_bearer_token_caught(self):
        hits = gateway._sensitive_check("Authorization Bearer abcdef1234567890abcdef")
        assert any(h[0] == 'Bearer token' for h in hits)

    def test_jwt_caught(self):
        jwt = ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
               "dozjgNryP4J3jVmNHl0w5N0XUkxSQt9VJApzcjB3Z2Y")
        hits = gateway._sensitive_check(jwt)
        assert any(h[0] == 'JWT' for h in hits)

    def test_password_assignment_caught(self):
        hits = gateway._sensitive_check("password: hunter2")
        assert any(h[0] == 'Password assignment' for h in hits)

    def test_cn_password_caught(self):
        hits = gateway._sensitive_check("\u5bc6\u7801\uff1ahunter2")
        assert any(h[0] == 'Password assignment (zh)' for h in hits)

    def test_cn_mobile_caught(self):
        hits = gateway._sensitive_check("call me at 13812345678")
        assert any(h[0] == 'CN mobile number' for h in hits)

    def test_email_caught(self):
        hits = gateway._sensitive_check("reach me at user@example.com")
        assert any(h[0] == 'Email' for h in hits)

    def test_clean_text_passes(self):
        assert gateway._sensitive_check(
            "normal text about project architecture") == []

    def test_remember_blocks_email_content(self):
        with pytest.raises(ValueError, match=".*"):
            gateway.remember("my email is secret-user@example.com keep private",
                             type="fact", source="codex")


# ---------------------------------------------------------------- P0#2: hits
class TestHitAgeDaysConfidence:
    def test_hit_has_age_days_and_confidence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        gateway.init_db()
        gateway.remember("age days test marker", type="fact", source="codex")
        import memsearch
        memsearch.DB = gateway.DB  # align memsearch DB with monkeypatched env
        gateway.init_db()
        r = memsearch.search_hybrid("age days test marker", limit=3)
        hits = r["results"] if isinstance(r, dict) else r
        assert hits, "expected at least one hit"
        h = hits[0]
        assert "age_days" in h
        assert "confidence" in h
        assert h["age_days"] is not None and h["age_days"] >= 0
        assert isinstance(h["confidence"], float) and 0.0 <= h["confidence"] <= 1.0


# ---------------------------------------------------------------- P0#3: MCP
class TestMcpInstructions:
    def test_initialize_returns_instructions(self, tmp_path):
        env = dict(os.environ)
        env["MEM_DB"] = str(tmp_path / "mcp.db")
        env["MEM_SKIP_VECTOR"] = "1"
        p = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(gateway.__file__),
                                          "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")
        try:
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {}}
            p.stdin.write(json.dumps(init) + "\n")
            p.stdin.flush()
            resp = json.loads(p.stdout.readline())
            result = resp.get("result", {})
            assert "instructions" in result, \
                "initialize response must carry routing instructions"
            instr = result["instructions"]
            assert "search_memory" in instr
            assert "add_memories" in instr
        finally:
            p.kill()


# ---------------------------------------------------------------- P0#4: health
class TestRebuildHealth:
    def test_no_wb_targets_returns_health(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "h.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.delenv("MEM_PROJ_PATH", raising=False)
        gateway.init_db()
        gateway.remember("rebuild health marker", type="fact", source="codex")
        r = gateway.rebuild()
        assert r.get("ok") is True
        if r.get("skipped") == "no_wb_targets":
            h = r.get("health")
            assert h is not None, "skipped rebuild must carry a health block"
            assert "active_count" in h
            assert "fts_count" in h
            assert "schema_ok" in h


# ---------------------------------------------------------------- P0#5: audit
class TestCorrectAuditReceipt:
    def test_correct_writes_audit_row(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "a.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        gateway.init_db()
        # audit_log is created lazily on first audit() call in init_db chain
        r1 = gateway.remember("correct audit marker v1", type="fact",
                              source="codex")
        r2 = gateway.correct(r1["uid"], "correct audit marker v2",
                             "test", by_agent="codex")
        assert r2.get("ok") is True
        conn = sqlite3.connect(gateway.DB)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE op='correct'").fetchone()[0]
        finally:
            conn.close()
        assert n >= 1, "correct must leave a provable audit receipt"