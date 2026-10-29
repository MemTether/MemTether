"""mem0 custom LLM that calls resp_bridge /v1/responses endpoint."""
import urllib.request, json, logging, time

class ResponsesBridgeLLM:
    """Adapts resp_bridge /v1/responses to mem0's expected generate_response interface."""
    def __init__(self, config=None):
        self.config = config
        self.base_url = "http://127.0.0.1:8814/v1/responses"
        self.model = "cn:deepseek-v4.1-flash"
    
    def generate_response(self, messages, response_format=None, tools=None, tool_choice="auto", **kwargs):
        # Flatten messages
        prompt = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                prompt += content + "\n\n"
            elif role == "assistant":
                prompt += content + "\n\n"
            else:
                prompt += content + "\n\n"
        
        data = json.dumps({
            "model": self.model,
            "input": prompt,
            "max_output_tokens": 12000,
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(self.base_url, data=data, headers={"Content-Type": "application/json"})
        
        last_err = None
        for attempt in range(3):
            try:
                resp = urllib.request.urlopen(req, timeout=60)
                result = json.loads(resp.read())
                for item in result.get("output", []):
                    if item.get("type") == "message":
                        for c in item.get("content", []):
                            if c.get("type") == "output_text":
                                return c["text"]
                # Status completed but no text content (all reasoning)
                usage = result.get("usage", {})
                logging.warning(f"LLM returned no text (reasoning={usage.get('output_tokens_details',{}).get('reasoning_tokens',0)}, total={usage.get('output_tokens',0)})")
                return ""
            except Exception as e:
                last_err = e
                logging.warning(f"LLM attempt {attempt+1} failed: {e}")
                time.sleep(2)
        raise Exception(f"All LLM attempts failed: {last_err}")

def create_mem0_memory(collection_name="mem0_lme_eval_final", path="E:/Temp/mem0_lme_chroma"):
    from mem0 import Memory
    
    config = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "cn:deepseek-v4.1-flash",
                "openai_base_url": "http://127.0.0.1:8814/v1",
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
                "collection_name": collection_name,
                "path": path + "/run_" + str(int(time.time())),
            }
        },
        "history_db_path": "E:/Temp/mem0_history_run_1790985143.db",
    }
    
    memory = Memory.from_config(config)
    memory.llm = ResponsesBridgeLLM()
    return memory





