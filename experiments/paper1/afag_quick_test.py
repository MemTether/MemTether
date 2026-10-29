import sys, io, json, os, time
# stdout encoding from env
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from afag import detect_afag_type, build_afag_prompt, _pick_judge_channel
from bench_longmemeval import judge_strict, judge_llm, generate_answer, retrieve_answer
from collections import defaultdict
from datetime import datetime

data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))
ms = [x for x in data if x["question_type"] == "multi-session"]
sample = []
for item in ms:
    total = sum(len(t.get("content","")) for s in item.get("haystack_sessions",[]) for t in s)
    if total <= 40000:
        sample.append(item)
    if len(sample) >= 20:
        break
print(f"sample: {len(sample)} multi-session questions")

results = []
for i, item in enumerate(sample):
    question = item["question"]
    gold = str(item["answer"]).strip()
    q_type = item["question_type"]
    
    # Retrieve using the standard bench pipeline
    try:
        import time
        time.sleep(1)
        for _retry in range(3):
            try:
                retrieval_results, _count = retrieve_answer(item, k=12, variant='oracle')
                break
            except Exception as db_err:
                if 'locked' in str(db_err).lower():
                    time.sleep(5)
                else:
                    raise
        if retrieval_results:
            evidence = retrieval if isinstance(retrieval, str) else "\n".join([str(r)[:200] for r in retrieval])
        else:
            evidence = ""
    except Exception as e:
        evidence = ""
    
    needs_afag, afag_type = detect_afag_type(question)
    
    # Generate with baseline
    baseline_answer, baseline_err = generate_answer(question, evidence, question_type=q_type)
    
    # Generate with AFAG
    afag_answer = None
    if needs_afag:
        prompt = build_afag_prompt(question, evidence, afag_type)
        import requests
        ch = _pick_judge_channel()
        if ch:
            envn, base_url, mdl, key = ch
            try:
                r = requests.post(base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    json={"model": mdl, "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 200, "temperature": 0}, timeout=60)
                r.raise_for_status()
                afag_answer = (r.json()["choices"][0]["message"]["content"] or "").strip()
            except:
                afag_answer = baseline_answer
    else:
        afag_answer = baseline_answer
    
    baseline_strict = judge_strict(baseline_answer or "", item["answer"])
    afag_strict = judge_strict(afag_answer or "", item["answer"])
    baseline_judge, _ = judge_llm(question, gold, baseline_answer or "")
    afag_judge, _ = judge_llm(question, gold, afag_answer or "")
    
    result = {
        "idx": i, "type": q_type,
        "afag_type": afag_type if needs_afag else "none",
        "gold": gold[:50],
        "baseline_answer": (baseline_answer or "")[:80],
        "afag_answer": (afag_answer or "")[:80],
        "baseline_strict": bool(baseline_strict) if baseline_strict is not None else None,
        "afag_strict": bool(afag_strict) if afag_strict is not None else None,
        "baseline_judge": baseline_judge,
        "afag_judge": afag_judge,
    }
    results.append(result)
    print(f"[{i+1}/{len(sample)}] {q_type} | AFAG={'Y' if needs_afag else 'N'}({afag_type}) "
          f"| strict: base={baseline_strict} afag={afag_strict} "
          f"| judge: base={baseline_judge} afag={afag_judge}")

bs_total = sum(1 for r in results if r["baseline_strict"] is not None)
bs_hits = sum(1 for r in results if r["baseline_strict"])
as_total = sum(1 for r in results if r["afag_strict"] is not None)
as_hits = sum(1 for r in results if r["afag_strict"])
bj_total = sum(1 for r in results if r["baseline_judge"] is not None)
bj_hits = sum(1 for r in results if r["baseline_judge"])
aj_total = sum(1 for r in results if r["afag_judge"] is not None)
aj_hits = sum(1 for r in results if r["afag_judge"])

print(f"\n{'='*60}")
print(f"E-AFAG Quick Test ({len(sample)} multi-session questions)")
print(f"  strict: baseline={bs_hits}/{bs_total}={bs_hits/max(bs_total,1)*100:.1f}% AFAG={as_hits}/{as_total}={as_hits/max(as_total,1)*100:.1f}%")
print(f"  judge:  baseline={bj_hits}/{bj_total}={bj_hits/max(bj_total,1)*100:.1f}% AFAG={aj_hits}/{aj_total}={aj_hits/max(aj_total,1)*100:.1f}%")
afag_count = sum(1 for r in results if r["afag_type"] != "none")
print(f"  AFAG activated on {afag_count}/{len(sample)} questions")

output = {"ts": datetime.now().isoformat(), "n": len(sample),
          "baseline": {"strict": bs_hits, "strict_n": bs_total, "judge": bj_hits, "judge_n": bj_total},
          "afag": {"strict": as_hits, "strict_n": as_total, "judge": aj_hits, "judge_n": aj_total},
          "afag_activations": afag_count, "results": results}
with open("E:/Temp/afag_quick_result.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print("Saved")





