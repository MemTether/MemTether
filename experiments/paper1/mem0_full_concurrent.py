"""
E1: mem0 full 500Q experiment, 10-concurrent, resumable from checkpoint.
Uses cn:deepseek-v4.1-flash via resp_bridge:8814/v1/responses only (NO tabbit).
Run: python experiments/paper1/mem0_full_concurrent.py
"""
import sys, io, json, os, time, re, urllib.request, threading
import concurrent.futures
from collections import defaultdict
from datetime import datetime

# stdout encoding handled by PYTHONIOENCODING env var
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

CKPT = "E:/Temp/mem0_checkpoint.json"
RESULT = "E:/Temp/bench_mem0_result.json"
MAX_WORKERS = 1  # GBM: serial — resp_bridge chokes on concurrent long-haystack extraction — long-haystack extraction overloads resp_bridge  # safe margin below 429 threshold
BATCH_SIZE = 8

# ============ Load data ============
data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))
import os as _os
_test_limit = int(_os.environ.get("MEM0_TEST_LIMIT", "0"))
if _test_limit > 0:
    data = data[:_test_limit]
    print(f"TEST MODE: limited to {len(data)} questions")
else:
    print(f"Loaded {len(data)} questions")

# ============ Load checkpoint ============
done = {}  # idx -> result dict
if os.path.exists(CKPT):
    try:
        ck = json.load(open(CKPT, encoding="utf-8"))
        done = {r["idx"]: r for r in ck.get("results", []) if r.get("status") in ("done", "skipped_long_haystack") or r.get("strict_n") or r.get("judge_n")}
        print(f"Resuming from checkpoint: {len(done)} done")
    except:
        print("No valid checkpoint, starting fresh")
else:
    print("Starting fresh")

