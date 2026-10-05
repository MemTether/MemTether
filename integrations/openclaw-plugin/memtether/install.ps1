# MemTether installer for openclaw-plugin
if (-not (Get-Command memtether -ErrorAction SilentlyContinue)) {
    Write-Host "Install memtether first: pip install memtether"; exit 1
}
memtether init
Write-Host "Done. Restart your AI client to pick up MCP config." -ForegroundColor Green
