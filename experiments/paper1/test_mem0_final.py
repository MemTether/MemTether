import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import create_mem0_memory

memory = create_mem0_memory("mem0_lme_test", "E:/Temp/mem0_lme_chroma")
print("Adding...")
memory.add([{"role": "user", "content": "My name is Alice and I like pizza"}], user_id="test_final")
print("Add done")

results = memory.search("What is my name?", filters={"user_id": "test_final"})
print(f"Search: {type(results)}")
print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
