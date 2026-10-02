"""How to connect to MemTether as an MCP server.

MemTether includes an MCP server (mcp_server.py) that exposes
search_memory, add_memories, and list_memories tools.

To connect from Claude Desktop or Cursor, add to your MCP config:
{
  "mcpServers": {
    "memtether": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {"MEM_DB": "~/.memtether/memory.db"}
    }
  }
}

Or use tether_connect to auto-detect and configure all clients:
  memtether-connect --all
"""

# The MCP server exposes these tools:
TOOLS = {
    "search_memory": {
        "description": "Search the shared memory hub",
        "params": {"query": "str", "limit": "int (default 10)"},
        "returns": "list of matching memories with scores and source attribution"
    },
    "add_memories": {
        "description": "Write new memories to the hub",
        "params": {"texts": "list of str", "source": "str (client name)"},
        "returns": "confirmation with UIDs"
    },
    "list_memories": {
        "description": "List recent memories",
        "params": {"limit": "int (default 20)"},
        "returns": "list of recent memories"
    }
}

if __name__ == "__main__":
    print("MemTether MCP Server tools:")
    for name, info in TOOLS.items():
        print(f"  {name}: {info['description']}")
    print("\nTo connect, run: memtether-connect --all")
    print("Or manually add to your MCP config (see docstring above)")
