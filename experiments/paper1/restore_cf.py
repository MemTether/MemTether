import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
# Same-directory siblings restore_cf_pass3.py / restore_cf_resume.py both have
# this line; this file did not, so `from llm_helper import ...` died with
# ModuleNotFoundError. Found 2026-10-22 by the first-ever import coverage
# run over experiments/.
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from llm_helper import call_llm
# bench_longmemeval lives in the REPO ROOT, not in this directory; the
# repo root is already on sys.path (line 3), so this import resolves.
from bench_longmemeval import judge_strict, _key_terms, _looks_like_prose
from collections import Counter
from datetime import datetime

data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))
ms = [x for x in data if x["question_type"] == "multi-session"]
print(f"multi-session total: {len(ms)}")

results = []
skipped = 0
for i, item in enumerate(ms):
    gold = str(item["answer"]).strip()
    
    # Skip prose and non-applicable
    kt = _key_terms(gold)
    if re.fullmatch(r"-?\d+(\.\d+)?", gold):
        pass  # numeric golds ARE applicable
    elif _looks_like_prose(gold) or len(kt) > 12 or len(kt) == 0:
        skipped += 1
        continue
    
    # Find gold evidence content
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
    
    # Generate answer from gold evidence
    prompt = f"Based on the following conversation history, answer the question concisely.\n\n{gold_content[:3000]}\n\nQuestion: {item['question']}\n\nAnswer:"
    generated = call_llm(prompt, max_tokens=100)
    
    if generated is None:
        results.append({"idx": i, "status": "gen_error"})
        continue
    
    # Check strict
    s = judge_strict(generated, item["answer"])
    
    # Check if gold string is in output
    contains = gold.lower() in generated.lower()
    
    # Check numeric presence
    if re.fullmatch(r"-?\d+(\.\d+)?", gold):
        number_hit = bool(re.search(r"\b" + re.escape(gold) + r"\b", generated))
    else:
        number_hit = False
    
    # Classify
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
    
    if (i+1) % 20 == 0:
        print(f"Progress: {i+1}/{len(ms)}")

# Summary
categories = Counter(r.get("category", "gen_error") for r in results)
print(f"\nRestore-Counterfactual Results (total={len(results)}, skipped={skipped}):")
for cat, count in categories.most_common():
    print(f"  {cat}: {count}")

computed_pct = categories.get("computed_not_in_output", 0) / max(len([r for r in results if 'category' in r]), 1) * 100
print(f"\nKEY: computed_not_in_output = {computed_pct:.1f}%")

output = {
    "ts": datetime.now().isoformat(),
    "experiment": "restore_counterfactual",
    "total_multi_session": len(ms),
    "strict_applicable": len(results),
    "skipped": skipped,
    "results": results,
    "summary": dict(categories),
}
with open("E:/Temp/restore_cf_results.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"Saved to E:/Temp/restore_cf_results.json")
