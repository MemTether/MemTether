"""llm_client.py — optional LLM client for entity extraction and relation classification.

OpenAI-compatible /v1/chat/completions API. Zero external dependencies (urllib).
If MEM_LLM_BASE_URL is not set, all functions return None and the system
falls back to regex/bge — body-guard pattern, LLM is never a hard dependency.
"""
import os
import json
import sys
import urllib.request
import urllib.error
import time

def is_available():
    """LLM 是否已配置（MEM_LLM_BASE_URL 已设置）。"""
    return bool(os.environ.get("MEM_LLM_BASE_URL"))

def _config():
    return {
        "base_url": os.environ.get("MEM_LLM_BASE_URL", "").rstrip("/"),
        "api_key": os.environ.get("MEM_LLM_API_KEY", ""),
        "model": os.environ.get("MEM_LLM_MODEL", "gpt-4o-mini"),
        "max_tokens": int(os.environ.get("MEM_LLM_MAX_TOKENS", "512")),
        "temperature": float(os.environ.get("MEM_LLM_TEMPERATURE", "0.1")),
        "timeout": int(os.environ.get("MEM_LLM_TIMEOUT", "30")),
    }

class LLMError(Exception):
    pass

def chat(messages, max_tokens=None, temperature=None):
    """调 OpenAI-compatible /v1/chat/completions。返回 str（assistant 回复）。

    零新依赖：urllib.request 直连。超时/网络错误 raise LLMError。
    """
    cfg = _config()
    url = cfg["base_url"] + "/chat/completions"
    mt = max_tokens or cfg["max_tokens"]
    temp = temperature if temperature is not None else cfg["temperature"]

    body = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": mt,
        "temperature": temp,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if cfg["api_key"]:
        req.add_header("Authorization", f"Bearer {cfg['api_key']}")

    for attempt in range(2):  # 1 retry
        try:
            resp = urllib.request.urlopen(req, timeout=cfg["timeout"])
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"] or ""
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, OSError) as e:
            if attempt == 0:
                time.sleep(1)
                continue
            raise LLMError(f"LLM API failed after retry: {e}") from e
    raise LLMError("LLM API failed: unreachable")


def extract_entities(text):
    """用 LLM 提取 entity。返回 list[dict] 或 None（fallback to regex）。

    输出验证：每个 entity 必须是原文的子串（防止 LLM 幻觉）。
    """
    if not is_available():
        return None

    system = (
        "You extract entities and their attributes from text. "
        "You always respond with ONLY a JSON array. No explanation, no markdown.\n"
        "Each item: {\"entity\": \"name\", \"attr\": \"attribute\"}\n"
        "Rules:\n"
        "- entity is a specific thing name (service, tool, database, person, etc.)\n"
        "- attr is what the text says about that entity\n"
        "- entity name MUST appear in the original text\n"
        "- skip generic words like 'user', 'system', 'the', 'it'\n"
        "- max 5 entities"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Text: " + text + "\nJSON:"},
    ]

    try:
        result = chat(messages)
    except LLMError:
        return None

    # Parse JSON from result
    import re
    json_match = re.search(r"\[.*?\]", result, re.DOTALL)
    if not json_match:
        return None

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, list):
        return None

    # Validate: entity must be a substring of the original text (anti-hallucination)
    text_lower = text.lower()
    entities = []
    seen = set()
    for item in parsed[:5]:
        if not isinstance(item, dict):
            continue
        entity = (item.get("entity") or "").strip()
        attr = (item.get("attr") or "").strip()
        if not entity or len(entity) < 2:
            continue
        if entity.lower() not in text_lower:
            continue  # hallucination check
        if entity.lower() in seen:
            continue
        # Skip generic words
        if entity.lower() in ("user", "system", "the", "it", "this", "that", "they", "server"):
            continue
        seen.add(entity.lower())
        entities.append({"entity": entity, "attr": attr})

    return entities if entities else None


def classify_relation(new_fact, existing_fact):
    """判断两条 fact 的关系。返回 str 或 None。

    可能的返回值：new / supersede / append / unrelated
    None = LLM 不可用或判断失败（fallback to bge cosine）
    """
    if not is_available():
        return None

    system = (
        "You classify the relationship between two facts about the same topic.\n"
        "Respond with ONLY one word: new, supersede, append, or unrelated.\n"
        "- new: the new fact introduces a completely new piece of information\n"
        "- supersede: the new fact replaces or corrects the existing fact\n"
        "- append: the new fact adds details to the existing fact without contradicting it\n"
        "- unrelated: the facts are about different topics"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Existing: {existing_fact}\nNew: {new_fact}\nRelation:"},
    ]

    try:
        result = chat(messages, max_tokens=10, temperature=0.0)
    except LLMError:
        return None

    result = result.strip().lower()
    if result in ("new", "supersede", "append", "unrelated"):
        return result
    return None


def reflect(entity, facts):
    """从同一 entity 的多条 fact 推导 observation。返回 str 或 None。

    Hindsight reflect 的简化版：发给 LLM，让它写一段总结。
    """
    if not is_available() or not facts:
        return None

    facts_text = "\n".join(f"- {f}" for f in facts[:10])
    system = (
        "You synthesize observations from a set of facts about the same entity. "
        "Write a concise 2-3 sentence summary of the key pattern or state. "
        "Do not repeat individual facts. Focus on what they collectively mean."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Entity: {entity}\n\nFacts:\n{facts_text}\n\nObservation:"},
    ]

    try:
        result = chat(messages, max_tokens=200, temperature=0.3)
    except LLMError:
        return None

    result = result.strip()
    return result if len(result) > 20 else None
