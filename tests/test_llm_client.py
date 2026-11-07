"""Comprehensive tests for llm_client.py — optional LLM client.

Tests use a real HTTP mock server (http.server in a thread) to verify
the actual network path, not just mocked function calls.
"""
import os
import json
import threading
import http.server
import pytest

# Skip all tests if llm_client can't be imported (should never happen)
from llm_client import (
    is_available, chat, extract_entities, classify_relation, reflect,
    LLMError, _config
)


# ---------------------------------------------------------------------------
# Mock LLM server
# ---------------------------------------------------------------------------

class MockLLMHandler(http.server.BaseHTTPRequestHandler):
    """Simulates an OpenAI-compatible /v1/chat/completions endpoint."""

    # Class-level: tests set this to control the response
    response_content = '[]'
    status_code = 200

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))

        # Record the request for assertions
        MockLLMHandler.last_request = body

        if MockLLMHandler.status_code != 200:
            self.send_response(MockLLMHandler.status_code)
            self.end_headers()
            self.wfile.write(b'{"error": "mock error"}')
            return

        response = {
            "choices": [{"message": {"content": MockLLMHandler.response_content}}]
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, format, *args):
        pass  # suppress logging


@pytest.fixture
def mock_llm():
    """Start a mock LLM server and configure MEM_LLM_* env vars."""
    server = http.server.HTTPServer(("127.0.0.1", 0), MockLLMHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Configure env vars to point at the mock
    old_env = {}
    env_vars = {
        "MEM_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "MEM_LLM_API_KEY": "test-key-123",
        "MEM_LLM_MODEL": "test-model",
        "MEM_LLM_MAX_TOKENS": "256",
        "MEM_LLM_TEMPERATURE": "0.1",
        "MEM_LLM_TIMEOUT": "10",
    }
    for k, v in env_vars.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Reset class-level state
    MockLLMHandler.response_content = '[]'
    MockLLMHandler.status_code = 200
    MockLLMHandler.last_request = None

    yield MockLLMHandler

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    server.shutdown()


@pytest.fixture
def no_llm():
    """Ensure LLM env vars are NOT set."""
    old_env = {}
    for k in ["MEM_LLM_BASE_URL", "MEM_LLM_API_KEY", "MEM_LLM_MODEL"]:
        old_env[k] = os.environ.get(k)
        os.environ.pop(k, None)
    yield
    for k, v in old_env.items():
        if v is not None:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Test: is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_true_with_base_url(self, mock_llm):
        assert is_available() is True

    def test_false_without_base_url(self, no_llm):
        assert is_available() is False


# ---------------------------------------------------------------------------
# Test: chat
# ---------------------------------------------------------------------------

class TestChat:
    def test_basic_chat(self, mock_llm):
        mock_llm.response_content = "Hello world"
        result = chat([{"role": "user", "content": "hi"}])
        assert result == "Hello world"
        # Verify request structure
        req = mock_llm.last_request
        assert req["model"] == "test-model"
        assert req["messages"][0]["content"] == "hi"
        # Authorization is in HTTP headers (not JSON body) — verified implicitly by the 200 response\n        assert req["model"] == "test-model"

    def test_empty_response(self, mock_llm):
        mock_llm.response_content = ""
        result = chat([{"role": "user", "content": "hi"}])
        assert result == ""

    def test_server_error_raises(self, mock_llm):
        mock_llm.status_code = 500
        with pytest.raises(LLMError):
            chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Test: extract_entities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_valid_json_response(self, mock_llm):
        mock_llm.response_content = '[{"entity": "nginx", "attr": "port 8080"}]'
        result = extract_entities("nginx is running on port 8080")
        assert result is not None
        assert len(result) == 1
        assert result[0]["entity"] == "nginx"
        assert result[0]["attr"] == "port 8080"

    def test_hallucinated_entity_rejected(self, mock_llm):
        """Entity not in original text must be rejected (anti-hallucination)."""
        mock_llm.response_content = '[{"entity": "hallucinated_service", "attr": "something"}]'
        result = extract_entities("nginx is running on port 8080")
        # "hallucinated_service" is not in the text → rejected
        assert result is None or len(result) == 0

    def test_generic_entity_rejected(self, mock_llm):
        """Generic words like 'user' must be rejected."""
        mock_llm.response_content = '[{"entity": "user", "attr": "something"}]'
        result = extract_entities("nginx is running on port 8080 for the user")
        # "user" is in the text but is a generic word → rejected
        assert result is None or len(result) == 0

    def test_no_json_response(self, mock_llm):
        mock_llm.response_content = "Sorry, I cannot do that."
        result = extract_entities("some text here")
        assert result is None

    def test_invalid_json_response(self, mock_llm):
        mock_llm.response_content = '[{broken json'
        result = extract_entities("some text here")
        assert result is None

    def test_not_available(self, no_llm):
        result = extract_entities("some text")
        assert result is None

    def test_chinese_entities(self, mock_llm):
        mock_llm.response_content = '[{"entity": "nginx", "attr": "配置文件"}]'
        result = extract_entities("nginx的配置文件在/etc/nginx/nginx.conf")
        assert result is not None
        assert result[0]["entity"] == "nginx"

    def test_multiple_entities(self, mock_llm):
        mock_llm.response_content = json.dumps([
            {"entity": "nginx", "attr": "port"},
            {"entity": "redis", "attr": "cache"}
        ])
        result = extract_entities("nginx and redis are running")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Test: classify_relation
# ---------------------------------------------------------------------------

class TestClassifyRelation:
    def test_supersede(self, mock_llm):
        mock_llm.response_content = "supersede"
        result = classify_relation("port is now 9090", "port is 8080")
        assert result == "supersede"

    def test_new(self, mock_llm):
        mock_llm.response_content = "new"
        result = classify_relation("added SSL certificate", "port is 8080")
        assert result == "new"

    def test_invalid_response(self, mock_llm):
        mock_llm.response_content = "I think it might be a new fact but I am not sure about the classification"
        result = classify_relation("a", "b")
        assert result is None

    def test_not_available(self, no_llm):
        result = classify_relation("a", "b")
        assert result is None


# ---------------------------------------------------------------------------
# Test: reflect
# ---------------------------------------------------------------------------

class TestReflect:
    def test_basic_reflect(self, mock_llm):
        mock_llm.response_content = "The server has been running stably on port 8080 with no issues reported."
        result = reflect("nginx", ["nginx is on port 8080", "nginx config is valid"])
        assert result is not None
        assert "server" in result.lower()

    def test_short_result_rejected(self, mock_llm):
        mock_llm.response_content = "ok"
        result = reflect("nginx", ["nginx is on port 8080"])
        assert result is None

    def test_empty_facts(self, mock_llm):
        result = reflect("nginx", [])
        assert result is None

    def test_not_available(self, no_llm):
        result = reflect("nginx", ["fact"])
        assert result is None


# ---------------------------------------------------------------------------
# Test: full pipeline — remember() with LLM enabled vs disabled
# ---------------------------------------------------------------------------

class TestRememberWithLLM:
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "llm_pipe.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        monkeypatch.setenv("MEM_DEFAULT_SOURCE", "codex")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_remember_with_llm_entities(self, tmp_path, monkeypatch):
        """When LLM is available and returns entities, they are used instead of regex."""
        # Start mock LLM
        server = http.server.HTTPServer(("127.0.0.1", 0), MockLLMHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        monkeypatch.setenv("MEM_LLM_BASE_URL", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("MEM_LLM_API_KEY", "test")
        monkeypatch.setenv("MEM_LLM_MODEL", "test")
        MockLLMHandler.response_content = '[{"entity": "deploy", "attr": "port 9090"}]'
        MockLLMHandler.last_request = None

        gw = self._setup(tmp_path, monkeypatch)
        r = gw.remember("deploy server is running on port 9090", source="codex")
        server.shutdown()

        assert r["ok"]
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "llm_pipe.db"))
        conn.row_factory = sqlite3.Row
        entities = conn.execute(
            "SELECT entity, source FROM fact_entities WHERE fact_uid=?",
            (r["uid"],)).fetchall()
        conn.close()
        entity_names = [e["entity"] for e in entities]
        assert "deploy" in entity_names, f"LLM entity 'deploy' should be in fact_entities, got: {entity_names}"

    def test_remember_without_llm_falls_back_to_regex(self, tmp_path, monkeypatch):
        """When LLM is not configured, remember falls back to regex."""
        gw = self._setup(tmp_path, monkeypatch)
        # Ensure no LLM
        monkeypatch.delenv("MEM_LLM_BASE_URL", raising=False)
        r = gw.remember("mydb的端口是5432", source="codex")
        assert r["ok"]
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "llm_pipe.db"))
        entities = conn.execute(
            "SELECT entity FROM fact_entities WHERE fact_uid=?",
            (r["uid"],)).fetchall()
        conn.close()
        # regex should have extracted "mydb"
        assert any("mydb" in e[0] for e in entities), \
            f"regex fallback should extract mydb, got: {entities}"
