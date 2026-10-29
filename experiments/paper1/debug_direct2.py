import json, urllib.request, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from mem0.memory.main import ADDITIVE_EXTRACTION_PROMPT
from mem0.configs.prompts import generate_additive_extraction_prompt
from mem0.memory.utils import parse_messages

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

full_input = ADDITIVE_EXTRACTION_PROMPT + "\n\n" + user_prompt
print(f"Total prompt: {len(full_input)} chars")

data = json.dumps({
    "model": "cn:glm-5.3-flash",
    "input": full_input,
    "max_output_tokens": 1000,
}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8814/v1/responses", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=180)
result = json.loads(resp.read())
print("status:", result.get("status"))
print("usage:", result.get("usage"))
for item in result.get("output", []):
    if item.get("type") == "message":
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                print("text:", repr(c["text"][:300]))
