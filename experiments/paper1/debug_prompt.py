import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import ResponsesBridgeLLM
from mem0.memory.main import ADDITIVE_EXTRACTION_PROMPT
from mem0.configs.prompts import generate_additive_extraction_prompt
from mem0.memory.utils import remove_code_blocks, parse_messages

llm = ResponsesBridgeLLM(None)  # config=None, will use default model

messages = [
    {"role": "user", "content": "I'm thinking of getting my car detailed soon. I just got my car serviced for the first time on March 15th."},
    {"role": "assistant", "content": "Glad the service went well! What kind of detailing are you looking for?"},
]
parsed = parse_messages(messages)

user_prompt = generate_additive_extraction_prompt(
    summary=None,
    recently_extracted_memories=None,
    existing_memories=[],
    new_messages=parsed,
    last_k_messages=messages,
    custom_instructions=None,
)

response = llm.generate_response(
    messages=[
        {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
        {"role": "user", "content": user_prompt}
    ],
    response_format={"type": "json_object"}
)

print("=== LLM Response ===")
print(repr(response[:500]) if response else "None")
cleaned = remove_code_blocks(response)
print("\n=== Cleaned ===")
print(repr(cleaned[:300]) if cleaned else "None")
try:
    r = json.loads(cleaned, strict=False)
    print(f"\n=== Parsed: {len(r.get('memory', []))} memories ===")
    print(r)
except Exception as e:
    print(f"Parse error: {e}")
