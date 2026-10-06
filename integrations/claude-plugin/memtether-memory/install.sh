#!/bin/bash
# MemTether Claude Code integration installer
set -e

echo "MemTether Claude Code plugin installer"
echo "========================================"

# Check memtether is installed
if ! command -v memtether &>/dev/null; then
    echo "Error: memtether not found. Install first:"
    echo "  pip install memtether"
    exit 1
fi

# Run init if not done
echo "Running memtether init..."
memtether init

# Write MCP config to Claude Code settings
CLAUDE_JSON="$HOME/.claude.json"
if [ -f "$CLAUDE_JSON" ]; then
    echo "Found existing $CLAUDE_JSON"
    # Use python to merge MCP config
    python3 -c "
import json, os
p = os.path.expanduser('~/.claude.json')
with open(p) as f: d = json.load(f)
if 'mcpServers' not in d: d['mcpServers'] = {}
d['mcpServers']['memtether'] = {
    'command': 'python3',
    'args': ['-m', 'mcp_server'],
    'transport': 'stdio'
}
with open(p, 'w') as f: json.dump(d, f, indent=2)
print('MCP config written to', p)
"
else
    echo '{"mcpServers":{"memtether":{"command":"python3","args":["-m","mcp_server"],"transport":"stdio"}}}' > "$CLAUDE_JSON"
    echo "Created $CLAUDE_JSON with MemTether MCP config"
fi

echo ""
echo "Done! Claude Code will now have memtether MCP tools available."
echo "Next: restart Claude Code and try: search_memory 'test'"
