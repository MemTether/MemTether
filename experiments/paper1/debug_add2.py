import sys, io, json, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import create_mem0_memory

memory = create_mem0_memory("mem0_lme_debug3", "E:/Temp/mem0_lme_chroma_debug3")

messages = [
    {"role": "user", "content": "I'm thinking of getting my car detailed soon. I just got my car serviced for the first time on March 15th."},
    {"role": "assistant", "content": "Glad the service went well! What kind of detailing are you looking for?"},
]

# Patch add to capture the actual call
from mem0.memory.main import Memory
import mem0.memory.main as main_mod

original_add = Memory._add_to_vector_store
call_log = []

def patched_add(self, messages, metadata, effective_filters, infer, prompt=None):
    print(f"\n=== _add_to_vector_store called ===")
    print(f"infer={infer}, prompt={prompt}")
    if infer:
        # Print the system prompt and user prompt
        from mem0.memory.main import ADDITIVE_EXTRACTION_PROMPT
        from mem0.configs.prompts import generate_additive_extraction_prompt
        from mem0.memory.utils import parse_messages
        
        parsed = parse_messages(messages)
        print(f"parsed_messages: {parsed}")
        
        existing = []
        try:
            existing = self._get_existing_memories(messages, metadata, effective_filters)
        except:
            pass
        
        user_prompt = generate_additive_extraction_prompt(
            existing_memories=existing,
            new_messages=parsed,
            last_k_messages=parsed[-5:] if len(parsed) > 5 else parsed,
            custom_instructions=prompt,
        )
        
        print(f"\n=== SYSTEM PROMPT (first 300) ===")
        print(ADDITIVE_EXTRACTION_PROMPT[:300])
        print(f"\n=== USER PROMPT (last 500) ===")
        print(user_prompt[-500:])
        
        # Call LLM directly to see response
        response = self.llm.generate_response(
            messages=[
                {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        print(f"\n=== LLM RESPONSE ===")
        print(repr(response[:500]) if response else "None")
        
        from mem0.memory.utils import remove_code_blocks
        cleaned = remove_code_blocks(response)
        print(f"\n=== CLEANED ===")
        print(repr(cleaned[:300]) if cleaned else "None")
        
        try:
            import json as j
            r = j.loads(cleaned, strict=False)
            print(f"\n=== PARSED ===")
            print(r)
            memories = r.get("memory", [])
            print(f"extracted {len(memories)} memories")
        except Exception as e:
            print(f"Parse error: {e}")
        
        # Now call original to let it proceed
        return original_add(self, messages, metadata, effective_filters, infer, prompt)
    return original_add(self, messages, metadata, effective_filters, infer, prompt)

Memory._add_to_vector_store = patched_add

result = memory.add(messages, user_id="debug3_user")
print(f"\n=== FINAL RESULT ===")
print(json.dumps(result, indent=2, ensure_ascii=False, default=str)[:500])
