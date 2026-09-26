# -*- coding: utf-8 -*-
"""
demo_exchange_5adapters.py — 5 系统记忆统一导入 + 冲突检测端到端 demo（2026-09-26）

证明 MemTether 的 Memory Exchange Schema v1 可以作为"记忆数据海关"：
  从 Mem0 / Zep / Letta / Graphiti / LangMem 五个异构系统各导入合成数据，
  全部进入同一个临时 SQLite 库 → 检测跨系统冲突 → 自动退役旧条。

用法：
  python scripts/demo_exchange_5adapters.py [--keep]
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import memtether_exchange as mx          # noqa: E402
import mem0_exchange as m0x              # noqa: E402
import zep_exchange as zpx               # noqa: E402
import letta_exchange as ltx             # noqa: E402
import graphiti_exchange as gtx          # noqa: E402
import langmem_exchange as lmx           # noqa: E402
import governance                        # noqa: E402


def _ts(minutes_ago: int) -> str:
    t = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def build_all_sources(tmp):
    """构造 5 个系统的合成导出文件，返回 {name: exchange_path}。"""
    paths = {}

    # Mem0: 5 条正常 + 1 组跨系统冲突（OPENAI_API_KEY）
    mem0_rows = [
        {"id": "mem0-001", "memory": "demo: 用户偏好深色主题。", "created_at": _ts(120)},
        {"id": "mem0-002", "memory": "demo: 项目使用 Python 3.12。", "created_at": _ts(90)},
        {"id": "mem0-003", "memory": "demo: 数据库用 PostgreSQL 16。", "created_at": _ts(60)},
        {"id": "mem0-004", "memory": "OPENAI_API_KEY 可用，余额充足。", "created_at": _ts(120)},
        {"id": "mem0-005", "memory": "demo: 部署在 AWS ap-northeast-1。", "created_at": _ts(30)},
    ]
    p = os.path.join(tmp, "mem0.json")
    json.dump(mem0_rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    m0x.mem0_to_exchange(p, p + ".ex", source="mem0", pii_redact=False)
    paths["mem0"] = p + ".ex"

    # Zep: 3 条正常 + 1 条与 Mem0 冲突（OPENAI_API_KEY 更新时间更近）
    zep_rows = [
        {"uuid": "zep-001", "fact": "demo: 用户 timezone 是 Asia/Shanghai。", "created_at": _ts(100)},
        {"uuid": "zep-002", "fact": "demo: 前端用 React 19。", "created_at": _ts(80)},
        {"uuid": "zep-003", "fact": "OPENAI_API_KEY 已失效（401），需要更换。", "created_at": _ts(20)},
    ]
    p = os.path.join(tmp, "zep.json")
    json.dump(zep_rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    zpx.zep_to_exchange(p, p + ".ex", source="zep", pii_redact=False)
    paths["zep"] = p + ".ex"

    # Letta: 2 blocks
    letta_data = {
        "agent_state": {
            "memory": {
                "blocks": [
                    {"id": "blk-human", "label": "human", "value": "demo: 用户是全栈开发者。", "preserve_on_migration": True},
                    {"id": "blk-persona", "label": "persona", "value": "demo: 助手负责代码审查。", "preserve_on_migration": False},
                ]
            }
        }
    }
    p = os.path.join(tmp, "letta.af")
    json.dump(letta_data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    ltx.letta_to_exchange(p, p + ".ex", source="letta", pii_redact=False)
    paths["letta"] = p + ".ex"

    # Graphiti: 3 条图 facts
    graphiti_rows = [
        {"uuid": "gt-001", "fact": "demo: Service Alpha 依赖 PostgreSQL 16。", "valid_at": _ts(200)},
        {"uuid": "gt-002", "fact": "demo: Deploy pipeline uses GitHub Actions.", "valid_at": _ts(150)},
        {"uuid": "gt-003", "fact": "demo: Monitoring via Grafana + Prometheus.", "valid_at": _ts(100)},
    ]
    p = os.path.join(tmp, "graphiti.json")
    json.dump(graphiti_rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    gtx.graphiti_to_exchange(p, p + ".ex", source="graphiti", pii_redact=False)
    paths["graphiti"] = p + ".ex"

    # LangMem: 2 条
    langmem_rows = {"memories": [
        {"id": "lm-001", "content": "demo: 用户偏好中文回复。", "created_at": _ts(70)},
        {"id": "lm-002", "content": "demo: 代码风格遵循 PEP 8。"},
    ]}
    p = os.path.join(tmp, "langmem.json")
    json.dump(langmem_rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    lmx.langmem_to_exchange(p, p + ".ex", source="langmem", pii_redact=False)
    paths["langmem"] = p + ".ex"

    return paths


def main():
    keep = "--keep" in sys.argv
    tmp = tempfile.mkdtemp(prefix="mt_demo_5adapters_", dir="E:/Temp")
    summary = {"tmp_dir": tmp, "adapters": [], "steps": []}

    try:
        sources = build_all_sources(tmp)
        demo_db = os.path.join(tmp, "unified.db")

        total_inserted = 0
        for name, path in sources.items():
            ex = json.load(open(path, encoding="utf-8"))
            stats = mx.import_exchange(path, demo_db)
            assert stats.get("ok"), f"{name}: {stats}"
            total_inserted += stats["facts_inserted"]
            summary["adapters"].append({
                "name": name, "facts_in_file": ex["counts"]["facts"],
                "inserted": stats["facts_inserted"], "skipped": stats["facts_skipped"]})

        summary["steps"].append({"total_facts_inserted": total_inserted})

        # 冲突检测：OPENAI_API_KEY 跨系统冲突（Mem0 说可用@120min ago，Zep 说失效@20min ago）
        governance.DB = demo_db
        conflicts = governance.detect_explicit_conflicts(90)
        assert len(conflicts) == 1, (
            f"expected 1 cross-system conflict (OPENAI_API_KEY), got {len(conflicts)}: {conflicts}")
        assert conflicts[0]["entity"] == "openai_api", (
            f"expected entity=openai, got: {conflicts[0]['entity']}")
        summary["steps"].append({"conflicts_detected": [
            {"entity": c["entity"], "retire": c["suggest_retire"], "keep": c["keep"]}
            for c in conflicts]})

        # 自动退役
        for c in conflicts:
            reason = f"{c['entity']} 状态以新条为准，旧条自动退役（跨系统冲突）"
            r = governance.retire(c["suggest_retire"], by_uid=c["keep"],
                                  reason=reason, apply=True, force=True)
            assert r.get("ok"), r

        conflicts2 = governance.detect_explicit_conflicts(90)
        assert conflicts2 == [], f"re-detect not clean: {conflicts2}"
        summary["steps"].append({"re_detect_conflicts": len(conflicts2)})

        # 统计各 source 的条数
        conn = sqlite3.connect(demo_db)
        conn.row_factory = sqlite3.Row
        by_source = {}
        for row in conn.execute("SELECT source, COUNT(*) as n FROM facts GROUP BY source"):
            by_source[row["source"]] = row["n"]
        conn.close()
        summary["steps"].append({"facts_by_source": by_source})

        summary["ok"] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("PASS")
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        return 0
    except Exception as e:
        summary["ok"] = False
        summary["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
