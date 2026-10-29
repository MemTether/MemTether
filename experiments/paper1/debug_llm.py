import sys, io, json, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
sys.path.insert(0, "E:/RUANJIAN/memory_hub")
sys.path.insert(0, "E:/RUANJIAN/memory_hub/experiments/paper1")

from mem0_llm_adapter import create_mem0_memory, ResponsesBridgeLLM

memory = create_mem0_memory("mem0_lme_debug2", "E:/Temp/mem0_lme_chroma_debug2")

# Test with the actual messages but check LLM response
messages = [{"role": "user", "content": "I'm thinking of getting my car detailed soon. Do you know any good detailers in the area or have any recommendations? By the way, I just got my car serviced for the first time on March 15th, and it was a great experience."},
{"role": "assistant", "content": "I can help you with car detailing recommendations! To give you the best suggestions, could you let me know your general location or area? For your car service, I'm glad it was a great experience! Regular maintenance like that is important for keeping your car running smoothly."}]

print("Testing direct LLM...")
llm = ResponsesBridgeLLM(memory.llm.config)
response = llm.generate_response(messages=[{"role":"system","content":"Extract facts. Return JSON object with key memory containing list of strings."},{"role":"user","content":str(messages)}], response_format={"type":"json_object"})
print("LLM response:", repr(response[:300]) if response else "None")
