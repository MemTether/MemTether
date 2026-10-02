"""Demonstrate Q-Value: memories that get used rank higher.

Q-Value is a usage-based ranking factor. When a memory is retrieved
and actually useful, its q_value increases. Next time it is searched,
it ranks higher than memories that have never been used.

This is inspired by reinforcement learning: reward what works.

Run: python examples/qvalue_demo.py
"""
import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "qvalue.db")

from gateway import remember, search, bump_qvalue

# Write two similar memories
r1 = remember("hubguard wraps gateway with concurrent locking", source="codex")
r2 = remember("hubguard also handles atomic writes", source="codex")
print(f"Written 2 memories about hubguard")
print(f"  u1: {r1['uid'][:20]}... q_value=0.5 (default)")
print(f"  u2: {r2['uid'][:20]}... q_value=0.5 (default)")

# Search: both should appear with similar ranking
results = search("hubguard", limit=5)
print(f"\nSearch 'hubguard': {len(results.get('results', []))} results")
for x in results.get("results", []):
    print(f"  score={x.get('score', 0):.4f} q={x.get('q_value', 0.5)} {x['content'][:40]}")

# Now bump Q-Value for the first one (simulating: this memory was useful)
print(f"\nBumping Q-Value for {r1['uid'][:20]}...")
bump_result = bump_qvalue(r1["uid"], reward=1.0, agent="codex")
print(f"  q_value: {bump_result.get('q_before', 0.5):.4f} -> {bump_result.get('q_after', 0.5):.4f}")
print(f"  score_factor: {bump_result.get('score_factor', 0):.4f}")

# Search again: the bumped memory should rank higher
results2 = search("hubguard", limit=5)
print(f"\nSearch again after bump:")
for x in results2.get("results", []):
    print(f"  score={x.get('score', 0):.4f} q={x.get('q_value', 0.5)} {x['content'][:40]}")

print("\n" + "=" * 60)
print("The bumped memory should now rank higher than before.")
print("This is how MemTether learns which memories are actually useful.")
print("=" * 60)
