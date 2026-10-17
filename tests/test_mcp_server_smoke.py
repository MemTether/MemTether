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

    def test_governance_tools_registered(self, server):
        """P0-1: correct/retire/pin/as_of/timeline must be exposed via MCP."""
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/list", {}, id=2)
        names = {t["name"] for t in r["result"]["tools"]}
        for expected in ("correct_memory", "retire_memory", "pin_memory",
                         "as_of_query", "timeline"):
            assert expected in names, names

    def test_correct_memory_roundtrip(self, server):
        """correct_memory supersedes old uid and activates a new one."""
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "correct-probe original version one",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        uid = json.loads(r["result"]["content"][0]["text"])["uid"]
        r2 = server.request("tools/call", {
            "name": "correct_memory",
            "arguments": {"old_uid": uid,
                           "new_content": "correct-probe fixed version two",
                           "reason": "smoke test", "source": "mcp_smoke"}}, id=4)
        assert "error" not in r2, r2
        payload = json.loads(r2["result"]["content"][0]["text"])
        assert payload["ok"] is True, payload
        assert payload["op"] == "supersede", payload
        assert payload["new_uid"] != uid

    def test_retire_memory(self, server):
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "retire-probe to be retired",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        uid = json.loads(r["result"]["content"][0]["text"])["uid"]
        r2 = server.request("tools/call", {
            "name": "retire_memory",
            "arguments": {"uid": uid, "reason": "smoke test",
                           "source": "mcp_smoke"}}, id=4)
        assert "error" not in r2, r2
        payload = json.loads(r2["result"]["content"][0]["text"])
        assert payload["ok"] is True, payload
        assert payload["op"] == "retired", payload

    def test_pin_list_and_toggle(self, server):
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "pin-probe always keep me",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        uid = json.loads(r["result"]["content"][0]["text"])["uid"]
        r2 = server.request("tools/call", {
            "name": "pin_memory", "arguments": {"uid": uid}}, id=4)
        assert "error" not in r2, r2
        assert json.loads(r2["result"]["content"][0]["text"])["pinned"] is True
        # list-only mode
        r3 = server.request("tools/call", {
            "name": "pin_memory", "arguments": {}}, id=5)
        assert "error" not in r3, r3
        listing = json.loads(r3["result"]["content"][0]["text"])
        assert listing["pinned_count"] >= 1, listing
        # unpin
        r4 = server.request("tools/call", {
            "name": "pin_memory", "arguments": {"uid": uid, "off": True}}, id=6)
        assert json.loads(r4["result"]["content"][0]["text"])["pinned"] is False

    def test_timeline_chain(self, server):
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        r = server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "timeline-probe v1",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        uid1 = json.loads(r["result"]["content"][0]["text"])["uid"]
        r2 = server.request("tools/call", {
            "name": "correct_memory",
            "arguments": {"old_uid": uid1, "new_content": "timeline-probe v2",
                           "reason": "chain", "source": "mcp_smoke"}}, id=4)
        uid2 = json.loads(r2["result"]["content"][0]["text"])["new_uid"]
        r3 = server.request("tools/call", {
            "name": "timeline", "arguments": {"uid": uid1}}, id=5)
        assert "error" not in r3, r3
        payload = json.loads(r3["result"]["content"][0]["text"])
        assert payload["chain_length"] >= 2, payload
        assert payload["chain"][0]["uid"] == uid1
        assert payload["chain"][-1]["uid"] == uid2

    def test_as_of_known_axis(self, server):
        server.request("initialize", {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}, id=1)
        server.request("tools/call", {
            "name": "add_memories",
            "arguments": {"content": "asof-probe recorded now",
                           "source": "mcp_smoke", "type": "fact"}}, id=3)
        r = server.request("tools/call", {
            "name": "as_of_query",
            "arguments": {"kind": "known", "limit": 20}}, id=4)
        assert "error" not in r, r
        payload = json.loads(r["result"]["content"][0]["text"])
        assert payload["count"] >= 1, payload
        assert any("asof-probe" in row["content"] for row in payload["rows"]), payload

    def test_server_stays_alive_after_invalid_method(self, server):
        """Robustness: an unknown method must not kill the server."""
        r1 = server.request("bogus/method", {}, id=9)
        assert "error" in r1, "unknown method should return JSON-RPC error"
        # server must still answer a valid request afterwards
        r2 = server.request("tools/list", {}, id=10)
        assert "result" in r2
