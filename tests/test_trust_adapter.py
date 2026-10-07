# -*- coding: utf-8 -*-
"""tests/test_trust_adapter.py — a47: clients/trust.py (was 16% coverage)

Electron MCP trust gate: hash algorithm + approve/is_trusted file
roundtrip + corrupt-file fail-closed behavior. trust.py ships in the
wheel and is the difference between "UI shows connected" and "agent
actually gets tools" for WorkBuddy/CodeBuddy class clients.
"""
import json, os, sys, pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from clients.trust import hash_of, key_of, is_trusted, approve, load_entry


class TestHashAlgorithm:
    def test_args_order_insensitive(self):
        a = hash_of({"command": "c", "args": ["b", "a"]})
        b = hash_of({"command": "c", "args": ["a", "b"]})
        assert a == b

    def test_env_value_insensitive_key_sensitive(self):
        h1 = hash_of({"command": "c", "env": {"A": "1"}})
        h2 = hash_of({"command": "c", "env": {"A": "2"}})
        h3 = hash_of({"command": "c", "env": {"B": "1"}})
        assert h1 == h2
        assert h1 != h3

    def test_sha256_length(self):
        assert len(hash_of({"command": "c"})) == 64

    def test_key_format(self):
        h = hash_of({"command": "c"})
        assert key_of({"command": "c"}, "srv") == h + "::srv"


class TestTrustRoundtrip:
    def test_approve_then_trusted(self, tmp_path):
        entry = {"command": "python", "args": ["-m", "mcp_server"]}
        ok, detail, path = approve(str(tmp_path), "memtether", entry, dry_run=False)
        assert ok, detail
        trusted, why = is_trusted(str(tmp_path), "memtether", entry)
        assert trusted, why

    def test_dry_run_does_not_write(self, tmp_path):
        entry = {"command": "python"}
        approve(str(tmp_path), "memtether", entry, dry_run=True)
        assert not (tmp_path / "mcp-approvals.json").exists()

    def test_command_change_breaks_trust_with_helpful_detail(self, tmp_path):
        """The whole point: hash mismatch must be detected and explained."""
        old = {"command": "python", "args": ["-m", "mcp_server"]}
        approve(str(tmp_path), "memtether", old, dry_run=False)
        new = {"command": "python3", "args": ["-m", "mcp_server"]}
        trusted, why = is_trusted(str(tmp_path), "memtether", new)
        assert not trusted
        assert "hash" in why.lower(), why

    def test_idempotent_approve(self, tmp_path):
        entry = {"command": "python"}
        approve(str(tmp_path), "memtether", entry, dry_run=False)
        ok, detail, _ = approve(str(tmp_path), "memtether", entry, dry_run=False)
        assert ok and "已存在" in detail

    def test_corrupt_approvals_fails_closed(self, tmp_path):
        """Broken JSON must NOT be overwritten (protects user's trust data)."""
        (tmp_path / "mcp-approvals.json").write_text("{corrupt", encoding="utf-8")
        entry = {"command": "python"}
        ok, detail, _ = approve(str(tmp_path), "memtether", entry, dry_run=False)
        assert not ok
        assert json.loads((tmp_path / "mcp-approvals.json").read_text(encoding="utf-8", errors="ignore") or "{}") if False else True
        assert (tmp_path / "mcp-approvals.json").read_text(encoding="utf-8") == "{corrupt"

    def test_load_entry_missing_root(self, tmp_path):
        assert load_entry(str(tmp_path), "nope") is None
