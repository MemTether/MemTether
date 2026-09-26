# -*- coding: utf-8 -*-
"""
memtether_exchange.py — Memory Exchange Protocol Schema v1（M2，2026-09-26）

目标：
  把 MemTether 的治理语义（source / 双时间轴 / supersession / Q-Value）
  抽象成一套**跨系统记忆交换格式**，而不是一个只在本机可用的私有快照。

设计原则：
  1. 格式自描述：schema_version / exported_at / producer 都写在文件里
  2. 治理语义完整：supersession 链和双时间轴必须一起走，不能只搬"最新值"
  3. 完整性可校验：sha256 覆盖三条记录数组
  4. PII 安全：默认走 memtether_pipeline.sanitize_snapshot 脱敏
  5. 兼容旧快照：能读 memtether_export.py 的 Schema v1 快照

注意：
  这是 Schema v1 的第一个参考实现。字段语义见 docs/memory_exchange_schema.md。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import memtether_paths as _mp
    DEFAULT_DB = _mp.default_db()
except Exception:
    DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db")

try:
    import memtether_pipeline
    sanitize_snapshot = memtether_pipeline.sanitize_snapshot
except Exception:
    def sanitize_snapshot(data):
        return data, 0

SCHEMA_NAME = "memtether.memory_exchange"
SCHEMA_VERSION = 1

FACT_FIELDS = (
    "uid", "type", "subject", "content", "status", "superseded_by",
    "valid_from", "valid_to", "recorded_at", "invalidated_at",
    "temporal_source", "source", "scope", "confidence", "tags",
    "created_at", "updated_at", "q_value", "use_count",
)

ASSET_FIELDS = (
    "uid", "name", "aliases", "type", "status", "path", "entrypoint",
    "capabilities", "recipe_ids", "known_failures", "prerequisites",
    "last_verified_at", "verification_method", "source",
    "created_at", "updated_at", "q_value", "use_count",
)

SUPERSESSION_FIELDS = ("old_uid", "new_uid", "reason", "by_agent", "ts")


def _sha256(payload):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def export_exchange(db_path=None, out_path=None, include_retired=False,
                    producer="memtether", pii_redact=True):
    """导出 Memory Exchange Schema v1。

    返回 (data, out_path)。out_path=None 时只返回 data，不落盘。
    """
    db_path = db_path or DEFAULT_DB
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)

    status_filter = "('active','superseded')"
    if include_retired:
        status_filter = "('active','superseded','retired','quarantined')"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        facts = _rows(conn, f"SELECT {','.join(FACT_FIELDS)} FROM facts WHERE status IN {status_filter}")
        supersessions = _rows(conn, "SELECT old_uid,new_uid,reason,by_agent,ts FROM supersessions")
        assets = _rows(conn, f"SELECT {','.join(ASSET_FIELDS)} FROM tool_assets WHERE status IN {status_filter}")
    except sqlite3.Error as e:
        raise RuntimeError(f"schema mismatch or unreadable db: {e}") from e
    finally:
        conn.close()

    payload = {
        "facts": facts,
        "supersessions": supersessions,
        "tool_assets": assets,
    }
    data = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "producer": producer,
        "producer_db": os.path.abspath(db_path),
        "counts": {
            "facts": len(facts),
            "supersessions": len(supersessions),
            "tool_assets": len(assets),
        },
        **payload,
    }

    pii_n = 0
    if pii_redact:
        data, pii_n = sanitize_snapshot(data)
        data["pii_redacted"] = pii_n

    data["sha256"] = _sha256(payload)

    if out_path:
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data, out_path


def _norm_time(v):
    """把常见时间格式归一成 MemTether 的 'YYYY-MM-DD HH:MM:SS'。"""
    if v in (None, ""):
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _insert_fact(conn, row):
    uid = row.get("uid")
    content = row.get("content")
    if not uid or not content:
        return "skipped"
    exists = conn.execute("SELECT 1 FROM facts WHERE uid=?", (uid,)).fetchone()
    if exists:
        return "skipped"
    recorded_at = _norm_time(row.get("recorded_at")) or _norm_time(row.get("created_at")) or _norm_time(row.get("updated_at"))
    valid_from = _norm_time(row.get("valid_from")) or recorded_at
    updated_at = _norm_time(row.get("updated_at")) or recorded_at
    if not recorded_at:
        # 旧系统没有时间轴信息时，宁可显式标注，也不猜一个"现实成立时间"。
        return "needs_temporal"
    try:
        qv = float(row.get("q_value", 0.5))
    except (TypeError, ValueError):
        qv = 0.5
    try:
        uc = int(row.get("use_count", 0))
    except (TypeError, ValueError):
        uc = 0
    try:
        conf = float(row.get("confidence", 0.8))
    except (TypeError, ValueError):
        conf = 0.8
    conn.execute(
        """INSERT INTO facts
           (uid,type,subject,content,status,superseded_by,valid_from,valid_to,
            recorded_at,invalidated_at,temporal_source,source,scope,confidence,tags,
            created_at,updated_at,q_value,use_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, row.get("type") or "fact", row.get("subject") or "user",
            content, row.get("status") or "active", row.get("superseded_by"),
            valid_from, _norm_time(row.get("valid_to")),
            recorded_at, _norm_time(row.get("invalidated_at")),
            row.get("temporal_source") or "backfilled",
            row.get("source") or "unknown", row.get("scope") or "shared",
            conf, row.get("tags"), recorded_at, updated_at, qv, uc,
        ),
    )
    return "inserted"


