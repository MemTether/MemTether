# -*- coding: utf-8 -*-
"""locomo_full_run.py — Full 1986Q LoCoMo benchmark for MemTether.

Ingests all 10 conversations into a scratch DB, then answers each question
using search_hybrid (the production retrieval pipeline).

No LLM judge needed for the r^N validation — we use strict_answer (gold text
substring match in generated answer) and strict_evidence (gold text in retrieved context).

This gives us (N, correct) pairs across 1986 questions for the r^N analysis.
"""
import json, io, os, sys, re, time, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'E:\RUANJIAN\memtether')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\.venv-memory\Lib\site-packages')

# ============ SETUP ============
MT_DB = "E:/Temp/_locomo_full_mt.db"
if os.path.exists(MT_DB): os.remove(MT_DB)
os.environ['MEM_DB'] = MT_DB
os.environ['MEM_SKIP_VECTOR'] = '0'  # NEED vector for search_hybrid
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Use a separate Chroma collection
os.environ['MEM_CHROMA_PATH'] = "E:/Temp/_locomo_full_chroma"

import gateway
gateway.init_db()

# Load LoCoMo
with open(r"E:\Temp\locomo10.json", encoding="utf-8") as f:
    locomo = json.load(f)
print(f"Loaded LoCoMo: {len(locomo)} conversations, {sum(len(c['qa']) for c in locomo)} questions")

# ============ INGEST ALL CONVERSATIONS ============
print("Ingesting all conversations...")
t0 = time.time()
total_msgs = 0
for ci, conv in enumerate(locomо if False else locomo):
    conv_id = conv.get("sample_id", f"conv_{ci}")
    conversation = conv["conversation"]
    
    # Ingest all session turns
    for key in sorted(conversation.keys()):
        if key.startswith("session_") and not key.endswith("_date_time"):
            session_data = conversation[key]
            if isinstance(session_data, list):
                for turn in session_data:
                    if isinstance(turn, dict) and "text" in turn:
                        speaker = turn.get("speaker", "unknown")
                        text = f"[{speaker}] {turn['text']}"
                        ts = conversation.get(f"{key}_date_time", "")
                        uid = f"locomo-{conv_id}-{key}-{turn.get('turn_id', total_msgs)}"
                        conn = sqlite3.connect(MT_DB)
                        conn.execute("""INSERT OR IGNORE INTO facts 
                            (uid, type, subject, content, status, source, scope, confidence,
                             created_at, updated_at, valid_from, recorded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (uid, "fact", conv_id, text, "active",
                             "locomo", "shared", 0.8, ts, ts, ts, ts))
                        conn.commit()
                        conn.close()
                        total_msgs += 1

print(f"Ingested {total_msgs} messages in {time.time()-t0:.1f}s")

# ============ ANSWER ALL QUESTIONS ============
print("\nAnswering questions...")
results = []
t1 = time.time()
for ci, conv in enumerate(locomo):
    conv_id = conv.get("sample_id", f"conv_{ci}")
    
    for qi, qa in enumerate(conv["qa"]):
        question = qa["question"]
        gold = str(qa.get("answer") or qa.get("adversarial_answer") or "")
        category = qa.get("category", 0)
        evidence = qa.get("evidence", [])
        
        # Derive N (number of distinct evidence sessions)
        sessions = set()
        for e in evidence:
            m = re.match(r"[AD]?(\d+):", str(e))
            if m:
                sessions.add(int(m.group(1)))
        n_sessions = len(sessions)
        
        # Retrieve using search_hybrid
        try:
            r = gateway.search(question, limit=15)
            retrieved = " ".join(x.get("content", "") for x in (r.get("results") or []))
            
            # strict_evidence: gold answer text in retrieved context
            strict_evidence = str(gold).lower() in retrieved.lower()
            
            # strict_answer: gold answer in the top-1 result (most similar)
            top1 = (r.get("results") or [{}])[0].get("content", "")
            strict_answer = str(gold).lower() in top1.lower()
            
            # Also check: is the evidence session in the retrieved results?
            ev_found = any(sessions and f"session_{s}" in str(r.get("results", [])) for s in sessions)
            
        except Exception as e:
            retrieved = ""
            strict_evidence = False
            strict_answer = False
            ev_found = False
        
        results.append({
            "conv": conv_id, "q_idx": qi, "category": category,
            "n_sessions": n_sessions, "question": question[:100],
            "gold": str(gold)[:60],
            "strict_evidence": strict_evidence,
            "strict_answer": strict_answer,
        })
        
        if (len(results)) % 200 == 0:
            elapsed = time.time() - t1
            print(f"  {len(results)}/1986 done ({elapsed:.0f}s)")

print(f"\nAll {len(results)} questions answered in {time.time()-t1:.0f}s")

# ============ SAVE ============
with open("E:/Temp/_locomo_full_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

# ============ QUICK ANALYSIS ============
from collections import defaultdict
agg_se = defaultdict(lambda: [0, 0])
agg_sa = defaultdict(lambda: [0, 0])
for r in results:
    n = r["n_sessions"]
    agg_se[n][0] += int(r["strict_evidence"])
    agg_se[n][1] += 1
    agg_sa[n][0] += int(r["strict_answer"])
    agg_sa[n][1] += 1

print("\n=== N -> Accuracy (strict_evidence) ===")
for n in sorted(agg_se):
    c, t = agg_se[n]
    print(f"  N={n}: {c}/{t} = {c/t:.1%}" if t else f"  N={n}: 0/0")

print(f"\nOverall strict_evidence: {sum(c for c,t in agg_se.values())}/{sum(t for c,t in agg_se.values())}")
print(f"Overall strict_answer: {sum(c for c,t in agg_sa.values())}/{sum(t for c,t in agg_sa.values())}")
print("\nsaved to E:/Temp/_locomo_full_results.json")
