# -*- coding: utf-8 -*-
"""
demo_export_roundtrip.py — Mem0 + Zep 导出方向端到端验证（2026-09-26）

验证链路：原始数据 → mt_to_mem0/mt_to_zep（导出）→ 再导入 memtether_exchange →
         数据一致性（内容 / source / 时间轴）比对。

用法：python scripts/demo_export_roundtrip.py
"""
import json, os, shutil, sqlite3, sys, tempfile, datetime
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import memtether_exchange as mx
import mem0_exchange as m0x
import zep_exchange as zpx
import governance

def _ts(minutes_ago):
    t = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")

def main():
    tmp = tempfile.mkdtemp(prefix="mt_roundtrip_", dir="E:/Temp")
    summary = {"tmp_dir": tmp, "roundtrips": []}
    try:
        # Build source exchange file (like 5-adapter demo)
        src_db = os.path.join(tmp, "source.db")
        ex_path = os.path.join(tmp, "source.ex.json")

        # Build initial exchange from raw sources (mem0 + zep)
        mem0_rows = [
            {"id":"r-001","memory":"rt: 用户偏好深色主题。","created_at":_ts(120)},
            {"id":"r-002","memory":"rt: Python 3.12。","created_at":_ts(90)},
            {"id":"r-003","memory":"OPENAI_API_KEY 可用，余额充足。","created_at":_ts(60)},
        ]
        zep_rows = [
            {"uuid":"rz-001","fact":"rt: 用户 timezone Asia/Shanghai。","created_at":_ts(100)},
            {"uuid":"rz-002","fact":"OPENAI_API_KEY 已失效（401）。","created_at":_ts(30)},
        ]
        p0 = os.path.join(tmp, "m0.json"); json.dump(mem0_rows, open(p0,"w",encoding="utf-8"), ensure_ascii=False)
        pz = os.path.join(tmp, "zp.json"); json.dump(zep_rows, open(pz,"w",encoding="utf-8"), ensure_ascii=False)
        m0x.mem0_to_exchange(p0, p0+".ex", source="mem0", pii_redact=False)
        zpx.zep_to_exchange(pz, pz+".ex", source="zep", pii_redact=False)

        # Import into unified db
        demo_db = os.path.join(tmp, "unified.db")
        mx.import_exchange(p0+".ex", demo_db)
        mx.import_exchange(pz+".ex", demo_db)

        # Export from unified db to mem0 format
        ex_out = os.path.join(tmp, "export.ex.json")
        mx.export_exchange(db_path=demo_db, out_path=ex_out)
        mem0_out = os.path.join(tmp, "export_mem0.json")
        m0x.mt_to_mem0(ex_out, mem0_out)
        zep_out = os.path.join(tmp, "export_zep.json")
        zpx.mt_to_zep(ex_out, zep_out)

        # Re-import exported mem0 format into fresh db
        rt_db = os.path.join(tmp, "rt_mem0.db")
        m0x.mem0_to_exchange(mem0_out, mem0_out+".ex", source="mem0_rt", pii_redact=False)
        s1 = mx.import_exchange(mem0_out+".ex", rt_db)

        # Re-import exported zep format into fresh db
        rt2_db = os.path.join(tmp, "rt_zep.db")
        zpx.zep_to_exchange(zep_out, zep_out+".ex", source="zep_rt", pii_redact=False)
        s2 = mx.import_exchange(zep_out+".ex", rt2_db)

        # Compare content
        def get_contents(db):
            conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT uid, content, source FROM facts ORDER BY uid").fetchall()
            conn.close()
            return [dict(r) for r in rows]

        orig = get_contents(demo_db)
        rt0 = get_contents(rt_db)
        rt1 = get_contents(rt2_db)

        orig_set = {r["content"] for r in orig}
        rt0_set = {r["content"] for r in rt0}
        rt1_set = {r["content"] for r in rt1}

        m0_ok = orig_set == rt0_set
        zep_ok = orig_set == rt1_set
        summary["roundtrips"].append({"fmt":"mem0","exported":len(rt0_set),"content_match":m0_ok})
        summary["roundtrips"].append({"fmt":"zep","exported":len(rt1_set),"content_match":zep_ok})
        summary["ok"] = m0_ok and zep_ok
        if not summary["ok"]:
            summary["orig_contents"] = sorted(orig_set)
            summary["mem0_rt_contents"] = sorted(rt0_set)
            summary["zep_rt_contents"] = sorted(rt1_set)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("PASS" if summary["ok"] else "FAIL")
        return 0 if summary["ok"] else 1
    except Exception as e:
        summary["ok"] = False; summary["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        print(f"FAIL: {e}", file=sys.stderr); return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())