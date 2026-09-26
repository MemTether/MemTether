# -*- coding: utf-8 -*-
"""
graphiti_exchange.py — Graphiti 适配器（2026-09-26，零外部依赖）

定位：
  把 Graphiti 的图记忆导出（JSON）导入 MemTether Memory Exchange v1。

诚实边界：
  1. Graphiti 的数据在 Neo4j 图里，没有"官方一键导出全部记忆"的功能。
     本适配器接受 Graphiti REST API / MCP 的 search 返回结果或手动导出的
     edges JSON（[{"fact":"...","uuid":"...","valid_at":"...","invalid_at":"..."}] 形态）。
  2. Graphiti 的图结构（节点/边/community）在本适配器中被展平为 facts；
     实体关系（subject→predicate→object）压缩为 content 文本。
  3. Graphiti 的 valid_at/invalid_at 与 MemTether 的双时间轴同源（Zep 系），
     可以直接映射，不需要回填。

用法：
  python graphiti_exchange.py graphiti-to-mt --from graphiti.json --out exchange.json
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


def _read_graphiti(path):
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
        for key in ("edges", "facts", "memories", "items", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def _as_content(row):
    if isinstance(row, str):
        return row
    if isinstance(row, dict):
        for key in ("fact", "content", "text", "summary", "memory"):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                return v
    raise ValueError(f"no fact content in record: {list(row) if isinstance(row, dict) else type(row)}")


def graphiti_to_exchange(src, out, source="graphiti", pii_redact=True):
    rows = _read_graphiti(src)
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
        valid_at = meta.get("valid_at")
        invalid_at = meta.get("invalid_at")
        recorded = mx._norm_time(created) or mx._norm_time(valid_at) or now
        facts.append({
            "uid": meta.get("uuid") or meta.get("id") or f"fact-graphiti-{i:04d}",
            "type": "fact",
            "subject": meta.get("subject") or meta.get("source_node") or "user",
            "content": content,
            "status": "active",
            "superseded_by": None,
            "valid_from": mx._norm_time(valid_at) or recorded,
            "valid_to": mx._norm_time(invalid_at),
            "recorded_at": recorded,
            "invalidated_at": mx._norm_time(invalid_at),
            "temporal_source": "native" if (mx._norm_time(valid_at) or mx._norm_time(created)) else "backfilled",
            "source": source,
            "scope": "shared",
            "confidence": 0.8,
            "tags": meta.get("labels", "") if isinstance(meta.get("labels"), str) else ",".join(meta.get("labels", [])),
            "created_at": recorded,
            "updated_at": recorded,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "graphiti_exchange",
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
    p = argparse.ArgumentParser(description="Graphiti -> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("graphiti-to-mt")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--source", default="graphiti")
    a.add_argument("--no-pii-redact", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "graphiti-to-mt":
        data, out = graphiti_to_exchange(args.src, args.out, source=args.source,
                                         pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
