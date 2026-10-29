import sys, io, json, re, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from llm_helper import call_llm
from bench_longmemeval import judge_strict, _key_terms, _looks_like_prose
from collections import Counter
from datetime import datetime

data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))
ms = [x for x in data if x["question_type"] == "multi-session"]

# Load previous results to resume
prev = {}
try:
    prev_data = json.load(open("E:/Temp/restore_cf_results.json", encoding="utf-8"))
    for r in prev_data.get("results", []):
        if "category" in r:
            prev[r["idx"]] = r
    print(f"Loaded {len(prev)} previous valid results")
except:
    print("No previous results")

def call_llm_retry(prompt, retries=3, max_tokens=100):
    for attempt in range(retries):
        r = call_llm(prompt, max_tokens=max_tokens)
        if r is not None:
            return r
        time.sleep(2 * (attempt + 1))
    return None

results = list(prev.values())
processed_idx = set(prev.keys())
skipped = 0
for i, item in enumerate(ms):
    if i in processed_idx:
        continue
    gold = str(item["answer"]).strip()
    kt = _key_terms(gold)
    if re.fullmatch(r"-?\d+(\.\d+)?", gold):
        pass
    elif _looks_like_prose(gold) or len(kt) > 12 or len(kt) == 0:
        skipped += 1
        continue
    
    answer_session_ids = item.get("answer_session_ids", [])
    gold_content = ""
    for j, session in enumerate(item.get("haystack_sessions", [])):
        session_id = ""
        ids = item.get("haystack_session_ids", [])
        if j < len(ids):
            session_id = ids[j]
        if session_id in answer_session_ids or not answer_session_ids:
            for turn in session:
                gold_content += f"{turn['role']}: {turn['content']}\n"
    if not gold_content:
        for session in item.get("haystack_sessions", []):
            for turn in session:
                gold_content += f"{turn['role']}: {turn['content']}\n"
    
    prompt = f"Based on the following conversation history, answer the question concisely.\n\n{gold_content[:3000]}\n\nQuestion: {item['question']}\n\nAnswer:"
    generated = call_llm_retry(prompt, retries=3)
    
    if generated is None:
        results.append({"idx": i, "status": "gen_error"})
        continue
    
    s = judge_strict(generated, item["answer"])
    contains = gold.lower() in generated.lower()
    if re.fullmatch(r"-?\d+(\.\d+)?", gold):
        number_hit = bool(re.search(r"\b" + re.escape(gold) + r"\b", generated))
    else:
        number_hit = False
    
    if s:
        category = "strict_hit_with_gold"
    elif contains or number_hit:
        category = "gold_in_output_but_strict_miss"
    else:
        category = "computed_not_in_output"
    
    results.append({
        "idx": i, "question": item["question"][:80],
        "gold": gold[:50], "generated": generated[:80],
        "strict_with_gold": bool(s), "category": category,
    })
    
    if len(results) % 20 == 0:
        print(f"Progress: {len(results)} valid results")

# Summary
categories = Counter(r.get("category", "gen_error") for r in results)
valid_results = [r for r in results if "category" in r]
print(f"\nRestore-Counterfactual Final (valid={len(valid_results)}, gen_error={len(results)-len(valid_results)}, skipped={skipped}):")
for cat, count in categories.most_common():
    print(f"  {cat}: {count}")

if valid_results:
    computed_pct = categories.get("computed_not_in_output", 0) / len(valid_results) * 100
    strict_hit_pct = categories.get("strict_hit_with_gold", 0) / len(valid_results) * 100
    print(f"\nKEY METRICS:")
    print(f"  computed_not_in_output: {computed_pct:.1f}%")
    print(f"  strict_hit_with_gold: {strict_hit_pct:.1f}%")

output = {
    "ts": datetime.now().isoformat(),
    "experiment": "restore_counterfactual",
    "total_multi_session": len(ms),
    "strict_applicable": len(valid_results),
    "skipped": skipped,
    "results": results,
    "summary": dict(categories),
    "valid_summary": {k: v for k, v in categories.items() if k != "gen_error"},
}
with open("E:/Temp/restore_cf_results.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"Saved to E:/Temp/restore_cf_results.json")
