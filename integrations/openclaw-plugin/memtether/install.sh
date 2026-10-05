#!/bin/bash
set -e
if ! command -v memtether &>/dev/null; then
    echo "Install memtether first: pip install memtether"; exit 1
fi
memtether init
echo "Done. Restart your AI client to pick up MCP config."
