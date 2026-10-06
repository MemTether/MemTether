# Adding a New AI Client Adapter

MemTether supports 23 AI clients out of the box. If yours isn't listed, you can add it in ~30 minutes.

## Quick Start

1. **Identify the config format** — most clients use one of three layouts:

| Layout | Root key | Example clients |
|---|---|---|
| Plain JSON | `mcpServers` | Claude Desktop, Cursor, Windsurf |
| JSONC (comments) | `mcpServers` or `servers` | VS Code, Cline, Roo Code |
| TOML | `[mcp_servers.<name>]` | Codex CLI |

2. **Create the adapter** in `clients/standard.py`:

```python
def _my_client_config():
    return os.path.join(HOME, ".my-client", "mcp.json")

MyClientAdapter = _mk(
    "MyClientAdapter", "my-client", "My Client",
    _my_client_config,
    ["mcpServers"],
    markers=[os.path.join(HOME, ".my-client")],  # install detection
)

# Add to REGISTRY
REGISTRY.append(MyClientAdapter())
```

3. **Test it**:
```bash
python tether_connect.py detect     # should find your client
python tether_connect.py apply --client my-client
python tether_connect.py verify --client my-client
```

4. **Add a test** in `tests/test_adapters.py` following the existing pattern.

## Key Principles

- **Read-only first**: `detect` and `plan` never write.
- **Idempotent**: applying twice = no-op the second time.
- **Fail closed**: can't parse? Skip, don't guess.
- **Preserve user config**: JSONC comments, key order, and indentation are preserved.
- **Install markers**: required for clients whose config lives in a shared directory (prevents creating configs for uninstalled software).

## File Layout Reference

See `clients/standard.py` header comment for a table of 15+ clients with their config paths and quirks.
