"""Tests for Hindsight Memory Defense (configurable PII policy, 2026-11-07)."""
import os
import pytest


class TestPiiPolicy:
    """_sensitive_check_with_policy: per-pattern block/redact/allow."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "pii.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_default_is_block(self, tmp_path, monkeypatch):
        """Without MEM_PII_POLICY, all patterns default to block."""
        gw = self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv("MEM_PII_POLICY", raising=False)
        should_block, labels, redacted, hits = gw._sensitive_check_with_policy(
            "email is test@example.com here")
        assert should_block, "default should block"
        assert "Email" in labels

    def test_redact_action(self, tmp_path, monkeypatch):
        """MEM_PII_POLICY with redact replaces match with [REDACTED:type]."""
        gw = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("MEM_PII_POLICY", '{"Email": "redact"}')
        should_block, labels, redacted, hits = gw._sensitive_check_with_policy(
            "email is test@example.com here")
        assert not should_block, "redact should not block"
        assert "[REDACTED:Email]" in redacted, f"expected [REDACTED:Email], got: {redacted}"
        assert "test@example.com" not in redacted, "raw email should be gone"

    def test_allow_action(self, tmp_path, monkeypatch):
        """MEM_PII_POLICY with allow passes content through unchanged."""
        gw = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("MEM_PII_POLICY", '{"High-entropy string": "allow"}')
        content = "token is AbcDefGhiJklMnoPqrStuVwxYz0123456789 here"
        should_block, labels, redacted, hits = gw._sensitive_check_with_policy(content)
        # Should not block on high-entropy
        high_entropy_blocked = any("High-entropy" in l for l in labels)
        assert not high_entropy_blocked, "allow should not block"
        # Content unchanged for that pattern
        assert content == redacted or "[REDACTED:High-entropy string]" not in redacted

    def test_fingerprint_no_raw_leak(self, tmp_path, monkeypatch):
        """Hit previews never contain the raw secret value."""
        gw = self._setup(tmp_path, monkeypatch)
        email = "verylongemail@company.example.com"
        _, _, _, hits = gw._sensitive_check_with_policy(f"contact {email} now")
        email_hits = [h for h in hits if h["detector"] == "Email"]
        assert email_hits, "should have email hit"
        for h in email_hits:
            assert email not in h["preview"], f"raw value leaked in preview: {h['preview']}"

    def test_policy_affects_remember(self, tmp_path, monkeypatch):
        """MEM_PII_POLICY=redact lets remember succeed with redacted content."""
        gw = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("MEM_PII_POLICY", '{"Email": "redact"}')
        r = gw.remember("contact person email is admin@company.com for support", source="codex")
        assert r["ok"], f"remember should succeed with redact policy: {r}"
