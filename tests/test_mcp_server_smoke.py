# -*- coding: utf-8 -*-
"""tests/test_mcp_server_smoke.py — a47: MCP server stdio smoke test

Spawns `python -m mcp_server` as a subprocess, speaks hand-rolled
JSON-RPC 2.0 over stdio (initialize -> tools/list -> search_memory),
and asserts protocol-level correctness. Closes the biggest
"new-user first hour" gap: mcp_server.py previously had ZERO tests.
"""
import json, os, subprocess, sys, tempfile, pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


class MCPServer:
    """Spawn mcp_server as a subprocess and speak JSON-RPC over stdio."""

    def __init__(self, db_path):
        env = dict(os.environ)
        env["MEM_DB"] = db_path
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [PY, "-X", "utf8", "-m", "mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=REPO, env=env)

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout=30):
        # stdio servers reply line-delimited JSON
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.poll() is not None else "(still running)"
            raise AssertionError(f"no response from mcp_server; stderr={err[:500]}")
        return json.loads(line)

    def request(self, method, params=None, id=1):
        req = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            req["params"] = params
        self.send(req)
        return self.recv()

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.wait(timeout=10)


@pytest.fixture()
def server(tmp_path):
    db = str(tmp_path / "mcp_smoke.db")
    os.environ["MEM_DB"] = db
    s = MCPServer(db)
    yield s
    s.close()


class TestMCPServerSmoke:
    def test_initialize_handshake(self, server):
        """MCP initialize must return protocolVersion + serverInfo."""
        r = server.request("initialize", {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        assert "error" not in r, r
        result = r["result"]
        assert "protocolVersion" in result or "serverInfo" in result, result

    def test_tools_list(self, server):
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/list", {}, id=2)
        assert "error" not in r, r
        tools = r["result"]["tools"]
        names = {t["name"] for t in tools}
        # core contract: add / search / list must exist
        assert "add_memories" in names, names
        assert "search_memory" in names, names
        assert "list_memories" in names, names

    def test_add_and_search_roundtrip(self, server, tmp_path):
        """Full round trip: add a memory via MCP, find it via search_memory."""
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        # add
        r = server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "roundtrip probe fact alpha-7741",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        assert "error" not in r, r
        # search
        r2 = server.request("tools/call", {
            "name": "search_memory",
            "arguments": {"query": "roundtrip probe fact alpha-7741", "limit": 3}}, id=4)
        assert "error" not in r2, r2
        text = json.dumps(r2["result"], ensure_ascii=False)
        assert "alpha-7741" in text, text

    def test_server_stays_alive_after_invalid_method(self, server):
        """Robustness: an unknown method must not kill the server."""
        r1 = server.request("bogus/method", {}, id=9)
        assert "error" in r1, "unknown method should return JSON-RPC error"
        # server must still answer a valid request afterwards
        r2 = server.request("tools/list", {}, id=10)
        assert "result" in r2
