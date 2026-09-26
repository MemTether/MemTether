# -*- coding: utf-8 -*-
"""
mem0_exchange.py — Mem0 适配器（M2，2026-09-26，零外部依赖）

定位：
  把 Mem0 的标准导出（JSON / JSONL）导入 MemTether Memory Exchange v1，
  或者把 MemTether Memory Exchange v1 转成 Mem0 可消费的简单 JSON。

诚实边界：
  1. Mem0 没有一个"官方通用记忆导出协议"，本适配器支持的是常见的
     {"memories":[...]} / [{"memory":"...",...}] / JSONL 三种形态。
  2. Mem0 通常没有双时间轴 / supersession / Q-Value，导入时这些字段
     显式回填为 MemTether 的默认治理语义（recorded_at=导入时间，
     temporal_source="backfilled"），不会伪造它本来就有。
  3. 转出到 Mem0 的格式是简化交换格式，不承诺覆盖 Mem0 的所有云端字段。

用法：
  python mem0_exchange.py mem0-to-mt --from mem0.json --out exchange.json
  python mem0_exchange.py mt-to-mem0 --from exchange.json --out mem0.json
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


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json_or_jsonl(path):
    text = open(path, "r", encoding="utf-8").read().strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("memories", "items", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            if "results" in data and isinstance(data["results"], list):
                return data["results"]
            return [data]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = []
    for i, ln in enumerate(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i + 1} is not valid JSON: {e}") from e
    return out


def _as_memory(row):
    """把 Mem0 记录的各种字段名归一到 content。"""
    if isinstance(row, str):
        return row
    if not isinstance(row, dict):
        raise ValueError(f"unsupported memory record type: {type(row).__name__}")
    for key in ("memory", "content", "text", "value", "data", "fact"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v
    raise ValueError(f"no memory content field in record: {list(row)}")


def _uid(i, content):
    return f"fact-mem0-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{i:04d}-{abs(hash(content)) % 100000:05d}"


def mem0_to_exchange(src, out, db_path=None, source="mem0", pii_redact=True):
    rows = _read_json_or_jsonl(src)
    now = _now()
    facts = []
    for i, row in enumerate(rows):
        try:
            content = _as_memory(row).strip()
        except ValueError as e:
            print(f"[skip] {e}")
            continue
        if not content:
            continue
        meta = row if isinstance(row, dict) else {}
        # Mem0 的通用形态没有可靠时间轴字段；有 created_at 就用，没有才回填。
        created = meta.get("created_at") or meta.get("timestamp") or meta.get("ts")
        recorded = mx._norm_time(created) or now
        facts.append({
            "uid": meta.get("id") or meta.get("uid") or _uid(i, content),
            "type": meta.get("type") or "fact",
            "subject": meta.get("subject") or "user",
            "content": content,
            "status": "active",
            "superseded_by": None,
            "valid_from": mx._norm_time(meta.get("valid_from")) or recorded,
            "valid_to": mx._norm_time(meta.get("valid_to")),
            "recorded_at": recorded,
            "invalidated_at": mx._norm_time(meta.get("invalidated_at")),
            "temporal_source": "native" if mx._norm_time(created) else "backfilled",
            "source": meta.get("source") or source,
            "scope": meta.get("scope") or "shared",
            "confidence": 0.8,
            "tags": ",".join(meta.get("tags", [])) if isinstance(meta.get("tags"), list) else meta.get("tags", ""),
            "created_at": recorded,
            "updated_at": recorded,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "mem0_exchange",
        "producer_db": os.path.abspath(src),
        "counts": {"facts": len(facts), "supersessions": 0, "tool_assets": 0},
        "facts": facts,
        "supersessions": [],
        "tool_assets": [],
    }
    pii_n = 0
    if pii_redact:
        data, pii_n = mx.sanitize_snapshot(data)
        data["pii_redacted"] = pii_n
    data["sha256"] = mx._sha256({
        "facts": facts, "supersessions": [], "tool_assets": []})
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data, out


def mt_to_mem0(src, out):
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    memories = []
    for row in data.get("facts", []):
        item = {"memory": row.get("content", ""), "user_id": row.get("subject") or "user"}
        for src_key, dst_key in (
            ("uid", "memtether_uid"), ("type", "memtether_type"),
            ("valid_from", "memtether_valid_from"), ("valid_to", "memtether_valid_to"),
            ("recorded_at", "memtether_recorded_at"), ("q_value", "memtether_q_value"),
        ):
            if row.get(src_key) is not None:
                item[dst_key] = row[src_key]
        memories.append(item)
    payload = {"memories": memories,
               "schema_name": mx.SCHEMA_NAME,
               "schema_version": mx.SCHEMA_VERSION,
               "exported_at": _now()}
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload, out


def main(argv=None):
    p = argparse.ArgumentParser(description="Mem0 <-> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("mem0-to-mt")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--source", default="mem0")
    a.add_argument("--no-pii-redact", action="store_true")
    b = sub.add_parser("mt-to-mem0")
    b.add_argument("--from", dest="src", required=True)
    b.add_argument("--out", required=True)
    args = p.parse_args(argv)

    if args.cmd == "mem0-to-mt":
        data, out = mem0_to_exchange(args.src, args.out, source=args.source,
                                     pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))
    else:
        payload, out = mt_to_mem0(args.src, args.out)
        print(json.dumps({"ok": True, "out": out, "memories": len(payload["memories"])},
                         ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
