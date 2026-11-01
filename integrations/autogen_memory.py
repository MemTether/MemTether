"""
AutoGen + MemTether: memory poisoning protection for multi-agent conversations.

MemTether's hash-chained audit log makes memory tampering detectable.
Source attribution traces which agent wrote what.

Usage: pip install pyautogen memtether
"""

import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "autogen_memory.db")

import memtether
from gateway import correct, as_of


def autogen_react_hook(agent_name: str, message: str) -> dict:
    """Call this after each AutoGen agent response to record it in MemTether.
    
    Usage in AutoGen:
        agent.register_reply(
            trigger=lambda agent, messages: True,
            reply_func=lambda agent, messages: autogen_react_hook(agent.name, messages[-1]["content"])
        )
    """
    r = memtether.remember(message[:500], type="fact", source=agent_name)
    return r


def autogen_memory_recall(query: str, limit: int = 5) -> list:
    """Call this before each AutoGen agent turn to recall relevant memories."""
    results = memtether.search(query, limit=limit)
    return results.get("results", [])


def autogen_memory_audit(uid: str) -> str:
    """Run Memory Court audit on a specific memory to check for tampering."""
    import subprocess
    r = subprocess.run(["memtether", "court", uid],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout
