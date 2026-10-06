<div align="center">

<img src="assets/demo.gif" width="100%" alt="MemTether — cross-client AI memory in action"/>

# MemTether

**Your AI agents forget everything. MemTether gives them one shared brain.**

One physical SQLite database, shared by every AI client via file-level pointers.
No cloud. No API fees. No sync daemon. Greppable, git-able, yours.

[![PyPI](https://img.shields.io/pypi/v/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

mcp-name: io.github.lanbass869-cell/memtether
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.lanbass869--cell%2Fmemtether-blue)](https://registry.modelcontextprotocol.io)

[**Try in browser**](https://huggingface.co/spaces/lanbass/memtether-demo) · [Install](#quick-start) · [Docs](https://memtether.github.io/MemTether/site/en/) · [Reproduce](https://memtether.github.io/MemTether/site/en/reproduce.html) · [中文文档](README.zh-CN.md)

</div>

---

## Documentation

- [Reproduce MemTether](docs/site/en/reproduce.html) - Every claim has a command
- [Founding 10 Challenge](docs/site/en/challenge.html) - 10 spots, 14 days, honest outcomes
- [Live site](https://lanbass869-cell.github.io/MemTether/en/) (when GitHub Pages is enabled)

## Quick Start

```bash
pip install memtether
memtether init    # creates DB + connects all detected AI clients
```

That's it. MemTether detects Claude Code, Cursor, Windsurf, Codex, Gemini CLI and 18 more clients on your machine, writes MCP config, and verifies.

<details>
<summary>More install options</summary>

```bash
# One client at a time
memtether setup claude-code
memtether setup cursor
memtether setup gemini-cli    # run `memtether setup list` for all 23

# With semantic search (local embedding, no cloud)
pip install "memtether[vector]"

# With REST API server + web dashboard
pip install "memtether[server]"
memtether dashboard

# Docker
docker-compose up
```

</details>

---

## Why MemTether

### The one thing no other memory system does: corrections don't delete

```bash
# Monday: Claude Code writes
$ memtether remember "Deploy to port 8080" --source claude-code

# Wednesday: you correct it via Cursor
$ memtether correct fact-xxx "Deploy to port 9090" --reason "port changed"

# Friday: another agent searches
$ memtether search "deploy port" --list
  1. [fact] Deploy to port 9090 (active)          ← current truth
     Old "port 8080" is superseded, not deleted.
     Full chain: memtether timeline fact-xxx
```

Every memory has `valid_from` / `valid_to` (business time) + `recorded_at` / `invalidated_at` (system time). You can query "what did the system know on Tuesday?"

### How it's different

| | MemTether | engram | mem0 | agent-memory |
|---|---|---|---|---|
| Storage | SQLite via file pointers | SQLite (Go binary) | Cloud API | Markdown files |
| Supersession | ✅ bi-temporal, never delete | ❌ | ⚠️ partial | ✅ |
| Source attribution | ✅ every fact tagged | ❌ | ❌ | ✅ |
| Q-Value feedback | ✅ used memories rank higher | ❌ | ❌ | ❌ |
| Poisoning defense | ✅ OWASP LLM01/02/06 | ❌ | ⚠️ faithfulness check | ❌ |
| Cross-client | ✅ file-level pointers (23 adapters) | ✅ MCP | ✅ API | ✅ CLI |
| Cloud required | ❌ | optional | ✅ | ❌ |

<details>
<summary>Feature comparison vs cognee / zep / letta</summary>

| Feature | MemTether | cognee | zep | letta |
|---|---|---|---|---|
| Local-first | ✅ | ❌ | ❌ | ⚠️ self-hosted |
| Zero config | ✅ | ❌ (Neo4j) | ❌ (Docker) | ⚠️ |
| Single file DB | ✅ | ❌ | ❌ | ❌ |
| REST API | ✅ (12 endpoints) | ✅ | ✅ | ✅ |
| MCP server | ✅ | ❌ | ✅ | ✅ |
| Web dashboard | ✅ | ❌ | ✅ | ✅ |
| Supersession chain | ✅ | ❌ | ❌ | ⚠️ |
| Bi-temporal | ✅ | ❌ | ❌ | ❌ |
| Source attribution | ✅ | ❌ | ⚠️ | ❌ |
| Q-Value ranking | ✅ | ❌ | ❌ | ❌ |
| Poisoning defense | ✅ | ❌ | ❌ | ❌ |
| Export as markdown | ✅ | ❌ | ❌ | ❌ |
| 23 client adapters | ✅ | ❌ | ❌ | ❌ |

</details>

---

## Architecture

```
Claude Code ────┐                  ┌──── Cursor
                │   symlink /      │
Codex ──────────┤   junction       ├──── Windsurf
                │                  │
Gemini CLI ─────┤                  ├─── WorkBuddy
                └───────┬──────────┘
                        │
                        ▼
              ┌──────────────────┐
              │   memory.db      │  ← One physical SQLite
              │   (WAL mode)     │
              ├──────────────────┤
              │ FTS5 trigram     │  ← BM25 full-text
              │ ChromaDB + bge-m3│  ← Semantic (optional)
              │ RRF fusion       │  ← Multi-path merge
              │ 4-factor rerank  │  ← Z-score + sigmoid
              │ Supersession     │  ← Never delete
              │ Bi-temporal      │  ← T-axis + T'-axis
              │ Q-Value          │  ← Usage-based ranking
              │ Hubguard         │  ← Concurrent write lock
              │ Poisoning guard  │  ← OWASP LLM defense
              └──────────────────┘
```

<details>
<summary>Technical details</summary>

| Component | Technology | Purpose |
|---|---|---|
| Database | SQLite (WAL mode) | Single-file, zero-config, cross-platform |
| Full-text | FTS5 trigram + triggers | O(1) BM25 search, SQL-level sync |
| Vector | ChromaDB + bge-m3 (1024-dim) | Semantic search, local embedding |
| Fusion | Reciprocal Rank Fusion (K=60) | Merge multi-path results |
| Re-ranking | 4-factor (sem .45 + rec .25 + freq .05 + imp .10) | Z-score + sigmoid |
| Governance | Supersession + bi-temporal + conflict detection | Never delete |
| Concurrency | Hubguard (file lock + atomic write) | Cross-process safe |
| Security | memtether_guard (OWASP LLM01/02/06) | Poisoning defense |
| API | FastAPI REST (12 endpoints) + MCP server (4 tools) | Any language |
| Packaging | PyPI + Docker + 23 client adapters | pip install memtether |

</details>

---

## Connect Your Clients

```bash
# All detected clients (recommended)
memtether init

# One at a time
memtether setup claude-code
memtether setup cursor
memtether setup windsurf
memtether setup codex
```

**Supported (23):** Claude Code, Claude Desktop, Cursor, Windsurf, VS Code, Zed, JetBrains, Cline, Roo Code, Kilo Code, Continue, Cody, Amazon Q, Gemini CLI, Neovim, Codex CLI, WorkBuddy (CN + Intl), CodeBuddy, ZCode, DSH, OpenClaw, Agents-Neutral

---

## Memory Operations

```bash
memtether remember "User prefers dark theme" --source claude-code
memtether search "theme" --list        # L0: one-line + uid
memtether search "theme"               # Full content
memtether correct <uid> "Updated"      # Supersede, never delete
memtether timeline <uid>               # Audit chain
memtether stats
memtether export-md --out ./backup     # Export as markdown
memtether dashboard                    # Web UI
```

---

## Benchmarks

<details>
<summary>LongMemEval (500 questions, full run, 2026-10-02)</summary>

| Metric | Score | Notes |
|---|---|---|
| **Strict match (global)** | 62.6% | 209/334 applicable |
| **LLM judge (global)** | 55.7% | 264/474 |
| **Multi-session strict** | 48.8% | 39/80 |
| **Multi-session LLM judge** | 60.0% | 72/120 |
| **EAF** | 75.7% | 253/334 — multi-session 32.5%→60.0% |

| Type | n | Strict | LLM Judge |
|---|---|---|---|
| knowledge-update | 78 | 76.6% | 62.5% |
| multi-session | 133 | 48.8% | 60.0% |
| single-session-assistant | 56 | 40.4% | 41.1% |
| single-session-preference | 30 | N/A | 43.3% |
| single-session-user | 70 | 86.4% | 85.7% |
| temporal-reasoning | 133 | 60.7% | 40.5% |

> Results use our own harness. Not directly comparable with mem0's reported numbers.

</details>

<details>
<summary>Test suite</summary>

| Test | Result |
|---|---|
| pytest | 30/30 (CI runs full tests/ directory) |
| hard_bench | 62/62 |
| e2e_verify | 13/13 |
| refuse_bench | 26/26 (wordform gate; 09-25 calibration; known blind spot: same-form-different-attribute) |
| check_packaging | ✅ |

</details>

---

## Security

- **Poisoning defense**: `memtether_guard` detects prompt injection, code execution, sensitive info (OWASP LLM01/02/06), 12 rules
- **Input validation**: content ≤10K, type whitelist, source format, confidence range, scope whitelist
- **Scope isolation**: `shared` / `private` / `restricted`
- **Concurrency**: Hubguard file lock + atomic write (WAL mode)
- **SQL**: All parameterized. No f-string SQL

---

## Known Limitations

1. `tether_connect detect` may falsely report "not connected" for same-source-different-path configs
2. DSH deep customizations (`cordis.patch.yml`) cannot be safely rewritten — use `plan` to preview
3. Semantic search requires optional deps (`chromadb`, `onnxruntime`) — without them, degrades to keyword search
4. Windows-first. macOS/Linux should work but not fully tested
5. Exchange adapters for mem0/zep are experimental — they require the corresponding library installed

---

## REST API

```bash
memtether dashboard   # starts server + opens browser
# or: uvicorn api_server:app --port 8820
```

12 endpoints: `/health` `/remember` `/search` `/stats` `/correct` `/retire` `/list` `/timeline/{uid}` `/absorb` `/qvalue` `/dashboard`

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE)

---

<div align="center">
<img src="https://api.star-history.com/svg?repos=MemTether/MemTether&type=Date" width="500" alt="Star History"/>
</div>
