# MemTether Memory — Claude Code Plugin

Cross-client AI memory hub via file-level pointers. This plugin connects Claude Code to MemTether.

## Install

### macOS / Linux
```bash
bash install.sh
```

### Windows (PowerShell)
```powershell
.\install.ps1
```

## What it does

1. Runs `memtether init` (creates DB + detects all AI clients)
2. Writes MemTether MCP server config to `~/.claude.json`
3. Provides `SKILL.md` teaching Claude Code when to use memory tools

## MCP Tools

After install + restart Claude Code, you get:
- `search_memory(query, limit)` — search shared memory
- `add_memories(content, source, type)` — write memory
- `list_memories(limit)` — list recent

## Manual install

If the installer doesn't work for your setup, add to `~/.claude.json`:
```json
{
  "mcpServers": {
    "memtether": {
      "command": "memtether",
      "args": ["dashboard"],
      "transport": "stdio"
    }
  }
}
```
