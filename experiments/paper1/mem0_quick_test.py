"""
E1: mem0 quick test on 10 LongMemEval questions.
Uses cn:glm-5.3-flash via resp_bridge /v1/responses.
"""
import sys, io, json, os, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import create_mem0_memory, ResponsesBridgeLLM
from bench_longmemeval import judge_strict, _key_terms, _looks_like_prose
from collections import defaultdict
from datetime import datetime

data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))

# Stratified sample: 2 per type
types_seen = defaultdict(int)
sample = []
target_per_type = {"single-session-user": 2, "single-session-assistant": 2, "single-session-preference": 1,
                   "knowledge-update": 2, "temporal-reasoning": 2, "multi-session": 2}
for item in data:
    t = item["question_type"]
    if types_seen[t] < target_per_type.get(t, 1):
        sample.append(item)
        types_seen[t] += 1
    if len(sample) >= 10:
        break

print(f"Sample: {len(sample)} questions, types: {dict(types_seen)}")

memory = create_mem0_memory("mem0_lme_quick", "E:/Temp/mem0_lme_chroma_quick")

results = []
per_type = defaultdict(lambda: {"n": 0, "strict": 0, "strict_n": 0, "judge": 0, "judge_n": 0})

def call_llm_direct(prompt, max_output_tokens=500):
    """Direct LLM call for generation and judging."""
    import urllib.request
    data = json.dumps({
        "model": "cn:glm-5.3-flash",
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8814/v1/responses", data=data, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=180)
            result = json.loads(resp.read())
            for item in result.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            return c["text"]
            return None
        except Exception as e:
            print(f"  LLM retry {attempt+1}: {e}")
            time.sleep(3)
    return None

strict_hits, strict_total, judge_hits, judge_total = 0, 0, 0, 0

for i, item in enumerate(sample):
    qid = f"quick_{i}"
    q_type = item["question_type"]
    question = item["question"]
    gold = str(item["answer"]).strip()
    per_type[q_type]["n"] += 1
    
    print(f"\n[{i+1}/{len(sample)}] {q_type}: {question[:60]}...")
    
    # Step 1: Add sessions to mem0
    try:
        for session in item.get("haystack_sessions", []):
            messages = [{"role": t["role"], "content": t["content"]} for t in session if t.get("content")]
            if messages:
                memory.add(messages, user_id=qid)
    except Exception as e:
        print(f"  Add error: {e}")
        continue
    
    # Step 2: Search
    try:
        search_result = memory.search(question, filters={"user_id": qid}, top_k=10)
        related = search_result.get("results", []) if isinstance(search_result, dict) else search_result
        pred = " | ".join([str(r.get("memory", ""))[:300] for r in related[:10]])
    except Exception as e:
        print(f"  Search error: {e}")
        pred = ""
    
    print(f"  Retrieved {len(related) if related else 0} memories, pred: {pred[:80]}...")
    
    # Step 3: Strict scoring
    s = judge_strict(pred, item["answer"])
    if s is not None:
        strict_total += 1
        per_type[q_type]["strict_n"] += 1
        if s:
            strict_hits += 1
            per_type[q_type]["strict"] += 1
    
    # Step 4: Generate + judge
    if pred:
        gen_prompt = f"Based on the following information, answer the question concisely.\n\nInformation: {pred[:2000]}\n\nQuestion: {question}\n\nAnswer:"
        answer = call_llm_direct(gen_prompt, 500)
        
        if answer:
            judge_prompt = f"Question: {question}\nExpected answer: {gold}\nGiven answer: {answer[:200]}\n\nIs the given answer semantically correct? Reply only YES or NO."
            judge_result = call_llm_direct(judge_prompt, 300)
            
            if judge_result:
                judge_total += 1
                per_type[q_type]["judge_n"] += 1
                is_correct = "YES" in judge_result.upper()
                if is_correct:
                    judge_hits += 1
                    per_type[q_type]["judge"] += 1
                print(f"  strict={s}, judge={judge_result.strip()[:20]}, answer={answer[:60]}...")
    else:
        print(f"  No memories retrieved")

print(f"\n{'='*60}")
print(f"Quick Test Results ({len(sample)} questions)")
print(f"  strict: {strict_hits}/{strict_total} = {strict_hits/max(strict_total,1)*100:.1f}%")
print(f"  judge:  {judge_hits}/{judge_total} = {judge_hits/max(judge_total,1)*100:.1f}%")

print(f"\nPer-type:")
for t in sorted(per_type.keys()):
    d = per_type[t]
    sr = d["strict"] / d["strict_n"] * 100 if d["strict_n"] else 0
    jr = d["judge"] / d["judge_n"] * 100 if d["judge_n"] else 0
    print(f"  {t:<30} n={d['n']} strict={sr:.0f}% ({d['strict']}/{d['strict_n']}) judge={jr:.0f}% ({d['judge']}/{d['judge_n']})")

output = {
    "ts": datetime.now().isoformat(),
    "system": "mem0",
    "model": "cn:glm-5.3-flash",
    "n": len(sample),
    "strict": round(strict_hits / strict_total, 4) if strict_total else 0,
    "strict_n": strict_total,
    "judge": round(judge_hits / judge_total, 4) if judge_total else 0,
    "judge_n": judge_total,
    "per_type": dict(per_type),
}
with open("E:/Temp/bench_mem0_quick_result.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"\nSaved to E:/Temp/bench_mem0_quick_result.json")
