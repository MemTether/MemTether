# MemTether vs Other Memory Solutions

> Last updated: 2026-11-01

## Quick Comparison

| Feature | MemTether | mem0 | Hindsight | EverOS | Cognee | Engram |
|---|---|---|---|---|---|---|
| Audit hash chain (tamper-evident) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Bi-temporal supersession | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Conflict gate (human adjudication) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Source attribution (mandatory) | ✅ | ⚠️ optional | ❌ | ❌ | ❌ | ❌ |
| Memory Court (visual audit) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Cross-client file-level pointers | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Q-value usage ranking | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Local SQLite (no cloud) | ✅ | ⚠️ | ⚠️ | ✅ | ❌ | ⚠️ |
| Zero API keys | ✅ | ❌ | ⚠️ | ✅ | ❌ | ✅ |
| MCP server | ✅ (10 tools) | ✅ | ✅ | ✅ | ✅ | ✅ |

## Why Audit Matters

Most memory servers store what agents tell them. But what happens when:
- An agent writes a **wrong fact** and another agent acts on it?
- An agent's memory is **tampered with** (accidentally or maliciously)?
- Two agents write **contradictory facts** and nobody notices?
- You need to know **what the agent believed last Tuesday** (not today)?

MemTether solves all four problems with a hash-chained audit log, bi-temporal
supersession, and a conflict gate that requires human adjudication.

**No other memory solution offers any of these features.**

## Detailed Comparison

### MemTether vs mem0

| | MemTether | mem0 |
|---|---|---|
| Stars | 1 | 68K |
| Focus | Memory governance | Memory recall |
| Audit chain | SHA-256 hash chain | ❌ |
| Corrections | Supersession (never delete) | Overwrite |
| Source tracking | Mandatory per-write | Optional |
| Conflict handling | Human adjudication gate | Last-write-wins |
| Storage | Local SQLite | Multi-backend (cloud available) |
| tamper detection | Hash chain verification | ❌ |

**Use mem0 if:** You want a battle-tested, widely-adopted memory layer with SaaS support.

**Use MemTether if:** You need to audit, trace, and govern what your agents remember — especially in security-sensitive or multi-agent environments where memory corruption has real consequences.

### MemTether vs Hindsight

| | MemTether | Hindsight |
|---|---|---|
| Stars | 1 | 57K |
| Focus | Memory governance | Agent memory that learns |
| Audit chain | SHA-256 hash chain | ❌ |
| Bi-temporal | ✅ (formal model) | ⚠️ (temporal but not bi-temporal) |
| Learning | ❌ | ✅ (learns from interactions) |
| Corporate backing | ❌ | Vectorize.io |
| Paper | In progress | arXiv |

**Use Hindsight if:** You want learning-based memory with corporate support and don't need audit trails.

**Use MemTether if:** You need provable memory integrity and don't want a black box.

### MemTether vs EverOS

| | MemTether | EverOS |
|---|---|---|
| Stars | 1 | 13.7K |
| Focus | Memory governance | Portable memory layer |
| Local-first | ✅ | ✅ |
| Audit chain | ✅ | ❌ |
| Conflict gate | ✅ | ❌ |
| Corporate backing | ❌ | EverMind-AI |

**Use EverOS if:** You want a portable memory layer with active community and corporate support.

**Use MemTether if:** You need memory governance features that EverOS doesn't provide.

## Migration

### From mem0

```python
# Before (mem0)
from mem0 import Memory
m = Memory()
m.add("fact", user_id="user1")

# After (MemTether)
import memtether
memtether.remember("fact", source="my-agent")
```

### From plain JSON/dict

```python
# Before
memory = {}

# After
import memtether
memtether.remember("fact", source="my-agent")
```

## License

Apache-2.0 — free for commercial use.
