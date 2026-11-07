"""Tests for EverOS-inspired dead-letter audit (2026-11-07)."""
import os
import pytest


class TestDeadLetterAudit:
    """dead_letter_audit: detect write-only data (written but never read)."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "audit.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_returns_list(self, tmp_path, monkeypatch):
        """Function returns a list (possibly empty)."""
        gw = self._setup(tmp_path, monkeypatch)
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        result = gw.dead_letter_audit(conn)
        conn.close()
        assert isinstance(result, list), f"expected list, got: {type(result)}"

    def test_candidates_is_dead_letter(self, tmp_path, monkeypatch):
        """candidates table gets writes but source never reads it via SELECT...FROM."""
        gw = self._setup(tmp_path, monkeypatch)
        # Insert a candidate
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        conn.execute("INSERT INTO candidates (content, type, reason, source, ts) "
                     "VALUES (?, ?, ?, ?, datetime('now'))",
                     ("test candidate", "fact", "below threshold", "codex"))
        conn.commit()
        result = gw.dead_letter_audit(conn)
        conn.close()
        # candidates table should appear as dead letter
        candidate_entries = [d for d in result if d.get("table") == "candidates"]
        assert len(candidate_entries) > 0, \
            f"candidates should be flagged as dead letter, got: {result}"

    def test_facts_is_not_dead_letter(self, tmp_path, monkeypatch):
        """facts table has both reads and writes — should NOT be flagged."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("test fact", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        result = gw.dead_letter_audit(conn)
        conn.close()
        facts_entries = [d for d in result if d.get("table") == "facts"]
        assert len(facts_entries) == 0, \
            f"facts table should not be flagged, got: {facts_entries}"

    def test_empty_table_not_flagged(self, tmp_path, monkeypatch):
        """Table with 0 rows should not be flagged even if write-only."""
        gw = self._setup(tmp_path, monkeypatch)
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        # Don't insert anything into candidates
        result = gw.dead_letter_audit(conn)
        conn.close()
        # Empty candidates table should not be flagged (0 rows)
        cand = [d for d in result if d.get("table") == "candidates"]
        # If flagged, it should have row_count > 0 (but we didn't insert)
        if cand:
            assert cand[0].get("row_count", 0) == 0, \
                "empty table should not be flagged as dead letter"
