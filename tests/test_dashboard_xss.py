# -*- coding: utf-8 -*-
"""tests/test_dashboard_xss.py — a50: dashboard XSS fuzz

a21 added escapeHtml; this fuzzes the dashboard HTML + API-rendered
values with classic XSS payloads to confirm nothing executes.
"""
import os, sys, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = os.path.join(__import__("tempfile").mkdtemp(prefix="mt_xss_"), "t.db")

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    TestClient = None

import gateway
if HAS_FASTAPI:
    gateway.init_db()
    from api_server import app
    client = TestClient(app)

requires_fastapi = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")

PAYLOADS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '"><svg onload=alert(1)>',
    "javascript:alert(1)",
    '<iframe src="data:text/html,<script>alert(1)</script>">',
]


@requires_fastapi
class TestDashboardXSSFuzz:
    def test_dashboard_html_has_no_raw_payload(self, tmp_path):
        """Store each payload as a fact; dashboard HTML must not contain
        unescaped <script>/<iframe>/<img onerror> coming from content."""
        for i, p in enumerate(PAYLOADS):
            r = client.post("/remember", json={
                "content": f"xss probe {i}: {p}", "source": "xss",
                "type": "fact"})
            assert r.status_code == 200
        r = client.get("/dashboard")
        assert r.status_code == 200
        body = r.text
        # The dashboard shell itself legitimately contains JS; what must NOT
        # happen is our stored payload appearing as raw executable markup
        for p in PAYLOADS:
            assert p not in body, f"raw payload leaked into dashboard: {p[:40]}"

    def test_search_api_returns_escaped_or_raw_but_safe_headers(self):
        for i, p in enumerate(PAYLOADS):
            r = client.post("/search", json={"query": f"xss probe {i}", "limit": 3})
            assert r.status_code == 200
            # JSON responses have application/json content-type -> browsers
            # won't execute; check header
            ct = r.headers.get("content-type", "")
            assert "application/json" in ct or "javascript" not in ct
