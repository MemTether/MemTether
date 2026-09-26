# -*- coding: utf-8 -*-
"""
langmem_exchange.py — LangMem 适配器（2026-09-26，零外部依赖）

定位：
  把 LangMem（LangChain 生态记忆库）的导出导入 MemTether Memory Exchange v1。

诚实边界：
  1. LangMem 的数据通常在 LangSmith 后端，本适配器接受本地导出的
     JSON/JSONL（{"memories":[...]} / [{"content":"..."}] 等常见形态）。
  2. LangMem 没有双时间轴/supersession/Q-Value，导入时显式回填，不伪造。

用法：
  python langmem_exchange.py langmem-to-mt --from langmem.json --out exchange.json
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


def _read_langmem(path):
    text = open(path, "r", encoding="utf-8").read().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
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
        for key in ("content", "text", "memory", "fact", "value"):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                return v
    raise ValueError(f"no content field in record: {list(row) if isinstance(row, dict) else type(row)}")


def langmem_to_exchange(src, out, source="langmem", pii_redact=True):
    rows = _read_langmem(src)
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
        created = meta.get("created_at") or meta.get("timestamp")
        recorded = mx._norm_time(created) or now
        facts.append({
            "uid": meta.get("id") or meta.get("uid") or f"fact-langmem-{i:04d}",
            "type": "fact",
            "subject": meta.get("subject") or meta.get("user_id") or "user",
            "content": content,
            "status": "active",
            "superseded_by": None,
            "valid_from": recorded,
            "valid_to": None,
            "recorded_at": recorded,
            "invalidated_at": None,
            "temporal_source": "native" if mx._norm_time(created) else "backfilled",
            "source": source,
            "scope": "shared",
            "confidence": 0.8,
            "tags": "",
            "created_at": recorded,
            "updated_at": recorded,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "langmem_exchange",
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


def main(argv=None):
    p = argparse.ArgumentParser(description="LangMem -> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("langmem-to-mt")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--source", default="langmem")
    a.add_argument("--no-pii-redact", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "langmem-to-mt":
        data, out = langmem_to_exchange(args.src, args.out, source=args.source,
                                        pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
