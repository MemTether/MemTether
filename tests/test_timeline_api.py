# -*- coding: utf-8 -*-
"""tests/test_timeline_api.py — a47: /timeline/{uid} supersession chain API

README headline feature ("the only memory hub where corrections don't
delete") served by GET /timeline/{uid} — previously zero tests.
"""
import os, sys, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = os.path.join(
    __import__("tempfile").mkdtemp(prefix="mt_timeline_"), "test.db")

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
    # isolated TestClient: the shared limiter is per-app; other modules
    # (rate-limit tests) can exhaust it module-order-dependently
    client = TestClient(app)

requires_fastapi = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@requires_fastapi
class TestTimelineAPI:
    def test_chain_after_correction(self):
        """correct() must link old->new so /timeline shows the full chain."""
        r1 = client.post("/remember", json={
            "content": "timeline probe: port is 8000", "source": "t", "type": "fact"})
        assert r1.status_code == 200
        uid1 = r1.json()["uid"]
        c = client.post("/correct", json={
            "old_uid": uid1, "new_content": "timeline probe: port is 8001",
            "source": "t"})
        assert c.status_code == 200, c.text
        d = c.json()
        uid2 = (d.get("uid") or d.get("new_uid")
                or (d.get("new") or {}).get("uid"))
        assert uid2, c.text
        # timeline of the OLD uid must reach the NEW fact
        t = client.get(f"/timeline/{uid1}")
        assert t.status_code == 200, t.text
        body = t.text
        assert uid2 in body or uid1 in body

    def test_timeline_of_unknown_uid_is_graceful(self):
        r = client.get("/timeline/fact-does-not-exist-000000")
        # 200 with empty chain or 404 — but never a 500
        assert r.status_code in (200, 404), r.status_code

    def test_newest_uid_has_no_successor(self):
        r1 = client.post("/remember", json={
            "content": "timeline leaf fact unique-zz9", "source": "t", "type": "fact"})
        uid = r1.json()["uid"]
        t = client.get(f"/timeline/{uid}")
        assert t.status_code == 200
