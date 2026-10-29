import json, urllib.request, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Direct test with the full prompt to see what goes wrong
from mem0.memory.main import ADDITIVE_EXTRACTION_PROMPT

prompt = ADDITIVE_EXTRACTION_PROMPT + "\n\nTest: return {\"memory\": []}"
print(f"Prompt length: {len(prompt)} chars, ~{len(prompt)//4} tokens")

data = json.dumps({
    "model": "cn:glm-5.3-flash",
    "input": prompt,
    "max_output_tokens": 800,
}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8814/v1/responses", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=180)
result = json.loads(resp.read())
print("status:", result.get("status"))
print("usage:", result.get("usage"))
for item in result.get("output", []):
    print("output type:", item.get("type"))
    if item.get("type") == "message":
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                print("text:", repr(c["text"][:200]))
