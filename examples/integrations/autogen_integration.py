"""
AutoGen + MemTether: memory poisoning protection for multi-agent conversations.

AutoGen agents with teachability can have their memory poisoned by adversarial
inputs. MemTether adds: hash-chained audit (tamper detection), source
attribution (trace which agent wrote what), and conflict gating.

Usage: pip install pyautogen memtether
"""

import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "autogen_memory.db")

import memtether
from gateway import correct, as_of

# Agent A writes a "fact" that could be poisoned
poisoned_fact = "The API key for service X is sk-admin-bypass-12345"
r1 = memtether.remember(poisoned_fact, type="fact", source="agent-A")
print(f"[Agent-A] wrote: {poisoned_fact[:50]}...")

# Agent B writes a contradictory fact
safe_fact = "The API key for service X is sk-user-readonly-67890"
r2 = memtether.remember(safe_fact, type="fact", source="agent-B")
print(f"[Agent-B] wrote: {safe_fact[:50]}...")

# MemTether detects the conflict — check what happened
results = memtether.search("API key service X", limit=5)
for hit in results.get("results", []):
    print(f"  [{hit.get('source','?')}] {hit['content'][:60]}")

# The audit chain catches tampering
# If someone edits the DB directly: memtether court --verify -> FAIL

# Agent C corrects the poisoned fact (supersede, not delete)
from gateway import correct
r3 = correct(r1["uid"],
    "The API key for service X was revoked. Use OAuth instead.",
    reason="security review: credential rotation",
    by_agent="agent-C")
print(f"\n[Agent-C] corrected poisoned fact -> {r3['new_uid']}")

# Query what the system believed BEFORE the correction
from gateway import as_of
past = as_of("2026-01-01", kind="valid")
print(f"[Forensics] facts valid at 2026-01-01: {past['count']}")

# Show the audit trail for the corrected memory
import subprocess
court = subprocess.run(["memtether", "court", r3["new_uid"]],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
print("\n" + court.stdout[:600])
