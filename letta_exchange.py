# -*- coding: utf-8 -*-
"""
letta_exchange.py — Letta agent-file (.af) 适配器（2026-09-26，零外部依赖）

定位：
  把 Letta 的 agent-file (.af) JSON 格式导入 MemTether Memory Exchange v1。
  .af 文件是 Letta 的开放 agent 状态格式，包含 memory blocks。

诚实边界：
  1. .af 是 JSON 格式但 Letta 没有"通用记忆导出"——本适配器读的是
     .af 文件的 memory.blocks 段。
  2. Letta blocks 只有 label/value/preserve_on_migration，没有
     双时间轴/supersession/Q-Value。导入时显式回填，不伪造。
  3. preserve_on_migration=true 的 block 对应 MemTether 的 pin 语义。

用法：
  python letta_exchange.py letta-to-mt --from agent.af --out exchange.json
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


def _read_af(path):
    data = json.load(open(path, "r", encoding="utf-8"))
    # .af 可能是 {agent_state: {memory: {blocks: [...]}}} 或 {memory: {blocks: [...]}} 或 {blocks: [...]}
    if isinstance(data, dict):
        for key in ("blocks", "memory_blocks"):
            if isinstance(data.get(key), list):
                return data[key]
        memory = data.get("memory")
        if isinstance(memory, dict):
            for key in ("blocks", "memory_blocks"):
                if isinstance(memory.get(key), list):
                    return memory[key]
        agent_state = data.get("agent_state")
        if isinstance(agent_state, dict):
            memory = agent_state.get("memory")
            if isinstance(memory, dict):
                for key in ("blocks", "memory_blocks"):
                    if isinstance(memory.get(key), list):
                        return memory[key]
    raise ValueError("no memory blocks found in .af file")


def letta_to_exchange(src, out, source="letta", pii_redact=True):
    blocks = _read_af(src)
    now = _now()
    facts = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        label = b.get("label") or b.get("name") or f"block_{i}"
        value = b.get("value") or b.get("text") or ""
        if not value.strip():
            continue
        uid = b.get("id") or f"fact-letta-{label}-{i:04d}"
        preserve = b.get("preserve_on_migration", False)
        tags = "pin" if preserve else ""
        facts.append({
            "uid": uid,
            "type": "fact",
            "subject": label,
            "content": value,
            "status": "active",
            "superseded_by": None,
            "valid_from": now,
            "valid_to": None,
            "recorded_at": now,
            "invalidated_at": None,
            "temporal_source": "backfilled",
            "source": source,
            "scope": "shared",
            "confidence": 0.8,
            "tags": tags,
            "created_at": now,
            "updated_at": now,
            "q_value": 0.5,
            "use_count": 0,
        })

    data = {
        "schema_name": mx.SCHEMA_NAME,
        "schema_version": mx.SCHEMA_VERSION,
        "exported_at": _now(),
        "producer": "letta_exchange",
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
    p = argparse.ArgumentParser(description="Letta .af -> MemTether exchange adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("letta-to-mt")
    a.add_argument("--from", dest="src", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--source", default="letta")
    a.add_argument("--no-pii-redact", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "letta-to-mt":
        data, out = letta_to_exchange(args.src, args.out, source=args.source,
                                      pii_redact=not args.no_pii_redact)
        print(json.dumps({"ok": True, "out": out, "facts": data["counts"]["facts"],
                          "pii_redacted": data.get("pii_redacted", 0)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
