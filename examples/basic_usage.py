"""Basic usage: remember + search + supersede + retire.

Run: python examples/basic_usage.py
Requires: pip install memtether
"""
import os, tempfile, sqlite3

# Use a temp DB so we don't touch any real data
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "demo.db")

from gateway import remember, search, correct, retire

# 1. Write a memory
r = remember("User prefers dark theme and uses Vim keybindings", source="claude-code")
print(f"[write] uid={r['uid'][:20]}... ok={r['ok']}")

# 2. Search for it
results = search("theme preference")
print(f"[search] found {len(results.get('results', []))} results")
for x in results.get("results", [])[:3]:
    print(f"  - {x['content'][:60]}")

# 3. Supersede (update, old version is preserved)
uid = r["uid"]
r2 = correct(uid, "User now prefers light theme", "changed preference", by_agent="cursor")
print(f"[supersede] old={uid[:15]}... -> new={r2['new_uid'][:15]}...")

# 4. Search again - should find the new version
results2 = search("theme preference")
print(f"[search after supersede] found {len(results2.get('results', []))} results")

# 5. Retire (no longer relevant)
new_uid = r2["new_uid"]
r3 = retire(new_uid, "Testing cleanup", by_agent="codex")
print(f"[retire] ok={r3['ok']}")

print("\nDone! All operations completed successfully.")
