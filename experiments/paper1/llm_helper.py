import urllib.request, json

def call_llm(prompt, model="cn:deepseek-v4-flash", max_tokens=200, temperature=0.1):
    """Call resp_bridge /v1/responses endpoint. Returns text or None."""
    try:
        data = json.dumps({
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8814/v1/responses",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read())
        # Parse Responses API output
        for item in result.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content["text"]
        return None
    except Exception as e:
        print(f"LLM call error: {e}")
        return None
