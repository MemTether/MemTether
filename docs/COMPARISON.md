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


## vs tigerless-labs/agent-memory (4.3K stars, the closest architectural rival)

Last audited from source 2026-11-04 (full clone, 246 files / 2,074 functions /
859 test functions read via AST + key files line-by-line; report:
`workdocs/memtether/教材-agent-memory-20261104.md`).

| Dimension | MemTether | agent-memory |
|---|---|---|
| Truth source | SQLite row = fact, SHA-256 row chain | Markdown file = fact, SQLite is a rebuildable cache |
| Read model | hybrid retrieval (BM25+vector RRF + rerank + Q-Value + verify_at decay) | MEMORY.md injection + BM25 FTS5 + plain `ls/grep` tree (three tracks) |
| Write model | every entry through `gateway.remember` (source guard / dedup / cross-source echo gate / incident cooldown / audit anchor) | every entry through `store.record` / `record_many` (validate → hash-diff → reproject), Manage rewrites share the same path |
| Sleep-time | `nrem sleep`: deterministic exact-dup proposals, cap 20, **dry-run by default**, `--apply` goes through governed `retire` + audit chain | Manage: menu-bounded proposals (merge/split/supersede/delete), per-kind caps, DreamReport + auto git commit, claims with lease recovery |
| Deletion | `retire` marks superseded/retired; rows preserved | `delete` ends the validity interval; file preserved; physical removal only via human `gc()` |
| Freshness | `verify_at` staleness + JIT path existence check + search-time auto-renewal | none (valid_from/invalid_at intervals only) |
| Conflict handling | human adjudication gate (`conflicts --accept/--discard`) wired to Q-Value | none (duplicate supersession only) |
| Integrity proof | per-fact hash chain + `court --verify` + external anchors + exportable dossier | file bytes are the proof; no chain concept |
| Packaging | PyPI stable 0.1.0 + GitHub Packages npm + MCP Registry | git clone + `uv sync` only; **no PyPI release** |
| Benchmark evidence | `docs/EVIDENCE.md` registry: every number carries date/denominator/reproduce command; `test_docs_claims.py` mechanically locks README numbers | README quotes LongMemEval numbers; referenced `docs/experiments.md` / `experiments/` are **404 in the repo** |
| Windows | maintainer's daily-driver OS; all tests run on Windows CI + locally | TODO admits Windows hooks unverified on live Codex install; long prompts can exceed Windows command-line limits |
| Tests | 675 passed / 6 skipped (Python 3.10, Windows, 2026-11-04) | 859 test functions / 111 files, CI gate `--cov-fail-under=85`, mypy strict-ish |

**Trade-off in one line:** agent-memory wins on human-auditable file trees and
host-CLI write automation; MemTether wins on row-level tamper evidence,
human conflict adjudication, freshness guarantees, and installable packaging.
They share one belief: **unattended consolidation must be proposal-only** —
we adopted that shape (and their single-write-path invariant) in the
2026-11-04 audit; we did not adopt file-as-truth because it conflicts with
the per-row audit chain.

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
