import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import create_mem0_memory

memory = create_mem0_memory("mem0_lme_debug", "E:/Temp/mem0_lme_chroma_debug")

# Test add with actual session data
data = json.load(open("E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle", encoding="utf-8"))
item = data[0]
session = item["haystack_sessions"][0]
messages = [{"role": t["role"], "content": t["content"]} for t in session if t.get("content")]
print(f"Adding {len(messages)} messages from session 0...")
print(f"First message: {messages[0]['content'][:100]}...")

try:
    result = memory.add(messages, user_id="debug_user")
    print(f"Add result type: {type(result)}")
    print(f"Add result: {json.dumps(result, indent=2, ensure_ascii=False, default=str)[:500]}")
except Exception as e:
    import traceback
    traceback.print_exc()

# Now search
try:
    results = memory.search("What was the first issue I had with my new car?", filters={"user_id": "debug_user"})
    print(f"\nSearch results: {json.dumps(results, indent=2, ensure_ascii=False, default=str)[:500]}")
except Exception as e:
    import traceback
    traceback.print_exc()
