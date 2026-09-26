# -*- coding: utf-8 -*-
"""
demo_exchange_conflict.py — Memory Exchange 冲突检测端到端 demo（M2，2026-09-26）

流程（全部合成数据，零生产库污染）：
  1. 构造 Mem0 风格 JSON（14 正常 + 2 重复 UID + 2 组极性冲突）
  2. mem0_to_exchange → Memory Exchange Schema v1
  3. import_exchange → 临时 demo 库
  4. governance.detect_explicit_conflicts(90) → 应检出 2 组
  5. 对每组 retire(suggest_retire → keep, apply+force) → 复检应为 0
  6. 断言旧条 status=superseded / superseded_by=keep / valid_to 非空

用法：
  python scripts/demo_exchange_conflict.py [--keep]   # --keep 保留临时目录调试
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
import governance                        # noqa: E402


def _ts(minutes_ago: int) -> str:
    t = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def build_mem0_rows():
    """合成 Mem0 导出格式：正常 14 + 重复 2 + 冲突 4（两组）。"""
    rows = []
    # 正常记忆（避开 _STATUS_ASSERT 的实体句式）
    normals = [
        "m-001", "m-002", "m-003", "m-004", "m-005",
        "m-006", "m-007", "m-008", "m-009", "m-010",
        "m-011", "m-012", "m-013", "m-014",
    ]
    for i, uid in enumerate(normals):
        rows.append({
            "id": uid,
            "memory": f"demo-normal-{i:02d}: 项目里程碑与设计决策记录，供检索回归使用。",
            "created_at": _ts(60 * 24 * (i + 1)),
            "source": "demo_agent",
        })
    # 重复 UID（应被 import 跳过 2 条）
    rows.append({"id": "m-003", "memory": "demo-duplicate: 同 UID 的重复记录，导入时应跳过。",
                 "created_at": _ts(120), "source": "demo_agent"})
    rows.append({"id": "m-007", "memory": "demo-duplicate: 同 UID 的重复记录，导入时应跳过。",
                 "created_at": _ts(120), "source": "demo_agent"})
    # 冲突组 1：GPTX_ASTRA_KEY（pos 在前旧，neg 更新 → 应退役旧 pos）
    rows.append({"id": "m-100", "memory": "GPTX_ASTRA_KEY 可用，直连正常。",
                 "created_at": _ts(60), "source": "demo_agent"})
    rows.append({"id": "m-101", "memory": "GPTX_ASTRA_KEY 失效（403）。",
                 "created_at": _ts(30), "source": "demo_agent"})
    # 冲突组 2：SILICONFLOW（neg 在前旧，pos 更新 → 应退役旧 neg）
    rows.append({"id": "m-200", "memory": "SILICONFLOW 余额不足，已失效。",
                 "created_at": _ts(60), "source": "demo_agent"})
    rows.append({"id": "m-201", "memory": "SILICONFLOW 已充值，可用。",
                 "created_at": _ts(30), "source": "demo_agent"})
    return rows


def main():
    keep = "--keep" in sys.argv
    tmp = tempfile.mkdtemp(prefix="mt_demo_conflict_", dir="E:/Temp")
    summary = {"tmp_dir": tmp, "steps": []}

    try:
        # 1) 合成 Mem0 JSON
        src = os.path.join(tmp, "mem0_export.json")
        rows = build_mem0_rows()
        json.dump(rows, open(src, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        # 2) Mem0 → Memory Exchange
        exchange_path = os.path.join(tmp, "exchange.json")
        m0x.mem0_to_exchange(src, exchange_path, source="demo_agent", pii_redact=False)
        ex = json.load(open(exchange_path, encoding="utf-8"))
        assert len(ex["facts"]) == len(rows), \
            f"exchange facts count {len(ex['facts'])} != {len(rows)}"

        # 3) 导入临时库
        demo_db = os.path.join(tmp, "demo_conflict.db")
        stats = mx.import_exchange(exchange_path, demo_db)
        assert stats.get("ok"), stats
        assert stats["facts_inserted"] == 18, stats
        assert stats["facts_skipped"] == 2, stats
        summary["steps"].append({"import": stats})

        # 4) 冲突检测（governance.DB 硬编码，demo 里显式指到临时库）
        governance.DB = demo_db
        conflicts = governance.detect_explicit_conflicts(90)
        assert len(conflicts) == 2, f"expected 2 conflicts, got {len(conflicts)}: {conflicts}"
        summary["steps"].append({
            "conflicts_detected": [
                {"entity": c["entity"], "retire": c["suggest_retire"],
                 "keep": c["keep"], "newer_side": c["newer"]}
                for c in conflicts]})

        # 5) 自动退役（force=True：demo 合成条无残留事实风险，且 reason 实体已过滤）
        retired = []
        for c in conflicts:
            reason = f"{c['entity']} 状态以新条为准，旧条自动退役"
            r = governance.retire(c["suggest_retire"], by_uid=c["keep"],
                                  reason=reason, apply=True, force=True)
            assert r.get("ok"), r
            retired.append({"retired_uid": c["suggest_retire"], "kept_uid": c["keep"],
                            "reason": reason})

        # 复检
        conflicts2 = governance.detect_explicit_conflicts(90)
        assert conflicts2 == [], f"re-detect not clean: {conflicts2}"

        # 6) 断言旧条生命周期字段
        conn = sqlite3.connect(demo_db)
        conn.row_factory = sqlite3.Row
        for item in retired:
            row = conn.execute("SELECT status, superseded_by, valid_to FROM facts WHERE uid=?",
                               (item["retired_uid"],)).fetchone()
            assert row["status"] == "superseded", dict(row)
            assert row["superseded_by"] == item["kept_uid"], dict(row)
            assert row["valid_to"], dict(row)
            keep_row = conn.execute("SELECT status FROM facts WHERE uid=?",
                                    (item["kept_uid"],)).fetchone()
            assert keep_row["status"] == "active", dict(keep_row)
        conn.close()

        summary["steps"].append({"retired": retired, "re_detect_conflicts": len(conflicts2)})
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


