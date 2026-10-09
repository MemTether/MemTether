# Examples

Each file is self-contained and can be run directly:

```bash
python examples/basic_usage.py       # remember + search + supersede + retire`npython examples/basic_remember.py    # minimal 10-line quickstart (CN comments)
python examples/multi_client_demo.py # two clients sharing one memory (Claude Code -> Cursor)
python examples/bitemporal_demo.py   # as-of queries
python examples/qvalue_demo.py       # Q-Value ranking demo
python examples/mcp_client.py        # MCP server connection guide (info only)
python examples/rest_api_client.py   # REST API usage guide (info only)
python examples/audit_chain_demo.py  # tamper-evident audit chain demo (core feature)
```

All examples use isolated temp databases and won't touch any real data.