# ============ LLM helper (10-concurrent safe) ============
class LLMClient:
    def __init__(self):
        self.base = "http://127.0.0.1:8814/v1/responses"
        self.model = "cn:deepseek-v4.1-flash"
        self.lock = threading.Lock()
        self.call_count = 0
    
    def call(self, prompt, max_output_tokens=4000, retries=3):
        data = json.dumps({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(self.base, data=data, headers={"Content-Type": "application/json"})
        
        last_err = None
        for attempt in range(retries):
            try:
                resp = urllib.request.urlopen(req, timeout=90)
                result = json.loads(resp.read())
                with self.lock:
                    self.call_count += 1
                for item in result.get("output", []):
                    if item.get("type") == "message":
                        for c in item.get("content", []):
                            if c.get("type") == "output_text" and c["text"].strip():
                                return c["text"]
                # completed but no text (all reasoning)
                return ""
            except Exception as e:
                last_err = e
                if "429" in str(e):
                    time.sleep(10 * (attempt + 1))  # backoff on rate limit
                else:
                    time.sleep(10)
        raise Exception(f"LLM failed after {retries} retries: {last_err}")

llm = LLMClient()

# ============ mem0 setup (lazy, per-batch) ============
_memory = None
_memory_lock = threading.Lock()

def get_memory():
    global _memory
    with _memory_lock:
        if _memory is None:
            from mem0_llm_adapter import create_mem0_memory
            _memory = create_mem0_memory("mem0_lme_full", "E:/Temp/mem0_lme_chroma_full")
        return _memory

# ============ Scoring helpers ============
from bench_longmemeval import judge_strict, _key_terms, _looks_like_prose

def score_question(idx, item, memory):
    """Run one question end-to-end. Returns result dict."""
    q_type = item["question_type"]
    question = item["question"]
    gold = str(item["answer"]).strip()
    qid = f"full_{idx}"
    
    result = {"idx": idx, "type": q_type, "n": 1}
    
    # Step 1: Add sessions
    try:
        for session in item.get("haystack_sessions", []):
            messages = [{"role": t["role"], "content": t["content"][:200]} for t in session if t.get("content") and len(t["content"]) > 50]
            if messages:
                memory.add(messages, user_id=qid)
    except Exception as e:
        result["status"] = "add_error"
        result["error"] = str(e)[:100]
        return result
    
    # Step 2: Search
    try:
        search_result = memory.search(question, filters={"user_id": qid}, top_k=10)
        related = search_result.get("results", []) if isinstance(search_result, dict) else search_result
        pred = " | ".join([str(r.get("memory", ""))[:300] for r in (related or [])[:10]])
    except Exception as e:
        result["status"] = "search_error"
        result["error"] = str(e)[:100]
        return result
    
    if not pred:
        result["status"] = "no_memories"
        return result
    
    # Step 3: Strict
    s = judge_strict(pred, item["answer"])
    if s is not None:
        result["strict_n"] = 1
        result["strict"] = 1 if s else 0
    
    # Step 4: Generate
    try:
        gen_prompt = f"Based on the following information, answer the question concisely.\n\nInformation: {pred[:2000]}\n\nQuestion: {question}\n\nAnswer:"
        answer = llm.call(gen_prompt, 500)
    except:
        result["status"] = "gen_error"
        return result
    
    if not answer:
        result["status"] = "gen_empty"
        return result
    
    # Step 5: Judge
    try:
        judge_prompt = f"Question: {question}\nExpected answer: {gold}\nGiven answer: {answer[:200]}\n\nIs the given answer semantically correct? Reply only YES or NO."
        judge_result = llm.call(judge_prompt, 300)
    except:
        result["status"] = "judge_error"
        return result
    
    if judge_result:
        result["judge_n"] = 1
        result["judge"] = 1 if "YES" in judge_result.upper() else 0
        result["status"] = "done"
    else:
        result["status"] = "judge_empty"
    
    return result

# ============ Batch runner ============
def process_batch(indices):
    """Process a batch of questions concurrently."""
    memory = get_memory()
    batch_results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {}
        for idx in indices:
            f = ex.submit(score_question, idx, data[idx], memory)
            futures[f] = idx
        
        for f in concurrent.futures.as_completed(futures):
            idx = futures[f]
            try:
                r = f.result(timeout=600)  # 10 min per question max
                batch_results.append(r)
            except Exception as e:
                batch_results.append({"idx": idx, "status": "thread_error", "error": str(e)[:100]})
    
    return batch_results

# ============ Main loop ============
remaining = []

for i in range(len(data)):
    if i in done:
        continue
    remaining.append(i)
print(f"Remaining: {len(remaining)} questions (no skip — GBM full-denominator run)")
print(f"Concurrency: {MAX_WORKERS}, est. time: ~{len(remaining) * 12 / 3600:.1f}h")
print("=" * 60)

start_ts = time.time()
processed = 0

for batch_start in range(0, len(remaining), BATCH_SIZE):
    batch = remaining[batch_start:batch_start + BATCH_SIZE]
    batch_results = process_batch(batch)
    
    # Update done dict
    for r in batch_results:
        done[r["idx"]] = r
    
    processed += len(batch)
    elapsed = time.time() - start_ts
    rate = processed / elapsed * 3600  # questions per hour
    eta = (len(remaining) - processed) / max(rate, 1) * 3600 / 3600  # hours
    
    # Progress
    strict_done = sum(1 for r in done.values() if r.get("strict_n"))
    strict_hits = sum(r.get("strict", 0) for r in done.values())
    judge_done = sum(1 for r in done.values() if r.get("judge_n"))
    judge_hits = sum(r.get("judge", 0) for r in done.values())
    
    print(f"[{processed}/{len(remaining)}] "
          f"strict={strict_hits}/{strict_done} ({strict_hits/max(strict_done,1)*100:.1f}%) "
          f"judge={judge_hits}/{judge_done} ({judge_hits/max(judge_done,1)*100:.1f}%) "
          f"| {elapsed/60:.0f}min elapsed, ~{eta:.1f}h remaining "
          f"| errors: {sum(1 for r in done.values() if 'error' in r or r.get('status','').endswith('_error'))}")
    
    # Save checkpoint every batch
    with open(CKPT, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now().isoformat(), "last_processed": batch, "results": list(done.values())}, f, indent=2, ensure_ascii=False)

# ============ Final save ============
per_type = defaultdict(lambda: {"n": 0, "strict": 0, "strict_n": 0, "judge": 0, "judge_n": 0})
for r in done.values():
    t = r.get("type", "unknown")
    per_type[t]["n"] += 1
    per_type[t]["strict"] += r.get("strict", 0)
    per_type[t]["strict_n"] += r.get("strict_n", 0)
    per_type[t]["judge"] += r.get("judge", 0)
    per_type[t]["judge_n"] += r.get("judge_n", 0)

strict_total = sum(v["strict_n"] for v in per_type.values())
strict_hits = sum(v["strict"] for v in per_type.values())
judge_total = sum(v["judge_n"] for v in per_type.values())
judge_hits = sum(v["judge"] for v in per_type.values())

output = {
    "ts": datetime.now().isoformat(),
    "system": "mem0",
    "model": "cn:deepseek-v4.1-flash",
    "endpoint": "resp_bridge:8814/v1/responses",
    "n": len(done),
    "strict": round(strict_hits / strict_total, 4) if strict_total else 0,
    "strict_n": strict_total,
    "judge": round(judge_hits / judge_total, 4) if judge_total else 0,
    "judge_n": judge_total,
    "per_type": dict(per_type),
    "details": list(done.values()),
    "total_llm_calls": llm.call_count,
    "total_time_min": round((time.time() - start_ts) / 60, 1),
}
with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n{'='*60}")
print(f"COMPLETE! {len(done)}/{len(data)} questions in {output['total_time_min']}min")
print(f"  strict: {strict_hits}/{strict_total} = {strict_hits/max(strict_total,1)*100:.1f}%")
print(f"  judge:  {judge_hits}/{judge_total} = {judge_hits/max(judge_total,1)*100:.1f}%")
print(f"  LLM calls: {llm.call_count}")
print(f"Saved to {RESULT}")







