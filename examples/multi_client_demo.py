"""Demonstrate two AI clients sharing the same memory.

Claude Code writes a memory -> Cursor searches and finds it.
This is the core value proposition of MemTether.

Run: python examples/multi_client_demo.py
"""
import os, tempfile, sqlite3

# Shared DB (simulates what junction/symlink does in production)
shared_db = os.path.join(tempfile.mkdtemp(), "shared_memory.db")
os.environ["MEM_DB"] = shared_db

from gateway import remember, search

print("=" * 60)
print("Multi-Client Shared Memory Demo")
print("=" * 60)

# --- Client A: Claude Code writes ---
print("\n[Client A: Claude Code]")
r1 = remember("User prefers dark theme", source="claude-code")
print(f"  Wrote: 'User prefers dark theme' (uid={r1['uid'][:20]}...)")

r2 = remember("Project uses SQLite + FTS5 for search", source="claude-code")
print(f"  Wrote: 'Project uses SQLite + FTS5' (uid={r2['uid'][:20]}...)")

# --- Client B: Cursor searches ---
print("\n[Client B: Cursor]")
results = search("theme preference")
print(f"  Searched: 'theme preference'")
print(f"  Found {len(results.get('results', []))} results:")
for x in results.get("results", []):
    print(f"    [{x['source']}] {x['content'][:60]}")
    print(f"     ^ This was written by Claude Code, found by Cursor!")

# --- Verify source attribution ---
print("\n[Source attribution check]")
db = sqlite3.connect(shared_db)
db.row_factory = sqlite3.Row
for row in db.execute("SELECT uid, source, content FROM facts WHERE status='active'"):
    print(f"  source={row['source']:<15} content={row['content'][:50]}")
db.close()

print("\n" + "=" * 60)
print("Both clients share the same physical memory.db.")
print("Cursor found memories written by Claude Code.")
print("This is what junction/symlink does in production.")
print("=" * 60)
