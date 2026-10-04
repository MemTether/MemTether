<div align="center">

<img src="assets/demo-usage.svg" width="100%" alt="MemTether Usage Demo — Two AI clients sharing memory"/>

# MemTether

**Your AI agents' memory is a file, not a pipeline.**

One physical SQLite database, shared by every AI client on your machine via file-level pointers.
No cloud. No API fees. No sync. Your memory stays greppable, git-able, and yours.

[![PyPI](https://img.shields.io/pypi/v/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.lanbass869--cell%2Fmemtether-blue)](https://registry.modelcontextprotocol.io)

**[Try it in your browser →](https://huggingface.co/spaces/lanbass/memtether-demo)** · [pip install](#quick-start) · [中文文档](README.zh-CN.md)

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
memtether dashboard
```

This starts the API server and opens the web dashboard in your browser.


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

> Memory should be a data format, not a multi-stage pipeline. MemTether treats your agents’ shared memory as a plain SQLite file — `ls` it, `grep` it, `git` it, back it up. The symlink is the deploy step.

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
| **LLM judge (global)** | 55.7% | 264/474 |
| **Multi-session strict** | 48.8% | 39/80 — above industry avg 27.9% |
| **Multi-session LLM judge** | 60.0% | 72/120 |
| **EAF (Evidence Assembly)** | 75.7% | 253/334 strict — multi-session 32.5%→60.0% |

<details>
<summary>Per-type breakdown</summary>

| Type | n | Strict | LLM Judge |
|---|---|---|---|
| knowledge-update | 78 | 76.6% | 62.5% |
| multi-session | 133 | 48.8% | 60.0% |
| single-session-assistant | 56 | 40.4% | 41.1% |
| single-session-preference | 30 | N/A | 43.3% |
| single-session-user | 70 | 86.4% | 85.7% |
| temporal-reasoning | 133 | 60.7% | 40.5% |

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
3. **MemTether shares one physical DB via file-level pointers.** On Windows this uses NTFS junctions; on macOS/Linux, symlinks. Both are native OS features — no sync daemon needed.
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


## REST API

MemTether includes a built-in REST API server (FastAPI) for non-CLI access:

```bash
# Start the API server
memtether dashboard --port 8820
```

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/remember` | POST | Store a memory |
| `/search` | POST | Search memories |
| `/stats` | GET | Memory statistics |
| `/correct` | POST | Correct/supersede a memory |
| `/retire` | POST | Retire a memory |
| `/list` | GET | List recent memories |
| `/qvalue` | POST | Update Q-Value score |
| `/dashboard` | GET | Web dashboard |

A web dashboard is available at `http://localhost:8820/dashboard` when the API server is running.

---

## License

Apache 2.0 - see [LICENSE](LICENSE)

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=MemTether/MemTether&type=Date)](https://star-history.com/#MemTether/MemTether&Date)

---

[中文文档](README.zh-CN.md) | [Security](SECURITY.md) | [Changelog](CHANGELOG.md)
