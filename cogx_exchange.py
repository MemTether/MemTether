# -*- coding: utf-8 -*-
"""
cogx_exchange.py — COGX (cognee eXchange) 适配器

定位：
  把 COGX 归档目录（manifest.json + JSONL per kind）导入 MemTether Exchange Schema v2，
  或者把 MemTether Exchange Schema v2 转成 COGX 可消费的归档目录。

COGX 格式（cognee 0.1）：
  一个目录，内含 manifest.json + documents.jsonl / episodes.jsonl /
  entities.jsonl / facts.jsonl / memories.jsonl / memory_blocks.jsonl。
  每条记录是 JSON，带 kind 字段区分类型。

诚实边界：
  1. COGX 没有双时间轴（只有 created_at/updated_at），没有 supersession 链。
     导入时 recorded_at 用 COGX 的 created_at/updated_at（有则 native，无则 backfilled），
     supersessions 一律空。
  2. COGX 没有 Q-Value。q_value 一律 0.5，不伪造。
  3. COGX 的 kind 不是全部一对一映射 MemTether type——映射表在 _KIND_MAP。

用法：
  python cogx_exchange.py cogx-to-mt --from <cogx_dir> --out exchange.json
  python cogx_exchange.py mt-to-cogx --from exchange.json --out <cogx_dir>
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import memtether_exchange as mx
except Exception:
    import memtether_exchange as mx

COGX_VERSION = "0.1"

# COGX kind -> MemTether type mapping
_KIND_MAP = {
    "memory": "fact",           # Mem0-style short fact text
    "fact": "fact",             # triplet fact, use fact_text
    "document": "fact",         # raw text
    "episode": "experience",    # conversation turns
    "entity": "fact",           # entity description
    "memory_block": "fact",     # Letta-style block value
}

# Record file names per COGX spec
_RECORD_FILES = {
    "document": "documents.jsonl",
    "episode": "episodes.jsonl",
    "entity": "entities.jsonl",
    "fact": "facts.jsonl",
    "memory": "memories.jsonl",
    "memory_block": "memory_blocks.jsonl",
}

_MANIFEST = "manifest.json"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_jsonl(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def _read_cogx_dir(cogx_dir):
    """读 COGX 归档目录，返回 (manifest, records)。

    records 是按目录文件读取的所有 typed 记录（dict）。
    """
    if not os.path.isdir(cogx_dir):
        raise FileNotFoundError(f"COGX archive dir not found: {cogx_dir}")
    manifest_path = os.path.join(cogx_dir, _MANIFEST)
    manifest = None
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        ver = str(manifest.get("cogx_version", COGX_VERSION))
        if ver.split(".")[0] > COGX_VERSION.split(".")[0]:
            raise ValueError(
                f"COGX version {ver} newer than supported {COGX_VERSION}")
    records = []
    for file_name in _RECORD_FILES.values():
        records.extend(_read_jsonl(os.path.join(cogx_dir, file_name)))
    # raw_nodes.jsonl (full fidelity graph nodes) — treat as raw fact content
    raw_path = os.path.join(cogx_dir, "nodes.jsonl")
    if os.path.isfile(raw_path):
        for row in _read_jsonl(raw_path):
            row.setdefault("kind", "raw_node")
            records.append(row)
    return manifest, records


def _content_from_record(rec):
    """从 COGX record 提取 content 文本。"""
    kind = rec.get("kind", "")
    if kind == "memory":
        return rec.get("content", "")
    if kind == "fact":
        # Triplet fact: use fact_text, or fallback to subject-predicate-object
        txt = rec.get("fact_text")
        if txt and str(txt).strip():
            return str(txt).strip()
        parts = [rec.get("subject_ref", ""), rec.get("predicate", ""), rec.get("object_ref", "")]
        return " ".join(p for p in parts if p).strip()
    if kind == "document":
        return rec.get("content", "")
    if kind == "episode":
        # Serialize turns into readable text
        turns = rec.get("turns", [])
        lines = []
        for t in turns:
            role = t.get("role", "user")
            c = t.get("content", "")
            lines.append(f"{role}: {c}")
        return "\n".join(lines) if lines else rec.get("title", "")
    if kind == "entity":
        desc = rec.get("description") or ""
        name = rec.get("name", "")
        etype = rec.get("entity_type", "")
        parts = [name]
        if etype:
            parts.append(f"({etype})")
        if desc:
            parts.append(f": {desc}")
        return " ".join(parts).strip()
    if kind == "memory_block":
        label = rec.get("label", "")
        val = rec.get("value", "")
        return f"{label}: {val}" if label else val
    if kind == "raw_node":
        props = rec.get("properties", {})
        # Try common content fields in raw node properties
        for k in ("content", "text", "value", "description", "name"):
            v = props.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return json.dumps(props, ensure_ascii=False)
    return ""


def _uid(rec, i):
    ext_id = rec.get("external_id")
    if ext_id:
        return f"fact-cogx-{ext_id}"
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    content = _content_from_record(rec)
    return f"fact-cogx-{ts}-{i:04d}-{abs(hash(content)) % 100000:05d}"


def cogx_to_exchange(cogx_dir, out, source="cogx", pii_redact=True):
    """COGX 归档 -> MemTether Exchange Schema v2 JSON 文件。"""
    manifest, records = _read_cogx_dir(cogx_dir)
    now = _now()
    facts = []
    supersessions = []
    for i, rec in enumerate(records):
        content = _content_from_record(rec)
        if not content or not str(content).strip():
            continue
        content = str(content).strip()
        kind = rec.get("kind", "memory")
        mt_type = _KIND_MAP.get(kind, "fact")
        scope_info = rec.get("scope") or {}
        subject = "user"
        if isinstance(scope_info, dict):
            # COGX scope has user_id / agent_id / session_id / run_id
            uid_val = scope_info.get("user_id") or scope_info.get("agent_id")
            if uid_val:
                subject = str(uid_val)
        meta = rec.get("metadata") or {}
        created = rec.get("created_at") or rec.get("updated_at") or meta.get("created_at")
        recorded = mx._norm_time(created) or now
        tags = ""
        if isinstance(meta.get("tags"), list):
            tags = ",".join(meta["tags"])
        elif isinstance(meta.get("categories"), list):
            tags = ",".join(meta["categories"])
        facts.append({
            "uid": _uid(rec, i),
            "type": mt_type,
            "subject": subject,
            "content": content,
            "status": "active",
            "superseded_by": None,
            "valid_from": recorded,
            "valid_to": None,
            "recorded_at": recorded,
            "invalidated_at": None,
            "temporal_source": "native" if mx._norm_time(created) else "backfilled",
            "source": rec.get("external_system") or source,
            "scope": meta.get("scope") or "shared",
            "confidence": 0.8,
            "tags": tags,
            "created_at": recorded,
            "updated_at": recorded,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "cogx_exchange",
        "producer_db": os.path.abspath(cogx_dir),
        "counts": {"facts": len(facts), "supersessions": 0, "tool_assets": 0},
        "facts": facts,
        "supersessions": supersessions,
        "tool_assets": [],
    }
    pii_n = 0
    if pii_redact:
        data, pii_n = mx.sanitize_snapshot(data)
        data["pii_redacted"] = pii_n
    data["sha256"] = mx._sha256({
        "facts": facts, "supersessions": supersessions, "tool_assets": []})
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data, out


def mt_to_cogx(src, cogx_dir):
    """MemTether Exchange Schema v2 -> COGX 归档目录。

    v2 专有字段（producer / content_blake3 / consent / sync）在 COGX 中
    没有对应字段——不伪造，静默丢弃，在 manifest.notes 里标注。
    """
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema_name") != mx.SCHEMA_NAME:
        raise ValueError(f"not a memory exchange file: {data.get('schema_name')}")

    cogx_dir = os.path.abspath(cogx_dir)
    os.makedirs(cogx_dir, exist_ok=True)

    # Write per-kind JSONL files
    kind_counts = {}
    kind_files = {}
    for kind in _RECORD_FILES:
        kind_files[kind] = open(
            os.path.join(cogx_dir, _RECORD_FILES[kind]), "w", encoding="utf-8")

    notes = []
    for row in data.get("facts", []):
        # Map MemTether type back to COGX kind
        mt_type = row.get("type", "fact")
        if mt_type == "experience":
            kind = "episode"
        elif mt_type in ("fact", "decision", "incident", "procedure", "preference", "environment"):
            kind = "memory"  # Mem0-style short fact
        else:
            kind = "memory"
        # Map to COGX record shape
        rec = {
            "kind": kind,
            "external_system": row.get("source") or "memtether",
            "external_id": row.get("uid") or "",
            "scope": {"user_id": row.get("subject") or "user"},
            "created_at": row.get("created_at") or row.get("recorded_at"),
            "updated_at": row.get("updated_at"),
            "metadata": {
                "memtether_type": mt_type,
                "memtether_status": row.get("status", "active"),
                "memtether_q_value": row.get("q_value", 0.5),
                "memtether_tags": row.get("tags", ""),
                "memtether_valid_from": row.get("valid_from"),
                "memtether_valid_to": row.get("valid_to"),
                "memtether_recorded_at": row.get("recorded_at"),
                "memtether_temporal_source": row.get("temporal_source", "backfilled"),
            },
            "content": row.get("content", ""),
            "categories": [t.strip() for t in (row.get("tags") or "").split(",") if t.strip()],
        }
        # Clean None values from metadata to keep JSONL clean
        rec["metadata"] = {k: v for k, v in rec["metadata"].items() if v is not None}
        kind_files[kind].write(json.dumps(rec, ensure_ascii=False) + "\n")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    for handle in kind_files.values():
        handle.close()

    # Manifest
    manifest = {
        "cogx_version": COGX_VERSION,
        "source_system": "memtether",
        "exported_at": _now(),
        "counts": kind_counts,
        "embedding_model": None,
        "migration_revision": None,
        "notes": notes,
    }
    # v2 fields: note if they exist (COGX can't carry them natively)
    if data.get("consent"):
        notes.append("memtether consent field present but not natively representable in COGX 0.1")
    if data.get("sync"):
        notes.append("memtether sync watermark present but not natively representable in COGX 0.1")
    if data.get("meta", {}).get("warnings"):
        notes.extend(data["meta"]["warnings"])

    manifest_path = os.path.join(cogx_dir, _MANIFEST)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest, cogx_dir


def main(argv=None):
    p = argparse.ArgumentParser(description="COGX (cognee) <-> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("cogx-to-mt")
    a.add_argument("--from", dest="src", required=True, help="COGX archive directory")
    a.add_argument("--out", required=True, help="output Exchange JSON path")
    a.add_argument("--source", default="cogx")
    a.add_argument("--no-pii-redact", action="store_true")
    b = sub.add_parser("mt-to-cogx")
    b.add_argument("--from", dest="src", required=True, help="input Exchange JSON path")
    b.add_argument("--out", required=True, help="output COGX archive directory")
    args = p.parse_args(argv)

    if args.cmd == "cogx-to-mt":
        data, out = cogx_to_exchange(args.src, args.out, source=args.source,
                                     pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))
    else:
        manifest, cogx_dir = mt_to_cogx(args.src, args.out)
        print(json.dumps({"ok": True, "out": cogx_dir, "counts": manifest["counts"],
                          "notes": manifest.get("notes", [])}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
