# -*- coding: utf-8 -*-
"""
zep_exchange.py — Zep 适配器（2026-09-26，零外部依赖）

定位：
  把 Zep 的记忆导出（JSON / JSONL）导入 MemTether Memory Exchange v1，
  或者把 MemTether Memory Exchange v1 转成 Zep 可消费的简单 JSON。

诚实边界：
  1. Zep 没有一个"官方通用记忆导出协议"，本适配器支持常见的
     {"memories":[...]} / JSONL / v2 API GET /sessions/{id}/memories 响应 三种形态。
  2. Zep v2 API 有 created_at / fact / role 等字段，导入时尽量保留；
     没有的治理字段显式回填，不伪造。
  3. Zep 的 graph 三层（episodic/semantic/community）结构不在本适配器范围内，
     只适配 memories 级别的条目。

用法：
  python zep_exchange.py zep-to-mt --from zep.json --out exchange.json
  python zep_exchange.py mt-to-zep --from exchange.json --out zep.json
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


def _read_zep(path):
    text = open(path, "r", encoding="utf-8").read().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # JSONL
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("memories", "items", "data", "results", "facts"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def _as_content(row):
    if isinstance(row, str):
        return row
    if isinstance(row, dict):
        for key in ("fact", "memory", "content", "text", "message", "value"):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                return v
        # Zep v2 嵌套 message
        msg = row.get("message")
        if isinstance(msg, dict):
            for key in ("content", "text"):
                v = msg.get(key)
                if isinstance(v, str) and v.strip():
                    return v
    raise ValueError(f"no content field in record: {list(row) if isinstance(row, dict) else type(row)}")


def _uid(i, content):
    return f"fact-zep-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-{i:04d}-{abs(hash(content)) % 100000:05d}"


def zep_to_exchange(src, out, source="zep", pii_redact=True):
    rows = _read_zep(src)
    now = _now()
    facts = []
    for i, row in enumerate(rows):
        try:
            content = _as_content(row).strip()
        except ValueError as e:
            print(f"[skip] {e}")
            continue
        if not content:
            continue
        meta = row if isinstance(row, dict) else {}
        created = meta.get("created_at") or meta.get("timestamp") or meta.get("ts")
        valid_from = meta.get("valid_at") or created
        valid_to = meta.get("invalid_at")
        recorded = mx._norm_time(created) or now
        facts.append({
            "uid": meta.get("uuid") or meta.get("id") or meta.get("uid") or _uid(i, content),
            "type": meta.get("type") or "fact",
            "subject": meta.get("subject") or meta.get("user_id") or "user",
            "content": content,
            "status": "active",
            "superseded_by": meta.get("invalid_at") and None,
            "valid_from": mx._norm_time(valid_from) or recorded,
            "valid_to": mx._norm_time(valid_to),
            "recorded_at": recorded,
            "invalidated_at": mx._norm_time(valid_to),
            "temporal_source": "native" if mx._norm_time(created) else "backfilled",
            "source": meta.get("source") or source,
            "scope": meta.get("scope") or "shared",
            "confidence": 0.8,
            "tags": meta.get("labels") if isinstance(meta.get("labels"), list) else meta.get("tags", ""),
            "created_at": recorded,
            "updated_at": recorded,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "zep_exchange",
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
    data["sha256"] = mx._sha256({"facts": facts, "supersessions": [], "tool_assets": []})
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data, out


def mt_to_zep(src, out):
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    memories = []
    for row in data.get("facts", []):
        item = {
            "fact": row.get("content", ""),
            "user_id": row.get("subject") or "user",
        }
        for sk, dk in (
            ("uid", "memtether_uid"), ("valid_from", "memtether_valid_from"),
            ("valid_to", "memtether_valid_to"), ("recorded_at", "memtether_recorded_at"),
            ("q_value", "memtether_q_value"), ("source", "memtether_source"),
        ):
            if row.get(sk) is not None:
                item[dk] = row[sk]
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
    p = argparse.ArgumentParser(description="Zep <-> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("zep-to-mt")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--source", default="zep")
    a.add_argument("--no-pii-redact", action="store_true")
    b = sub.add_parser("mt-to-zep")
    b.add_argument("--from", dest="src", required=True)
    b.add_argument("--out", required=True)
    args = p.parse_args(argv)

    if args.cmd == "zep-to-mt":
        data, out = zep_to_exchange(args.src, args.out, source=args.source,
                                    pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))
    else:
        payload, out = mt_to_zep(args.src, args.out)
        print(json.dumps({"ok": True, "out": out, "memories": len(payload["memories"])},
                         ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
