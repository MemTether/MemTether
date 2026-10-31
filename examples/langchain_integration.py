"""
LangChain + MemTether: persistent memory with audit trails for LangChain agents.

LangChain's built-in memory (ConversationBufferMemory, etc.) is in-process only.
MemTether provides persistent, cross-session, auditable memory.

Usage: pip install langchain langchain-openai memtether
"""

import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "lc_memory.db")

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
import memtether
from gateway import correct, as_of

# Initialize LLM (use any OpenAI-compatible endpoint)
llm = ChatOpenAI(model="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY", "sk-placeholder"))

# Store facts from a conversation in MemTether
conversation = [
    ("user", "My project uses PostgreSQL 15 on port 5432"),
    ("assistant", "Got it. I'll remember your PostgreSQL setup."),
    ("user", "Actually, we migrated to PostgreSQL 16 on port 5433"),
    ("assistant", "Updated. PostgreSQL 16 on port 5433."),
]

print("=== Processing conversation ===")
for role, text in conversation:
    if role == "user" and any(kw in text.lower() for kw in ["postgres", "port", "migrate"]):
        # This is a fact worth remembering
        r = memtether.remember(text, type="fact", source="langchain-user")
        print(f"  stored: {text[:60]} (uid={r['uid']})")

# Later: a new session needs to recall these facts
print("\n=== New session: recalling project context ===")
results = memtether.search("PostgreSQL", limit=5)
for hit in results.get("results", []):
    status = hit.get("status", "active")
    print(f"  [{status}] {hit['content'][:60]} (score={hit.get('score','?')})")

# The key MemTether feature: corrections are supersession, not deletion
print("\n=== Correcting a memory ===")
# Find the old PostgreSQL memory
old = results["results"][0] if results.get("results") else None
if old:
    from gateway import correct
    r = correct(old["uid"], "PostgreSQL 16 on port 5433 (confirmed by DBA)",
                reason="verified with DBA", by_agent="dba-review")
    print(f"  corrected -> {r['new_uid']} (old: {r['old_uid']} superseded)")
    
    # The old value is still queryable
    past = as_of("2026-01-01", kind="known")
    print(f"  forensic query: {past['count']} memories known at 2026-01-01")
