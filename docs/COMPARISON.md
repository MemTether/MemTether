# MemTether vs Other Memory Solutions

> Last updated: 2026-11-01

## Quick Comparison

| Feature | MemTether | mem0 | Hindsight | EverOS | Cognee | Engram | Caura | LongMemory |
|---|---|---|---|---|---|---|
| Audit hash chain (tamper-evident) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ broker-side log | ❌ |
| Bi-temporal supersession | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ temporal truth |
| Conflict gate (human adjudication) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Source attribution (mandatory) | ✅ | ⚠️ optional | ❌ | ❌ | ❌ | ❌ | ⚠️ broker namespace | ⚠️ runtime identity |
| Memory Court (visual audit) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Cross-client file-level pointers | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Q-value usage ranking | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Local SQLite (no cloud) | ✅ | ⚠️ | ⚠️ | ✅ | ❌ | ⚠️ | ⚠️ core-api | ✅ sqlite |
| Zero API keys | ✅ | ❌ | ⚠️ | ✅ | ❌ | ✅ | ⚠️ standalone mode | ✅ |
| MCP server | ✅ (10 tools) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (13 tools) |

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


## Governance has (at least) two routes

As of 2026-11-02, "governed memory" is claimed by three different
architectures. We describe them neutrally — each solves a real problem:

| Route | Example systems | What "governance" means there | What it does not include |
|---|---|---|---|
| **Organizational governance** | [Caura](https://github.com/caura-ai/caura) (544★, eToro production) | Fleet trust tiers, policy enforcement, redaction at a local broker, per-agent digests | Per-fact cryptographic verification; human conflict adjudication per pair |
| **Query-semantic governance** | [LongMemory](https://github.com/CaviraOSS/LongMemory) (4.6K★) | Five recall modes with temporal/contradiction/confidence gates; runtime-identity-bound tools | Per-fact hash chain; a court-style human verdict workflow |
| **Evidence governance (MemTether)** | this repo | Per-fact SHA-256 hash chain with anchors; mandatory source attribution; conflicts queue to a human verdict that joins the audited record | Fleet management, org policy streams, LLM enrichment |

**How to choose:** if you need fleet-wide policy and org dashboards, Caura's
route is built for that. If you need semantically filtered recall modes,
LongMemory's is. If you need *provable* memory integrity that anyone can
verify with one command and no dependencies — that is what MemTether
optimizes for. The routes can coexist; they are not mutually exclusive.

## License

Apache-2.0 — free for commercial use.
