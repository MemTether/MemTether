import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")

from mem0.memory.main import ADDITIVE_EXTRACTION_PROMPT
from mem0.configs.prompts import generate_additive_extraction_prompt
from mem0.memory.utils import remove_code_blocks

prompt = generate_additive_extraction_prompt(
    existing_memories=[],
    new_messages=[{"role": "user", "content": "My name is Alice and I like pizza"}],
    last_k_messages=[{"role": "user", "content": "My name is Alice and I like pizza"}],
    custom_instructions=None,
)

import urllib.request
data = json.dumps({
    "model": "best",
    "messages": [
        {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
        {"role": "user", "content": prompt}
    ],
    "max_tokens": 500,
    "temperature": 0.1,
}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8800/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=120)
    result = json.loads(resp.read())
    content = result['choices'][0]['message']['content']
    print("=== LLM Response ===")
    print(repr(content[:600]))
    cleaned = remove_code_blocks(content)
    print("\n=== After remove_code_blocks ===")
    print(repr(cleaned[:300]))
    parsed = json.loads(cleaned, strict=False)
    print("\n=== Parsed OK ===")
    print(parsed)
except Exception as e:
    print(f"Error: {e}")
