"""Core MemTether functionality tests."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestExchange:
    """Test Memory Exchange Schema v2 roundtrip."""

    def test_export_import_roundtrip(self, tmp_db, tmp_path):
        """Export from source DB and import into target DB — facts should match."""
        from memtether_exchange import export_exchange, import_exchange
        import sqlite3

        # Ensure a fact exists (fixture may not have inserted one)
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT OR IGNORE INTO facts (uid, content, type, source, recorded_at, valid_from) "
            "VALUES ('rt-test-1', 'roundtrip test content', 'fact', 'test', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.commit()
        conn.close()

        out_path = str(tmp_path / "exchange.json")
        data, out = export_exchange(db_path=tmp_db, out_path=out_path)
        assert data["schema_version"] >= 1
        assert data["counts"]["facts"] >= 1

        target_db = str(tmp_path / "target.db")
        import sqlite3
        conn = sqlite3.connect(target_db)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
                type TEXT, subject TEXT, content TEXT NOT NULL,
                status TEXT DEFAULT 'active', superseded_by TEXT,
                valid_from TEXT, valid_to TEXT, recorded_at TEXT,
                invalidated_at TEXT, temporal_source TEXT, source TEXT,
                scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8,
                tags TEXT, created_at TEXT, updated_at TEXT,
                q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0, predicate TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, name TEXT,
                aliases TEXT, type TEXT, status TEXT DEFAULT 'active', path TEXT,
                entrypoint TEXT, capabilities TEXT, recipe_ids TEXT,
                known_failures TEXT, prerequisites TEXT, last_verified_at TEXT,
                verification_method TEXT, source TEXT, created_at TEXT, updated_at TEXT,
                q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS supersessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, old_uid TEXT, new_uid TEXT,
                reason TEXT, by_agent TEXT, ts TEXT
            );
        """)
        conn.commit()
        conn.close()

        stats = import_exchange(out_path, db_path=target_db)
        assert stats["ok"] is True
        assert stats["facts_inserted"] >= 1

    def test_schema_v2_fields(self, tmp_db, tmp_path):
        """Schema v2 export should include consent and sync when provided."""
        from memtether_exchange import export_exchange

        consent = {"granted_by": "user-test", "scope": "read_write", "purpose": "test"}
        sync = {"watermark": "2026-01-01T00:00:00Z", "mode": "incremental"}
        out_path = str(tmp_path / "exchange_v2.json")
        data, _ = export_exchange(db_path=tmp_db, out_path=out_path, consent=consent, sync=sync)
        assert data["schema_version"] == 2
        assert data.get("consent") == consent
        assert data.get("sync") == sync

    def test_v3_rejected(self, tmp_db, tmp_path):
        """Schema v3 files should be rejected by v2 importer."""
        from memtether_exchange import import_exchange
        import json

        payload = {"facts": [], "supersessions": [], "tool_assets": []}
        data = {
            "schema_name": "memtether.memory_exchange",
            "schema_version": 3,
            "facts": [], "supersessions": [], "tool_assets": [],
            "sha256": "fake",
        }
        v3_path = tmp_path / "v3.json"
        with open(v3_path, "w") as f:
            json.dump(data, f)
        stats = import_exchange(str(v3_path))
        assert stats.get("ok") is False
        assert "schema v3" in stats.get("error", "")


class TestGovernance:
    """Test governance conflict detection."""

    def test_conflict_detection_basic(self):
        from governance import detect_explicit_conflicts
        # This is a smoke test — actual conflict data needed for full test
        assert callable(detect_explicit_conflicts)
