import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "E:/RUANJIAN/memory_hub")

from mem0 import Memory
from mem0.llms.openai import OpenAILLM
from mem0.configs.llms.openai import OpenAIConfig

class PatchedOpenAILLM(OpenAILLM):
    """Override generate_response to strip response_format (tabbit:8800 doesn't support it)."""
    def generate_response(self, messages, response_format=None, tools=None, tool_choice="auto", **kwargs):
        # Call parent but strip response_format
        import logging
        params = self._get_supported_params(messages=messages, **kwargs)
        params.update({"model": self.config.model, "messages": messages})
        # Intentionally skip response_format and tools
        response = self.client.chat.completions.create(**params)
        parsed_response = self._parse_response(response, tools)
        return parsed_response

config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "best",
            "openai_base_url": "http://127.0.0.1:8800/v1",
            "api_key": "dummy",
            "temperature": 0.1,
        }
    },
    "embedder": {
        "provider": "huggingface",
        "config": {
            "model": "all-MiniLM-L6-v2",
        }
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "mem0_lme_eval_v3",
            "path": "E:/Temp/mem0_lme_chroma",
        }
    },
    "history_db_path": "E:/Temp/mem0_history.db",
}

memory = Memory.from_config(config)
# Monkey-patch the LLM
memory.llm = PatchedOpenAILLM(memory.llm.config)

print("Adding messages...")
memory.add([{"role": "user", "content": "My name is Alice and I like pizza"}], user_id="test_user6")
print("Add done")

results = memory.search("What is my name?", filters={"user_id": "test_user6"})
print(f"Search returned: {type(results)}")
print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
