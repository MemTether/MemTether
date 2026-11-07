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

# ---------------------------------------------------- P1: Persome follow-ups
class TestPersomeP1:
    """P1 batch (2026-11-07): corruption probe / schema fingerprint /
    retrieval-stats carry-over."""

    def test_corruption_probe_raises_at_connect(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "corrupt.db"))
        (tmp_path / "corrupt.db").write_bytes(b"not a sqlite database at all" * 10)
        import importlib
        import gateway
        importlib.reload(gateway)
        try:
            with pytest.raises(RuntimeError, match="corrupted"):
                gateway.get_conn()
        finally:
            importlib.reload(gateway)

    def test_schema_fingerprint_deterministic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "fp.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.init_db()
        fp1 = gateway._schema_fingerprint()
        fp2 = gateway._schema_fingerprint()
        assert fp1 and fp1 == fp2
        # deterministic across a fresh init on the same db
        gateway.init_db()
        assert gateway._schema_fingerprint() == fp1

    def test_correct_carries_use_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "carry.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib, sqlite3
        import gateway
        importlib.reload(gateway)
        gateway.init_db()
        r1 = gateway.remember("carry-over probe v1", source="codex")
        uid1 = r1["uid"]
        gateway.bump_qvalue(uid1, reward=1.0, agent="codex")  # use_count 0->1
        r2 = gateway.correct(uid1, "carry-over probe v2", reason="update",
                             by_agent="codex")
        assert r2.get("ok"), r2
        conn = sqlite3.connect(gateway.DB)
        try:
            old_n = conn.execute(
                "SELECT use_count FROM facts WHERE uid=?", (uid1,)).fetchone()[0]
            new_n = conn.execute(
                "SELECT use_count FROM facts WHERE uid=?",
                (r2["new_uid"],)).fetchone()[0]
        finally:
            conn.close()
        assert new_n >= 1 and new_n >= old_n, (
            f"supersede must carry earned use_count (old={old_n}, new={new_n})")

class TestPersomeP1TxnWrite:
    """P1#9: two-phase transactional write for projections."""

    def test_txn_write_committed_and_journal_readable(self, tmp_path):
        import hubguard
        target = tmp_path / "MEMORY.md"
        target.write_text("old", encoding="utf-8")
        r = hubguard.transactional_write(str(target), "new content",
                                         op="projection.test")
        assert r["ok"] and r["txn_stage"] == "committed"
        assert r["journal"] and target.read_text(encoding="utf-8") == "new content"
        j = hubguard.read_txn_journal(str(target))
        assert j["op"] == "projection.test"
        assert j["stage"] == "committed"
        assert j["expected"] is not None

    def test_journal_written_before_replace(self, tmp_path):
        import hubguard
        target = tmp_path / "MEMORY2.md"
        target.write_text("v1", encoding="utf-8")
        r = hubguard.transactional_write(str(target), "v2")
        assert r["txn_stage"] == "committed"
        j = hubguard.read_txn_journal(str(target))
        # expected hash must have been the PRE-write content hash
        assert j["expected_size"] == 2, j

