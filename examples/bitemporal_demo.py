"""Demonstrate bi-temporal queries: "what did we know at time X?"

MemTether tracks two time axes:
  T  (valid time): when the fact was true in the real world
  T-prime (recorded time): when the system learned about it

This lets you answer "what did the system believe on 2026-09-15?"
which is different from "what was actually true on 2026-09-15?"

Run: python examples/bitemporal_demo.py
"""
import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "bitemporal.db")

from gateway import remember, as_of

# Write a fact with a specific valid_from date
# (simulating: this fact became true on 09-15, but we're recording it now)
r = remember(
    "DeepSeek API key is valid and has $10 balance",
    source="codex",
    valid_from="2026-09-15"
)
uid = r["uid"]
print(f"Written: fact with valid_from=2026-09-15")

# Now suppose the key expired on 09-16 but we only found out on 09-18
from gateway import correct, retire

# Retire the old fact with valid_to = 09-16 (when it actually expired)
r2 = retire(uid, "API key expired on 09-16", by_agent="codex")
print(f"Retired: fact expired on 2026-09-16")

# Now query as-of different dates:
print("\n--- As-of queries ---")

# What was true on 09-15 12:00?
result1 = as_of("2026-09-15 12:00", kind="valid")
print(f"\n[T-axis] What was TRUE on 2026-09-15 12:00?")
print(f"  Active facts: {len(result1.get('rows', []))}")
for row in result1.get("rows", [])[:3]:
    print(f"    {row.get('content', '')[:60]}")

# What did the system KNOW on 09-15?
result2 = as_of("2026-09-15 12:00", kind="known")
print(f"\n[T-prime axis] What did the system KNOW on 2026-09-15 12:00?")
print(f"  Active facts: {len(result2.get('rows', []))}")
for row in result2.get("rows", [])[:3]:
    print(f"    {row.get('content', '')[:60]}")

# What did the system know on 09-18 (after discovering the expiry)?
result3 = as_of("2026-09-18 12:00", kind="known")
print(f"\n[T-prime axis] What did the system KNOW on 2026-09-18 12:00?")
print(f"  Active facts: {len(result3.get('rows', []))}")

print("\n" + "=" * 60)
print("KEY INSIGHT: On 09-15, the key was ALREADY expired in reality (T-axis),")
print("but the system still THOUGHT it was valid (T-prime axis).")
print("Only by tracking both axes can you reconstruct historical beliefs.")
print("=" * 60)
