
# MemTether Claude Code integration installer (Windows)
Write-Host "MemTether Claude Code plugin installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check memtether
if (-not (Get-Command memtether -ErrorAction SilentlyContinue)) {
    Write-Host "Error: memtether not found. Install first:" -ForegroundColor Red
    Write-Host "  pip install memtether"
    exit 1
}

# Run init
Write-Host "Running memtether init..."
memtether init

# Write MCP config to Claude Code
$claudeJson = "$env:USERPROFILE\.claude.json"
if (Test-Path $claudeJson) {
    Write-Host "Found existing $claudeJson"
    python -c "
import json, os
p = os.path.expanduser('~/.claude.json')
with open(p) as f: d = json.load(f)
if 'mcpServers' not in d: d['mcpServers'] = {}
d['mcpServers']['memtether'] = {
    'command': 'python',
    'args': ['-m', 'mcp_server'],
    'transport': 'stdio'
}
with open(p, 'w') as f: json.dump(d, f, indent=2)
print('MCP config written to', p)
"
} else {
    '{"mcpServers":{"memtether":{"command":"python","args":["-m","mcp_server"],"transport":"stdio"}}}' | Out-File -Encoding utf8 $claudeJson
    Write-Host "Created $claudeJson with MemTether MCP config"
}

Write-Host ""
Write-Host "Done! Claude Code will now have memtether MCP tools available." -ForegroundColor Green
Write-Host "Next: restart Claude Code and try: search_memory 'test'"
