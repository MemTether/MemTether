"""Shared fixtures for MemTether test suite."""
import os
import tempfile
import sqlite3
import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary MemTether database with the standard schema."""
    db_path = str(tmp_path / "test_memory.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE,
            type TEXT,
            subject TEXT,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            superseded_by TEXT,
            valid_from TEXT, valid_to TEXT,
            recorded_at TEXT, invalidated_at TEXT,
            temporal_source TEXT,
            source TEXT, scope TEXT DEFAULT 'shared',
            confidence REAL DEFAULT 0.8, tags TEXT,
            created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
            predicate TEXT
        );
        CREATE TABLE IF NOT EXISTS tool_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE, name TEXT, aliases TEXT,
            type TEXT, status TEXT DEFAULT 'active',
            path TEXT, entrypoint TEXT, capabilities TEXT,
            recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
            last_verified_at TEXT, verification_method TEXT,
            source TEXT, created_at TEXT, updated_at TEXT,
            q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS supersessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_uid TEXT, new_uid TEXT, reason TEXT,
            by_agent TEXT, ts TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sample_fact(tmp_db):
    """Insert a sample fact and return its uid."""
    conn = sqlite3.connect(tmp_db)
    uid = "test-fact-001"
    conn.execute(
        "INSERT INTO facts (uid, content, type, source, recorded_at, valid_from) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, "This is a test memory about Python 3.12", "fact", "test", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    )
    conn.commit()
    conn.close()
    return uid
