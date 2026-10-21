"""MemTether: bi-temporal supersession with governance layer."""

import os
import sys
import shutil
import tempfile

# MemTether is a pip package; no local path injection needed.
# (The previous version hardcoded E:\\RUANJIAN\\memory_hub, which meant the
# documented reproduce path only worked on one machine - exactly the kind of
# unreproducible claim the docs are supposed to avoid.)

from openai import OpenAI

from agents_memory.locomo import extract_dialogues
from agents_memory.systems._helpers import _qa_results

SYSTEM_INFO = {
    "architecture": "bi-temporal supersession with governance layer",
    "infrastructure": "SQLite + local onnx embedding + governance",
}

_ANSWER_PROMPT_SHORT = (
    "Answer the question concisely (1-5 words) using ONLY the "
    "provided memories. If not found, say 'None'."
)
_ANSWER_PROMPT_NATURAL = (
    "Answer the question concisely but completely using ONLY the "
    "provided memories. If not found, say 'None'."
)

# SiliconFlow embedding config
_SILICONFLOW_KEY = os.environ.get("SILICON_KEY", "")
_SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
_SILICONFLOW_MODEL = "BAAI/bge-m3"


def run(
    conv: dict, llm_model: str, run_judge: bool,
    category_names: dict | None = None, judge_fn: str | None = None,
) -> list[dict]:
    """Run MemTether on a single LoCoMo conversation.

    Uses MemTether's gateway.remember() for ingestion and
    memsearch.search_hybrid() for retrieval. No LLM extraction
    (unlike mem0) -- raw facts are stored directly.
    """

    # Use a temp directory for isolated MemTether DB per conversation
    tmpdir = tempfile.mkdtemp(prefix="memtether_memeval_")
    os.environ["MEM_DB"] = os.path.join(tmpdir, "memory.db")
    os.environ["MEM_SOURCE_GUARD"] = "0"

    # Force reimport with fresh DB
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(("gateway", "memsearch", "governance", "predicates")):
            del sys.modules[mod_name]

    from gateway import remember
    from memsearch import search_hybrid

    # LLM client for answer generation (use SiliconFlow free models)
    # Use 8813 pool (free)
    llm_key = "dummy"
    llm_base = "http://127.0.0.1:8813/v1"
    llm_name = "cn:glm-5.3-flash"

    client = OpenAI(api_key=llm_key, base_url=llm_base)

    dialogues = extract_dialogues(conv)
    sample_id = conv.get("sample_id", "unknown")

    # --- 1. INGEST: store dialogues in MemTether ---
    ingested = 0
    for d in dialogues:
        text = f"[{d['speaker']}] ({d['timestamp']}): {d['text']}"
        try:
            remember(text, source='codex')
            ingested += 1
        except Exception as e:
            print(f"    Ingest error: {e}")

    print(f"    Dialogues ingested: {ingested}/{len(dialogues)}")

    sys_prompt = (
        _ANSWER_PROMPT_NATURAL if judge_fn == "longmemeval"
        else _ANSWER_PROMPT_SHORT
    )

    def answer_fn(question: str) -> str:
        try:
            result = search_hybrid(question, limit=20)
            results = result.get("results", [])
            memory_text = "\n".join(
                r.get("content", "")[:300] for r in results[:20]
            )
        except Exception as e:
            print(f"    Search error: {e}")
            memory_text = ""

        if not memory_text.strip():
            return "None"

        models = [llm_name, "cn:deepseek-v4.1-flash", "cn:minimax-m3", "cn:kimi-k3-1"]
        for m in models:
            for attempt in range(3):
                try:
                    response = client.chat.completions.create(
                        model=m,
                        messages=[
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": f"Memories:\n{memory_text}\n\nQuestion: {question}"},
                        ],
                        max_tokens=50,
                        temperature=0.1,
                    )
                    return response.choices[0].message.content.strip()
                except Exception as e:
                    if "429" in str(e) or "402" in str(e):
                        import time as _t
                        _t.sleep(3)
                    else:
                        break
        return "None"

    results = _qa_results(
        conv, answer_fn, run_judge,
        category_names=category_names, judge_fn=judge_fn,
    )

    # Cleanup temp dir
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    return results
