# MemTether Memory

Cross-client AI memory hub. Claude Code can use MemTether MCP tools to remember and recall across sessions.

## Slash commands

- /mem-remember <fact> - save a memory
- /mem-search <query> - search shared memory
- /mem-stats - recent memories summary

## Available MCP tools

When the MemTether MCP server is connected (via install.sh or install.ps1), you have access to:

- `search_memory(query, limit)` - Search shared memory for relevant facts
- `add_memories(content, source, type)` - Write a memory (use source="claude-code")
- `list_memories(limit)` - List recent memories

## When to use

- User says "remember this" or "save this" -> add_memories
- User asks about past decisions or previous conversations -> search_memory first
- Starting a new session on a shared project -> search_memory for project context

## When NOT to use

- Sensitive credentials (API keys, passwords) -> never store these
- Session-specific temporary context -> the conversation context is enough
- Information the user hasn't explicitly asked to remember

## Source attribution

Always use `source: "claude-code"` so other clients know who wrote the memory.
