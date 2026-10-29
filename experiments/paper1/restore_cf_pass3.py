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

prev_data = json.load(open("E:/Temp/restore_cf_results.json", encoding="utf-8"))
prev = {}
for r in prev_data.get("results", []):
    if "category" in r:
        prev[r["idx"]] = r
processed_idx = set(prev.keys())

def call_llm_retry(prompt, retries=5, max_tokens=60):
    for attempt in range(retries):
        try:
            r = call_llm(prompt, max_tokens=max_tokens)
            if r is not None:
                return r
        except:
            pass
        time.sleep(3)
    return None

new_results = []
for i, item in enumerate(ms):
    if i in processed_idx:
        continue
    gold = str(item["answer"]).strip()
    kt = _key_terms(gold)
    if re.fullmatch(r"-?\d+(\.\d+)?", gold):
        pass
    elif _looks_like_prose(gold) or len(kt) > 12 or len(kt) == 0:
        continue
    
    answer_session_ids = item.get("answer_session_ids", [])
    gold_content = ""
    for j, session in enumerate(item.get("haystack_sessions", [])):
        ids = item.get("haystack_session_ids", [])
        session_id = ids[j] if j < len(ids) else ""
        if session_id in answer_session_ids or not answer_session_ids:
            for turn in session:
                gold_content += f"{turn['role']}: {turn['content']}\n"
    if not gold_content:
        for session in item.get("haystack_sessions", []):
            for turn in session:
                gold_content += f"{turn['role']}: {turn['content']}\n"
    
    # Shorter prompt to reduce timeout
    prompt = f"Based on the conversation, answer concisely.\n\n{gold_content[:1500]}\n\nQ: {item['question']}\nA:"
    generated = call_llm_retry(prompt, retries=5, max_tokens=60)
    
    if generated is None:
        new_results.append({"idx": i, "status": "gen_error"})
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
    
    new_results.append({
        "idx": i, "question": item["question"][:80],
        "gold": gold[:50], "generated": generated[:80],
        "strict_with_gold": bool(s), "category": category,
    })

# Merge
all_results = list(prev.values()) + new_results
categories = Counter(r.get("category", "gen_error") for r in all_results)
valid_results = [r for r in all_results if "category" in r]
print(f"\nFinal (valid={len(valid_results)}, gen_error={len(all_results)-len(valid_results)}):")
for cat, count in categories.most_common():
    print(f"  {cat}: {count}")

if valid_results:
    computed_pct = categories.get("computed_not_in_output", 0) / len(valid_results) * 100
    print(f"\nKEY: computed_not_in_output = {computed_pct:.1f}% ({categories.get('computed_not_in_output', 0)}/{len(valid_results)})")

output = {
    "ts": datetime.now().isoformat(),
    "experiment": "restore_counterfactual",
    "total_multi_session": len(ms),
    "strict_applicable": len(valid_results),
    "results": all_results,
    "summary": dict(categories),
    "valid_summary": {k: v for k, v in categories.items() if k != "gen_error"},
}
with open("E:/Temp/restore_cf_results.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print("Saved")