class TestBinPackFill:
    """P1#10: shortest-first bin packing beats greedy-skip on wasted headroom."""

    def test_binpack_fits_more_than_greedy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "bp.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.setenv("MEM_SINK_PATH", str(tmp_path / "sink.json"))
        monkeypatch.setenv("MEM_PROJ_PATH", str(tmp_path / "MEMORY.md"))
        monkeypatch.setenv("MEM_PROJ_BUDGET", "900")
        monkeypatch.setenv("MEM_PROJ_ASSET_MAX", "0")
        monkeypatch.setenv("MEM_PROJ_ASSET_PRIO_MAX", "0")
        monkeypatch.delenv("MEM_PROJ_GREEDY_FILL", raising=False)
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        # one huge candidate first (time order), then short ones
        gateway.remember("huge candidate " + "x" * 250, source="codex")
        for i in range(5):
            gateway.remember("short %d" % i, source="codex")
        r = gateway.rebuild()
        md = open(os.environ["MEM_PROJ_PATH"], encoding="utf-8").read()
        kept_short = sum(1 for i in range(5) if ("short %d" % i) in md)
        # bin-packing must place most/all short rows despite the huge one
        assert kept_short >= 3, (kept_short, r["facts_listed"], len(md))
        assert len(md) <= 1100

    def test_greedy_fallback_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "gr.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.setenv("MEM_SINK_PATH", str(tmp_path / "sink.json"))
        monkeypatch.setenv("MEM_PROJ_PATH", str(tmp_path / "MEMORY.md"))
        monkeypatch.setenv("MEM_PROJ_BUDGET", "3980")
        monkeypatch.setenv("MEM_PROJ_GREEDY_FILL", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        gateway.remember("greedy fallback probe", source="codex")
        gateway.rebuild()
        md = open(os.environ["MEM_PROJ_PATH"], encoding="utf-8").read()
        assert "greedy fallback probe" in md

class TestPiiParity17:
    """Persome 17-pattern parity (P0 remainder, 2026-11-07)."""

    def test_google_api_key(self):
        key = 'AIza' + 'A' * 29 + 'a1B2c3'   # 35 after AIza (4+35=39)
        assert len(key) == 39, len(key)
        hits = gateway._sensitive_check(key)
        assert any(h[0] == 'Google API key' for h in hits), hits

    def test_international_phone(self):
        hits = gateway._sensitive_check('call +441234567890 now')
        assert any(h[0] == 'International phone' for h in hits), hits
        # short / non-phone numbers must NOT match
        assert gateway._sensitive_check('order +123') == []
        assert gateway._sensitive_check('v1.2.3 build 4567890 ok') == []

    def test_card_or_id(self):
        hits = gateway._sensitive_check('card 6222021234567890123')
        assert any(h[0] == 'Card or ID number' for h in hits), hits
        # 12 digits = not in window -> no match
        assert gateway._sensitive_check('id 123456789012') == []

    def test_home_path(self):
        hits = gateway._sensitive_check('lives at /Users/alice/docs')
        assert any(h[0] == 'Home path' for h in hits), hits
        # Persome #398: no trailing slash required
        hits2 = gateway._sensitive_check('lives at /home/bob')
        assert any(h[0] == 'Home path' for h in hits2), hits2
        # Windows-style C:/Users/... still matches the /Users/ segment
        # (Persome pattern is drive-letter agnostic; both are leaks)
        hits3 = gateway._sensitive_check('use C:/Users/alice')
        assert any(h[0] == 'Home path' for h in hits3), hits3

    def test_high_entropy(self):
        s = 'Ab1' + 'Ab1' * 10   # 40 chars, mixed case+digit
        hits = gateway._sensitive_check(s)
        assert any(h[0] == 'High-entropy string' for h in hits), hits
        # Persome #389-again: package names (lowercase, no upper) never match
        assert gateway._sensitive_check('pip install some-package-name-abc') == []

    def test_remember_blocks_google_key(self):
        with pytest.raises(ValueError):
            gateway.remember('my key AIza' + 'A' * 35, type='fact',
                             source='codex')

class TestPersomeP2:
    """P2 batch (2026-11-07): bounded MCP sections + parent watchdog."""

    def test_search_section_bounded(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "sb.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.setenv("MEM_MCP_SECTION_BYTES", "2048")
        import importlib, json as _json
        import gateway, memsearch
        importlib.reload(gateway)
        importlib.reload(memsearch)
        memsearch.DB = gateway.DB
        gateway.init_db()
        for i in range(30):
            gateway.remember("section bound filler %d " % i + "y" * 200,
                             type="fact", source="codex")
        import mcp_server
        importlib.reload(mcp_server)
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "search_memory",
                          "arguments": {"query": "section bound filler",
                                        "limit": 30}}}
        resp = mcp_server.handle(req)
        result = resp["result"]
        txt = result["content"][0]["text"]
        data = _json.loads(txt)
        assert data.get("truncated") is True, txt[:300]
        assert len(data["results"]) < 30

    def test_watchdog_starts_and_flags(self, monkeypatch):
        import mcp_server
        monkeypatch.delenv("MEM_MCP_WATCHDOG", raising=False)
        mcp_server._WATCHDOG_STARTED = False
        mcp_server._start_parent_watchdog()
        assert mcp_server._WATCHDOG_STARTED is True
        # idempotent
        mcp_server._start_parent_watchdog()
        assert mcp_server._WATCHDOG_STARTED is True

    def test_watchdog_env_off(self, monkeypatch):
        import mcp_server
        monkeypatch.setenv("MEM_MCP_WATCHDOG", "0")
        mcp_server._WATCHDOG_STARTED = False
        mcp_server._start_parent_watchdog()
        assert mcp_server._WATCHDOG_STARTED is False

class TestMmrDiversity:
    """P2#14: MMR diversity on final results (token-Jaccard proxy)."""

    def test_mmr_diversifies_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "mmr.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.delenv("MEM_MMR", raising=False)
        monkeypatch.setenv("MEM_RECALL_BUDGET", "6000")
        import importlib
        import gateway, memsearch
        importlib.reload(gateway)
        importlib.reload(memsearch)
        memsearch.DB = gateway.DB
        gateway.init_db()
        # two near-identical (distinct content strings, same query terms)
        # + one distinct-topic doc, also matching query terms
        gateway.remember("deploy port 9090 alpha", source="codex")
        gateway.remember("deploy port 9090 alpha v2", source="codex")
        gateway.remember("deploy backup port beta", source="codex")
        r = memsearch.search_hybrid("deploy port", limit=3)
        hits = r["results"]
        assert len(hits) == 3, (len(hits), r["diag"].get("recall_budget"))
        texts = [h.get("content", "") for h in hits]
        assert any("beta" in tx for tx in texts), texts
        assert r["diag"].get("mmr_lambda") == 0.7

    def test_mmr_off_restores_pure_relevance(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "mmr0.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.setenv("MEM_MMR", "0")
        import importlib
        import gateway, memsearch
        importlib.reload(gateway)
        importlib.reload(memsearch)
        memsearch.DB = gateway.DB
        gateway.init_db()
        gateway.remember("mmr off probe", source="codex")
        r = memsearch.search_hybrid("mmr off probe", limit=3)
        assert "mmr_reordered" not in r["diag"]
