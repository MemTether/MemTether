"""consolidation.py - Background memory consolidation index
Based on Mnemon (arXiv 2609.36059): consolidation adds +4.4% on LongMemEval-S
Three index types: topic timelines, value histories, standing instructions
"""
import sqlite3, json, os, sys, re, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("MEM_DB") or os.path.join(HERE, "memory.db")

def _get_conn():
    db = os.environ.get("MEM_DB") or DB
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn

def build_topic_timelines(db_path=None):
    conn = _get_conn()
    entity_map = conn.execute(
        "SELECT fe.entity_name, fe.fact_uid, f.content, f.type, "
        "COALESCE(f.updated_at, f.created_at) as ts "
        "FROM fact_entities fe JOIN facts f ON f.uid = fe.fact_uid "
        "WHERE f.status = 'active' ORDER BY ts ASC"
    ).fetchall()
    topic_timelines = defaultdict(list)
    for row in entity_map:
        topic = row["entity_name"]
        if len(topic) >= 3:
            topic_timelines[topic].append({
                "uid": row["fact_uid"], "ts": row["ts"],
                "type": row["type"], "content": row["content"][:200],
            })
    output = {}
    for topic, timeline in topic_timelines.items():
        if len(timeline) >= 2:
            output[topic] = {
                "count": len(timeline),
                "first_seen": timeline[0]["ts"],
                "last_seen": timeline[-1]["ts"],
                "timeline": timeline[-20:],
            }
    out_path = os.path.join(HERE, "topic_index.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    conn.close()
    return {"topics": len(output), "total_entries": sum(v["count"] for v in output.values())}

def build_value_histories(db_path=None):
    conn = _get_conn()
    chains = conn.execute(
        "SELECT s.old_uid, s.new_uid, s.reason, s.ts, "
        "f_old.content as old_content, f_new.content as new_content "
        "FROM supersessions s JOIN facts f_old ON f_old.uid = s.old_uid "
        "JOIN facts f_new ON f_new.uid = s.new_uid ORDER BY s.ts ASC"
    ).fetchall()
    value_histories = defaultdict(list)
    for chain in chains:
        key = chain["old_content"][:50].strip()
        value_histories[key].append({
            "old": chain["old_content"][:200], "new": chain["new_content"][:200],
            "changed_at": chain["ts"], "reason": chain["reason"] or "update",
        })
    out_path = os.path.join(HERE, "value_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(value_histories), f, ensure_ascii=False, indent=2)
    conn.close()
    return {"value_chains": len(value_histories), "total_changes": sum(len(v) for v in value_histories.values())}

def build_standing_instructions(db_path=None):
    conn = _get_conn()
    patterns = ["%always%", "%never%", "%must%", "%remember%", "%forbidden%",
                "%never%", "%every time%", "%always%", "%must%", "%not allowed%", "%iron rule%"]
    instructions = []
    for pattern in patterns:
        rows = conn.execute(
            "SELECT uid, content, type, source, COALESCE(updated_at, created_at) as ts "
            "FROM facts WHERE status='active' AND content LIKE ? "
            "ORDER BY ts DESC LIMIT 20", (pattern,)
        ).fetchall()
        for r in rows:
            if not any(i["uid"] == r["uid"] for i in instructions):
                instructions.append({
                    "uid": r["uid"], "content": r["content"][:200],
                    "type": r["type"], "source": r["source"], "ts": r["ts"],
                    "trigger": pattern.strip("%"),
                })
    instructions.sort(key=lambda x: x["ts"], reverse=True)
    out_path = os.path.join(HERE, "standing_instructions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(instructions, f, ensure_ascii=False, indent=2)
    conn.close()
    return {"instructions": len(instructions)}

def build_all():
    return {
        "topics": build_topic_timelines(),
        "values": build_value_histories(),
        "instructions": build_standing_instructions(),
    }

def search_index(query, limit=5):
    results = []
    topic_path = os.path.join(HERE, "topic_index.json")
    if os.path.exists(topic_path):
        topics = json.load(open(topic_path, "r", encoding="utf-8"))
        q_lower = query.lower()
        for topic, data in topics.items():
            if topic.lower() in q_lower or any(w in q_lower for w in topic.lower().split()):
                results.append({
                    "type": "topic_timeline", "topic": topic,
                    "count": data["count"], "last_seen": data["last_seen"],
                    "entries": data["timeline"][-3:],
                })
                if len([r for r in results if r["type"] == "topic_timeline"]) >= limit:
                    break
    inst_path = os.path.join(HERE, "standing_instructions.json")
    if os.path.exists(inst_path):
        instructions = json.load(open(inst_path, "r", encoding="utf-8"))
        q_lower = query.lower()
        for inst in instructions:
            if inst["trigger"].lower() in q_lower:
                results.append({
                    "type": "standing_instruction",
                    "content": inst["content"], "ts": inst["ts"],
                })
    return results[:limit]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--search", type=str)
    args = parser.parse_args()
    if args.build:
        print(json.dumps(build_all(), indent=2, ensure_ascii=False))
    elif args.search:
        print(json.dumps(search_index(args.search), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
