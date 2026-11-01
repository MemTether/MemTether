# MemTether — Project Invariants

## NEVER DO
1. Never delete a memory record. Use supersession instead.
2. Never write a memory without a source field. Every write must carry `source=<agent_name>`.
3. Never bypass the hash chain. Every audit entry must chain to the previous one.
4. Never auto-resolve a conflict. Contradictory writes must go to human adjudication.
5. Never expose private memories across tenant boundaries.

## ALWAYS DO
1. Use `memtether remember --source <agent_name>` to write memories.
2. Use `memtether correct <uid> --reason <why>` to correct memories (never DELETE + INSERT).
3. Use `memtether as_of <timestamp>` to query historical state.
4. Use `memtether court <uid>` to audit a memory's provenance chain.
5. Use `memtether search --limit N` to recall relevant memories.

## ARCHITECTURE
- Storage: SQLite (single file, local, no cloud)
- Audit: SHA-256 hash chain (each entry links to the previous)
- Corrections: Bi-temporal supersession (valid_from/valid_to + recorded_at/invalidated_at)
- Conflict: Contradictory writes flagged, human resolves via `memtether conflicts`
- Ranking: Q-value (usage-based, not recency-based)

## KEY FILES
- `gateway.py` — Memory API (remember, search, correct, retire, stats)
- `memsearch.py` — Search engine (hybrid: vector + keyword + literal)
- `memtether.py` — CLI entry point + public API
- `memory_court.py` — Audit chain verification + case file export
- `mcp_server.py` — MCP server (10 tools)
