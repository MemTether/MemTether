<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=180&section=header&text=MemTether&fontSize=48&fontColor=fff&animation=fadeIn&desc=Cross-client%20AI%20Memory%20Hub&descSize=18&descAlignY=65&descAlign=center" width="100%" alt="MemTether"/>

<img src="assets/demo-usage.svg" width="100%" alt="MemTether Usage Demo — Two AI clients sharing memory"/>

<img src="assets/demo-architecture.svg" width="100%" alt="MemTether Architecture — 23 clients connected to one memory hub"/>

<h1>Your AI agents can now share memories.</h1>

<b>Local-first - No cloud - No API fees - One physical memory.db shared by 23+ clients</b>

[![PyPI](https://img.shields.io/pypi/v/memtether?color=%2334D058&label=pypi)](https://pypi.org/project/memtether/)
[![Python](https://img.shields.io/pypi/pyversions/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/MemTether/MemTether)](LICENSE)
[![Stars](https://img.shields.io/github/stars/MemTether/MemTether?style=social)](https://github.com/MemTether/MemTether/stargazers)

[Quick Start](#quick-start) | [Why MemTether](#why-memtether) | [Architecture](#architecture) | [Benchmarks](#benchmarks) | [Known Limitations](#known-limitations) | [中文文档](README.zh-CN.md)

</div>

---

## Quick Start

mcp-name: io.github.lanbass869-cell/memtether

```bash
# 1. Install
pip install memtether

# 2. Initialize (creates a demo memory DB)
memtether init

# 3. Connect all detected AI clients (Claude Code, Cursor, Windsurf, etc.)
memtether connect --all
```

Try it:

```bash
memtether search "shared memory"
memtether remember "my first shared memory"
memtether stats
```

Or on Windows, double-click `install.bat` for one-click setup.

<details>
<summary>More install options</summary>

```bash
# From source
git clone https://github.com/MemTether/MemTether.git && cd MemTether && pip install -e .

# With semantic search (local embedding, no cloud)
pip install "memtether[vector]"

# With REST API server
pip install "memtether[server]"

# Docker
docker build -t memtether . && docker run -p 8420:8420 -v ./data:/app/data memtether
docker-compose up
```

Try it without installing:

```bash
python scripts/make_demo_db.py
MEM_DB=~/.memtether/demo.db python mem.py search "shared memory"
```

</details>

---

## Why MemTether

### The problem

You use Claude Code for coding, Cursor for refactoring, and Windsurf for exploration. Each has its own memory. Switch tools and your AI forgets everything.

**MemTether's answer is simpler than you'd expect: make them all point to the same file.**

### How it is different

| Common approach | Problem | MemTether approach |
|---|---|---|
| Per-client memory | Switch tools, lose context | **File-level pointer**: all clients read/write same `memory.db` |
| Cloud-hosted memory | Privacy + API fees + downtime | **Local-first**: SQLite on your machine, zero cloud |
| Delete old memories | Cannot trace what was known | **Supersession**: old memories marked, never deleted |
| Single time axis | Cannot distinguish when true vs when learned | **Bi-temporal**: dual T/T-prime axes with as-of queries |
| Equal treatment of memories | Useful memories get buried | **Q-Value**: used memories rank higher |
| Single search path | Misses keyword matches | **4-path recall**: vector + FTS5 + literal + entity graph |
| No concurrent protection | Simultaneous writes = data loss | **Hubguard**: file lock + atomic write |

### Feature comparison

| Feature | MemTether | mem0 | cognee | zep |
|---|---|---|---|---|
| **Local-first** | Yes | No (cloud) | Yes | No (cloud) |
| **Cross-client shared** | Yes (23) | No | No | No |
| **Source attribution** | Yes | No | No | Yes |
| **Bi-temporal** | Yes | No | No | Yes |
| **Q-Value ranking** | Yes | No | No | No |
| **Supersession** | Yes | No | No | Yes |
| **4-factor re-ranking** | Yes | No | No | No |
| **Scaffolds** | Yes | No | No | No |
| **MCP server** | Yes | Yes | Yes | Yes |
| **Eval suite included** | Yes | Yes | No | No |
| **No API key needed** | Yes | No | No | No |
| **REST API** | Yes | Yes | Yes | Yes |
| **Docker** | Yes | Yes | Yes | Yes |

<details>
<summary>Full feature list</summary>

- **Cross-client shared memory**: 23 adapters (Claude Code, Cursor, Windsurf, VS Code, Zed, JetBrains, Cline, Roo Code, Kilo Code, Continue, Cody, Amazon Q, Gemini CLI, Neovim, Claude Desktop, Codex, WorkBuddy CN/Intl, CodeBuddy, ZCode, DSH, OpenClaw, Agents-Neutral)
- **Source attribution**: every memory knows which client wrote it
- **Bi-temporal**: T (when true) + T-prime (when recorded), as-of queries
- **Supersession**: old memories marked superseded, never deleted, full audit trail
- **Q-Value**: usage-based ranking (0.3 + 0.7 x q_value multiplier)
- **4-factor re-ranking**: semantic (0.45) + recency (0.25) + frequency (0.05) + importance (0.10), blended 70/30 with RRF
- **Deterministic scaffolds**: counting/temporal/comparison/aggregation prepended to top result
- **Consolidation index**: 2708 topics + 315 chains + 37 standing instructions as bonus recall
- **FTS5 triggers**: SQLite-level full-text sync (INSERT/DELETE/UPDATE triggers)
- **Hubguard**: cross-process concurrent write lock + atomic write + format fallback
- **Conflict detection**: 89 quantified conflict patterns
- **LLM auto-extraction**: extract structured memories from conversation text
- **Projection**: auto-generates MEMORY.md (3980 char budget) for context injection
- **Multi-path search**: vector + FTS5 BM25 + literal + entity graph PPR, RRF fused
- **Three-layer dedup**: supersession-aware, content exact, tag-signature
- **Low-confidence rejection**: marks results when keyword empty AND vector < 0.50
- **TTL expiry**: expired conclusions downweighted with annotation
- **Self-reference suppression**: meta-discussion ranked below answers

</details>

---

## Architecture

```
+---------+   +---------+   +---------+   +---------+
|  Claude |   | Cursor  |   |Windsurf |   | VS Code |  ... 23 adapters
|  Code   |   |         |   |         |   |         |
+----+----+   +----+----+   +----+----+   +----+----+
     |              |              |              |
     +--------------+------+-------+--------------+
                          |
                   +------v------+
                   |  MemTether  |
                   | Memory Hub  |
                   |             |
                   | SQLite      |  <- one physical memory.db
                   | FTS5        |  <- full-text search (triggers)
                   | ChromaDB    |  <- vector search (bge-m3)
                   | Bi-temporal |  <- T + T-prime dual time axes
                   | Q-Value     |  <- usage-based ranking
                   | Hubguard    |  <- concurrent write lock
                   +-------------+
```

<details>
<summary>Tech stack</summary>

| Component | Technology | Purpose |
|---|---|---|
| Database | SQLite (WAL mode) | Single-file, zero-config |
| Full-text | FTS5 trigram + triggers | O(1) BM25, SQL-level sync |
| Vector | ChromaDB + bge-m3 (1024-dim) | Semantic search, local |
| Fusion | Reciprocal Rank Fusion (K=60) | Merge multi-path results |
| Re-ranking | 4-factor (sem .45 + rec .25 + freq .05 + imp .10) | Z-score + sigmoid |
| Governance | Supersession + bi-temporal + conflict | Never delete |
| Concurrency | Hubguard (file lock + atomic write) | Cross-process safe |
| API | FastAPI REST + MCP server | Any language |

</details>

---

## Connect Your Clients

```bash
python -m memtether connect --all
python -m memtether verify
python -m memtether detect
python -m memtether selftest
```

**Supported (23):** Claude Code, Cursor, Windsurf, VS Code, Zed, JetBrains, Cline, Roo Code, Kilo Code, Continue, Cody, Amazon Q, Gemini CLI, Neovim, Claude Desktop, Codex, WorkBuddy (CN + Intl), CodeBuddy, ZCode, DSH, OpenClaw, Agents-Neutral

---

## Memory Operations

```bash
python -m memtether remember "User prefers dark theme" --source claude-code
python -m memtether search "theme preference"
python -m memtether correct <uid> "Updated text"
python -m memtether retire <uid> "No longer relevant"
python -m memtether stats
python -m memtether as-of 2026-09-15 --kind known
python -m memtether rebuild
```

---

## Benchmarks

### LongMemEval (500 questions, full run, 2026-10-02)

| Metric | Score | Notes |
|---|---|---|
| **Strict match (global)** | 62.6% | 209/334 applicable |
| **LLM judge (global)** | 54.6% | 263/482 |
| **Multi-session strict** | 48.8% | Above industry avg 27.9% |
| **Multi-session LLM judge** | 60.0% | |
| **E-Hybrid** | 73.3% | 11/15 (small sample) |

<details>
<summary>Per-type breakdown</summary>

| Type | n | Strict | LLM Judge |
|---|---|---|---|
| knowledge-update | 78 | 76.6% | 64.4% |
| multi-session | 133 | 48.8% | 58.4% |
| single-session-assistant | 56 | 40.4% | 42.9% |
| single-session-preference | 30 | N/A | 33.3% |
| single-session-user | 70 | 86.4% | 80.0% |
| temporal-reasoning | 133 | 60.7% | 41.4% |

</details>

> Results use our own harness. Not directly comparable with mem0 reported numbers.

### Test suite

| Test | Result |
|---|---|
| hard_bench | 62/62 |
| asset_bench | 23/23 |
| pytest | 31/31 |
| e2e_verify | 13/13 |
| refuse_bench | 26/26 |
| concurrent_stress | 4/4 PASS |

---

## Known Limitations

1. **`tether_connect detect`** may falsely report "not connected" for same-source-different-path configs. Use `verify` for accurate results.
2. **DSH `cordis.patch.yml`** deep customizations cannot be safely rewritten. Use `plan` to preview.
3. **`memory_hub` (production) and `memtether` (open source) are two copies**. Changes need directional sync.
4. **Semantic search requires optional deps** (chromadb, onnxruntime). Without them, degrades to keyword search.
5. **Windows-first.** macOS/Linux should work but not fully tested.

---

## Relationship to Other Projects

- **mem0**: managed memory with cloud API. MemTether is for people who want everything local.
- **cognee**: knowledge graph + pipeline. MemTether is lightweight operational memory (SQLite, no Neo4j).
- **letta (MemGPT)**: agent framework. MemTether works with existing agents you already use.
- **engram**: Go + SQLite + FTS5 + MCP. MemTether adds bi-temporal, source attribution, Q-Value, 23 adapters.

You can use MemTether **alongside** any of these.

---

## License

Apache 2.0 - see [LICENSE](LICENSE)

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=MemTether/MemTether&type=Date)](https://star-history.com/#MemTether/MemTether&Date)

---

[中文文档](README.zh-CN.md) | [Security](SECURITY.md) | [Changelog](CHANGELOG.md)
