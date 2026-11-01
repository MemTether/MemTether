# Agent Memory Skill

Persistent, auditable memory for AI agents. Cross-client, local-first, tamper-evident.

## What This Does

Gives your agent a shared memory that survives session restarts, with:
- Hash-chained audit log (every write is cryptographically linked)
- Bi-temporal supersession (corrections never delete, query what was believed at any past time)
- Source attribution (every memory carries the agent's identity)
- Conflict gate (contradictory writes flagged for human review)

## When to Use

- Your agent needs to remember facts across sessions
- Multiple agents need to share a knowledge base
- You need to audit what your agent "learned" and who told it
- You need to detect if someone tampered with the agent's memory

## Quick Start

```bash
# Store a memory
memtether remember "User prefers dark mode" --source my-agent

# Search memories
memtether search "dark mode"

# Correct a memory (never deletes)
memtether correct <uid> "User prefers light mode now" --reason "user changed preference"

# Audit a memory's provenance
memtether court <uid>

# Query what the system believed last week
memtether as_of "2026-10-25" --kind valid
```

## MCP Server Mode

Add to your MCP config:
```json
{ "mcpServers": { "memtether": { "command": "memtether", "args": ["mcp"] } } }
```

## Python API

```python
import memtether
r = memtether.remember("fact", type="fact", source="my-agent")
results = memtether.search("query")
```

## Rules

1. NEVER delete a memory. Use `memtether correct` to supersede.
2. ALWAYS carry a `source` field. Anonymous writes are a governance failure.
3. Check `memtether court <uid>` if a memory seems suspicious.
