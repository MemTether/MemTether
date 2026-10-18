# -*- coding: utf-8 -*-
"""trigger_detector.py - Proactive Memory Recall Trigger.

System-level automatic memory retrieval: detect known entities in user
messages and auto-trigger memory search. No LLM, deterministic, <2ms.

Usage:
    from trigger_detector import detect_triggers, auto_context
    triggers = detect_triggers(user_message)
    if triggers:
        context = auto_context(triggers)
"""
import os, re, sys, json, time, sqlite3

HUB = os.path.dirname(os.path.abspath(__file__))
_ENTITY_INDEX = None
_ENTITY_INDEX_TS = 0

# Stop words that should never trigger memory recall
STOP_WORDS = frozenset({
    'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her',
    'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how',
    'its', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'did', 'yes',
    'this', 'that', 'with', 'have', 'from', 'they', 'will', 'would',
    'there', 'their', 'what', 'about', 'which', 'when', 'make', 'like',
    'time', 'just', 'know', 'take', 'into', 'year', 'your', 'good',
    'some', 'them', 'other', 'than', 'then', 'look', 'only', 'come',
    'over', 'also', 'back', 'after', 'use', 'two', 'how', 'our', 'work',
    'first', 'well', 'even', 'want', 'because', 'these', 'give', 'most', 'tell', 'joke', 'hello', 'world', 'today', 'yesterday', 'tomorrow',
})


def _get_db_path(db_path=None):
    if db_path:
        return db_path
    return os.environ.get("MEM_DB", os.path.join(HUB, "memory.db"))


def _load_entity_index(db_path):
    global _ENTITY_INDEX, _ENTITY_INDEX_TS
    db_ts = os.path.getmtime(db_path) if os.path.exists(db_path) else 0
    if _ENTITY_INDEX is not None and _ENTITY_INDEX_TS == db_ts:
        return _ENTITY_INDEX
    conn = sqlite3.connect(db_path)
    entities = set()
    try:
        rows = conn.execute(
            "SELECT DISTINCT entity_name FROM fact_entities "
            "WHERE entity_name IS NOT NULL AND length(entity_name) >= 3"
        ).fetchall()
        import re as _re
        _noise = _re.compile(r'^[a-f0-9]{16,}$')  # hash fragments
        _regex_meta = _re.compile(r'[\[\]\^\$\*\+\?\(\)\|{}\\]')  # contains regex meta-chars
        _frag = _re.compile(r'^[a-z]{1,2}(-[a-z0-9]{1,4})+$')  # regex char-class fragments like a-za-z0-9
        _hex = _re.compile(r'^[a-f0-9]{5,}$')  # hex hashes/ids (5+ chars)
        for r in rows:
            e = r[0].strip().lower()
            if len(e) >= 3 and e not in STOP_WORDS and not _noise.match(e) and not _regex_meta.search(e) and not _frag.match(e) and not _hex.match(e):
                entities.add(e)
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT DISTINCT name AS asset_name FROM tool_assets "
            "WHERE asset_name IS NOT NULL AND length(asset_name) >= 3"
        ).fetchall()
        for r in rows:
            e = r[0].strip().lower()
            if len(e) >= 3 and e not in STOP_WORDS:
                entities.add(e)
    except Exception:
        pass
    conn.close()
    _ENTITY_INDEX = entities
    _ENTITY_INDEX_TS = db_ts
    return entities


def tokenize_message(message):
    if not message:
        return set()
    tokens = set()
    # English words (3+ chars)
    for m in re.finditer(r'[a-zA-Z][a-zA-Z0-9_-]{2,}', message):
        t = m.group().lower()
        if t not in STOP_WORDS and len(t) >= 3:
            tokens.add(t)
    # Chinese phrases (2-6 chars)
    for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', message):
        tokens.add(m.group())
    # Technical terms with dots (gateway.py etc.)
    for m in re.finditer(r'\w+\.\w{1,4}\b', message):
        t = m.group().lower()
        if t not in STOP_WORDS:
            tokens.add(t)
    return tokens


def detect_triggers(user_message, db_path=None):
    if not user_message or len(user_message) < 2:
        return []
    db = _get_db_path(db_path)
    if not os.path.exists(db):
        return []
    entity_index = _load_entity_index(db)
    if not entity_index:
        return []
    tokens = tokenize_message(user_message)
    matched = []
    for token in tokens:
        if token in entity_index:
            matched.append(token)
        elif len(token) >= 4:
            # Substring match for longer entities only
            for entity in entity_index:
                if len(entity) >= 5 and token in entity:
                    matched.append(entity)
                    break
    if not matched:
        return []
    matched = sorted(set(matched), key=len, reverse=True)[:10]
    return [{
        "trigger_type": "entity_match",
        "matched_entities": matched,
        "keywords": matched[:5],
        "message_snippet": user_message[:100],
        "timestamp": time.time(),
    }]


def auto_context(triggers, db_path=None, limit=5):
    if not triggers:
        return ""
    db = _get_db_path(db_path)
    keywords = []
    for t in triggers:
        keywords.extend(t.get("keywords", []))
    if not keywords:
        return ""
    context_parts = []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    for kw in keywords[:3]:
        rows = conn.execute(
            "SELECT content, source FROM facts "
            "WHERE status='active' AND content LIKE ? LIMIT ?",
            (f"%{kw}%", limit)
        ).fetchall()
        for r in rows:
            context_parts.append(f"[{r['source']}] {r['content'][:200]}")
    conn.close()
    if not context_parts:
        return ""
    header = f"<!-- proactive-memory-recall: {len(context_parts)} results -->"
    return f"{header}\n" + "\n".join(context_parts[:limit * 3])


def reset_cache():
    global _ENTITY_INDEX, _ENTITY_INDEX_TS
    _ENTITY_INDEX = None
    _ENTITY_INDEX_TS = 0


if __name__ == "__main__":
    os.environ["MEM_DB"] = r"E:\RUANJIAN\memory_hub\memory.db"
    import random, string
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tests = [
        ("帮我看一下 packy 通道的状态", True),
        ("check memtether gateway.py", True),
        ("clash 端口是多少", True),
        ("What is the capital of France?", False),
        ("Tell me a joke", False),
        ("How are you today?", False),
    ]
    print("=== Trigger tests ===")
    for msg, expected in tests:
        t = detect_triggers(msg)
        ok = (len(t) > 0) == expected
        mark = "PASS" if ok else "FAIL"
        ents = t[0]["matched_entities"][:3] if t else []
        print(f"  [{mark}] '{msg[:35]}' -> {len(t)} triggers {ents}")
    # Random
    fp = 0
    random.seed(42)
    for i in range(100):
        msg = " ".join(random.choices(string.ascii_lowercase, k=5) for _ in range(5))
        if detect_triggers(msg):
            fp += 1
    print(f"\nRandom text false positives: {fp}/100")
    # Latency
    start = time.perf_counter()
    for i in range(200):
        detect_triggers("帮我看一下 packy 和 clash")
    elapsed = (time.perf_counter() - start) / 200 * 1000
    print(f"Latency: {elapsed:.2f} ms")