def _insert_asset(conn, row):
    uid = row.get("uid")
    name = row.get("name")
    if not uid or not name:
        return "skipped"
    exists = conn.execute("SELECT 1 FROM tool_assets WHERE uid=?", (uid,)).fetchone()
    if exists:
        return "skipped"
    created = _norm_time(row.get("created_at"))
    updated = _norm_time(row.get("updated_at")) or created
    try:
        qv = float(row.get("q_value", 0.5))
    except (TypeError, ValueError):
        qv = 0.5
    try:
        uc = int(row.get("use_count", 0))
    except (TypeError, ValueError):
        uc = 0
    conn.execute(
        """INSERT INTO tool_assets
           (uid,name,aliases,type,status,path,entrypoint,capabilities,recipe_ids,
            known_failures,prerequisites,last_verified_at,verification_method,source,
            created_at,updated_at,q_value,use_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, name, row.get("aliases"), row.get("type") or "local_tool",
            row.get("status") or "active", row.get("path"), row.get("entrypoint"),
            row.get("capabilities"), row.get("recipe_ids"), row.get("known_failures"),
            row.get("prerequisites"), _norm_time(row.get("last_verified_at")),
            row.get("verification_method"), row.get("source") or "unknown",
            created, updated, qv, uc,
        ),
    )
    return "inserted"


def _ensure_schema(conn):
    """导入端最小建表；与 gateway.SCHEMA 的核心列保持一致。"""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             uid TEXT UNIQUE,
             type TEXT, subject TEXT, content TEXT NOT NULL,
             status TEXT DEFAULT 'active', superseded_by TEXT,
             valid_from TEXT, valid_to TEXT, recorded_at TEXT,
             invalidated_at TEXT, temporal_source TEXT,
             source TEXT, scope TEXT DEFAULT 'shared',
             confidence REAL DEFAULT 0.8, tags TEXT,
             created_at TEXT, updated_at TEXT,
             q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
             predicate TEXT
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tool_assets (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             uid TEXT UNIQUE, name TEXT, aliases TEXT,
             type TEXT, status TEXT DEFAULT 'active',
             path TEXT, entrypoint TEXT, capabilities TEXT,
             recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
             last_verified_at TEXT, verification_method TEXT,
             source TEXT, created_at TEXT, updated_at TEXT,
             q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS supersessions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             old_uid TEXT, new_uid TEXT, reason TEXT,
             by_agent TEXT, ts TEXT
           )"""
    )
    # 旧库补列（幂等）。
    for table, col, typ, dflt in (
        ("facts", "predicate", "TEXT", None),
        ("facts", "q_value", "REAL", "0.5"),
        ("facts", "use_count", "INTEGER", "0"),
        ("tool_assets", "q_value", "REAL", "0.5"),
        ("tool_assets", "use_count", "INTEGER", "0"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            suffix = f" DEFAULT {dflt}" if dflt is not None else ""
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}{suffix}")


def import_exchange(from_path, db_path=None, dry_run=False):
    """导入 Memory Exchange Schema v1。

    UID 冲突跳过；时间轴缺失的 fact 记为 needs_temporal，不猜 valid_from。
    """
    with open(from_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("schema_name") not in (None, SCHEMA_NAME):
        raise ValueError(f"not a memory exchange file: {data.get('schema_name')}")
    ver = int(data.get("schema_version", 0) or 0)
    if ver > SCHEMA_VERSION:
        return {"ok": False, "error": f"schema v{ver} > supported v{SCHEMA_VERSION}"}

    payload = {
        "facts": data.get("facts", []),
        "supersessions": data.get("supersessions", []),
        "tool_assets": data.get("tool_assets", []),
    }
    integrity_ok = _sha256(payload) == data.get("sha256")
    # 旧 memtether_export.py 快照的 sha256 是对 [facts, supersessions, tool_assets]
    # 这个 list 序列化算的；兼容读入，但不把它升级成新格式的伪装。
    if not integrity_ok and ver == 0:
        legacy_blob = json.dumps(
            [payload["facts"], payload["supersessions"], payload["tool_assets"]],
            ensure_ascii=False, sort_keys=True)
        integrity_ok = hashlib.sha256(legacy_blob.encode("utf-8")).hexdigest() == data.get("sha256")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        facts = _rows(conn, f"SELECT {','.join(FACT_FIELDS)} FROM facts WHERE status IN {status_filter}")
        supersessions = _rows(conn, "SELECT old_uid,new_uid,reason,by_agent,ts FROM supersessions")
        assets = _rows(conn, f"SELECT {','.join(ASSET_FIELDS)} FROM tool_assets WHERE status IN {status_filter}")
        raise RuntimeError(f"schema mismatch or unreadable db: {e}") from e
    finally:
        conn.close()

    payload = {
        "facts": facts,
        "supersessions": supersessions,
        "tool_assets": assets,
    }
    data = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "producer": producer,
        "producer_db": os.path.abspath(db_path),
        "counts": {
            "facts": len(facts),
            "supersessions": len(supersessions),
            "tool_assets": len(assets),
        },
        **payload,
    }

    pii_n = 0
    if pii_redact:
        data, pii_n = sanitize_snapshot(data)
        data["pii_redacted"] = pii_n

    data["sha256"] = _sha256(payload)

    if out_path:
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data, out_path


def _norm_time(v):
    """把常见时间格式归一成 MemTether 的 'YYYY-MM-DD HH:MM:SS'。"""
    if v in (None, ""):
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _insert_fact(conn, row):
    uid = row.get("uid")
    content = row.get("content")
    if not uid or not content:
        return "skipped"
    exists = conn.execute("SELECT 1 FROM facts WHERE uid=?", (uid,)).fetchone()
    if exists:
        return "skipped"
    recorded_at = _norm_time(row.get("recorded_at")) or _norm_time(row.get("created_at")) or _norm_time(row.get("updated_at"))
    valid_from = _norm_time(row.get("valid_from")) or recorded_at
    updated_at = _norm_time(row.get("updated_at")) or recorded_at
    if not recorded_at:
        # 旧系统没有时间轴信息时，宁可显式标注，也不猜一个"现实成立时间"。
        return "needs_temporal"
    try:
        qv = float(row.get("q_value", 0.5))
    except (TypeError, ValueError):
        qv = 0.5
    try:
        uc = int(row.get("use_count", 0))
    except (TypeError, ValueError):
        uc = 0
    try:
        conf = float(row.get("confidence", 0.8))
    except (TypeError, ValueError):
        conf = 0.8
    conn.execute(
        """INSERT INTO facts
           (uid,type,subject,content,status,superseded_by,valid_from,valid_to,
            recorded_at,invalidated_at,temporal_source,source,scope,confidence,tags,
            created_at,updated_at,q_value,use_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, row.get("type") or "fact", row.get("subject") or "user",
            content, row.get("status") or "active", row.get("superseded_by"),
            valid_from, _norm_time(row.get("valid_to")),
            recorded_at, _norm_time(row.get("invalidated_at")),
            row.get("temporal_source") or "backfilled",
            row.get("source") or "unknown", row.get("scope") or "shared",
            conf, row.get("tags"), recorded_at, updated_at, qv, uc,
        ),
    )
    return "inserted"


def _insert_asset(conn, row):
    uid = row.get("uid")
    name = row.get("name")
    if not uid or not name:
        return "skipped"
    exists = conn.execute("SELECT 1 FROM tool_assets WHERE uid=?", (uid,)).fetchone()
    if exists:
        return "skipped"
    created = _norm_time(row.get("created_at"))
    updated = _norm_time(row.get("updated_at")) or created
    try:
        qv = float(row.get("q_value", 0.5))
    except (TypeError, ValueError):
        qv = 0.5
    try:
        uc = int(row.get("use_count", 0))
    except (TypeError, ValueError):
        uc = 0
    conn.execute(
        """INSERT INTO tool_assets
           (uid,name,aliases,type,status,path,entrypoint,capabilities,recipe_ids,
            known_failures,prerequisites,last_verified_at,verification_method,source,
            created_at,updated_at,q_value,use_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uid, name, row.get("aliases"), row.get("type") or "local_tool",
            row.get("status") or "active", row.get("path"), row.get("entrypoint"),
            row.get("capabilities"), row.get("recipe_ids"), row.get("known_failures"),
            row.get("prerequisites"), _norm_time(row.get("last_verified_at")),
            row.get("verification_method"), row.get("source") or "unknown",
            created, updated, qv, uc,
        ),
    )
    return "inserted"


def _ensure_schema(conn):
    """导入端最小建表；与 gateway.SCHEMA 的核心列保持一致。"""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             uid TEXT UNIQUE,
             type TEXT, subject TEXT, content TEXT NOT NULL,
             status TEXT DEFAULT 'active', superseded_by TEXT,
             valid_from TEXT, valid_to TEXT, recorded_at TEXT,
             invalidated_at TEXT, temporal_source TEXT,
             source TEXT, scope TEXT DEFAULT 'shared',
             confidence REAL DEFAULT 0.8, tags TEXT,
             created_at TEXT, updated_at TEXT,
             q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0,
             predicate TEXT
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tool_assets (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             uid TEXT UNIQUE, name TEXT, aliases TEXT,
             type TEXT, status TEXT DEFAULT 'active',
             path TEXT, entrypoint TEXT, capabilities TEXT,
             recipe_ids TEXT, known_failures TEXT, prerequisites TEXT,
             last_verified_at TEXT, verification_method TEXT,
             source TEXT, created_at TEXT, updated_at TEXT,
             q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS supersessions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             old_uid TEXT, new_uid TEXT, reason TEXT,
             by_agent TEXT, ts TEXT
           )"""
    )
    # 旧库补列（幂等）。
    for table, col, typ, dflt in (
        ("facts", "predicate", "TEXT", None),
        ("facts", "q_value", "REAL", "0.5"),
        ("facts", "use_count", "INTEGER", "0"),
        ("tool_assets", "q_value", "REAL", "0.5"),
        ("tool_assets", "use_count", "INTEGER", "0"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            suffix = f" DEFAULT {dflt}" if dflt is not None else ""
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}{suffix}")


def import_exchange(from_path, db_path=None, dry_run=False):
    """导入 Memory Exchange Schema v1。

    UID 冲突跳过；时间轴缺失的 fact 记为 needs_temporal，不猜 valid_from。
    """
    with open(from_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("schema_name") not in (None, SCHEMA_NAME):
        raise ValueError(f"not a memory exchange file: {data.get('schema_name')}")
    ver = int(data.get("schema_version", 0) or 0)
    if ver > SCHEMA_VERSION:
        return {"ok": False, "error": f"schema v{ver} > supported v{SCHEMA_VERSION}"}

    payload = {
        "facts": data.get("facts", []),
        "supersessions": data.get("supersessions", []),
        "tool_assets": data.get("tool_assets", []),
    }
    integrity_ok = _sha256(payload) == data.get("sha256")
    # 旧 memtether_export.py 快照的 sha256 是对 [facts, supersessions, tool_assets]
    # 这个 list 序列化算的；兼容读入，但不把它升级成新格式的伪装。
    if not integrity_ok and ver == 0:
        legacy_blob = json.dumps(
            [payload["facts"], payload["supersessions"], payload["tool_assets"]],
            ensure_ascii=False, sort_keys=True)
        integrity_ok = hashlib.sha256(legacy_blob.encode("utf-8")).hexdigest() == data.get("sha256")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        stats = {
            "facts_inserted": 0, "facts_skipped": 0, "facts_needs_temporal": 0,
            "supersessions_inserted": 0, "supersessions_skipped": 0,
            "tool_assets_inserted": 0, "tool_assets_skipped": 0,
            "integrity_ok": integrity_ok, "dry_run": bool(dry_run),
        }
        for row in payload["facts"]:
            action = _insert_fact(conn, row) if not dry_run else (
                "skipped" if conn.execute("SELECT 1 FROM facts WHERE uid=?", (row.get("uid"),)).fetchone()
                else ("needs_temporal" if not (row.get("recorded_at") or row.get("created_at") or row.get("updated_at")) else "inserted")
            )
            stats[f"facts_{action}"] = stats.get(f"facts_{action}", 0) + 1

        for row in payload["tool_assets"]:
            action = _insert_asset(conn, row) if not dry_run else (
                "skipped" if conn.execute("SELECT 1 FROM tool_assets WHERE uid=?", (row.get("uid"),)).fetchone()
                else "inserted"
            )
            stats[f"tool_assets_{action}"] = stats.get(f"tool_assets_{action}", 0) + 1

        for row in payload["supersessions"]:
            old_uid = row.get("old_uid"); new_uid = row.get("new_uid")
            if not old_uid or not new_uid:
                continue
            exists = conn.execute(
                "SELECT 1 FROM supersessions WHERE old_uid=? AND new_uid=?",
                (old_uid, new_uid)).fetchone()
            if exists:
                stats["supersessions_skipped"] += 1
                continue
            if not dry_run:
                conn.execute(
                    "INSERT INTO supersessions (old_uid,new_uid,reason,by_agent,ts) VALUES (?,?,?,?,?)",
                    (old_uid, new_uid, row.get("reason"), row.get("by_agent"), row.get("ts")))
            stats["supersessions_inserted"] += 1

        if not dry_run:
            conn.commit()
        stats["ok"] = True
        return stats
    except sqlite3.Error as e:
        if not dry_run:
            conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="MemTether Memory Exchange Protocol v1")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export")
    e.add_argument("--db")
    e.add_argument("--out")
    e.add_argument("--include-retired", action="store_true")
    e.add_argument("--producer", default="memtether")
    e.add_argument("--no-pii-redact", action="store_true")

    i = sub.add_parser("import")
    i.add_argument("--from", dest="src", required=True)
    i.add_argument("--db")
    i.add_argument("--dry-run", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "export":
        data, out = export_exchange(
            db_path=args.db, out_path=args.out,
            include_retired=args.include_retired, producer=args.producer,
            pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "counts": data["counts"],
                          "pii_redacted": data.get("pii_redacted", 0),
                          "sha256": data["sha256"][:16] + "..."}, ensure_ascii=False))
    else:
        r = import_exchange(args.src, db_path=args.db, dry_run=args.dry_run)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
