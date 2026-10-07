# -*- coding: utf-8 -*-
"""tests/test_dashboard_e2e.py — a47: dashboard end-to-end

a40 shipped a wheel where /dashboard returned "Dashboard not found"
while /health said 200 — CI never caught it because no test booted the
real server. This test starts actual uvicorn (not TestClient) and
asserts the dashboard HTML is served with real content.
"""
import os, socket, subprocess, sys, time, tempfile, urllib.request, pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_server():
    db_dir = tempfile.mkdtemp(prefix="mt_dash_e2e_")
    env = dict(os.environ)
    env["MEM_DB"] = os.path.join(db_dir, "test.db")
    env["PYTHONIOENCODING"] = "utf-8"
    port = _free_port()
    proc = subprocess.Popen(
        [PY, "-X", "utf8", "-m", "uvicorn", "api_server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    # wait for /health to come up (max 20s)
    up = False
    for _ in range(40):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.5)
    if not up:
        proc.kill()
        err = proc.stderr.read().decode("utf-8", "replace")
        pytest.fail(f"server did not start; stderr={err[:500]}")
    yield base
    proc.kill()


class TestDashboardE2E:
    def test_health_ok(self, live_server):
        with urllib.request.urlopen(live_server + "/health", timeout=10) as r:
            assert r.status == 200

    def test_dashboard_served_with_content(self, live_server):
        """Regression for the a40 incident: must NOT return 'Dashboard not found'."""
        with urllib.request.urlopen(live_server + "/dashboard", timeout=10) as r:
            body = r.read().decode("utf-8")
        assert r.status == 200
        assert "Dashboard not found" not in body
        assert len(body) > 2000, f"dashboard suspiciously small: {len(body)} bytes"

    def test_stats_endpoint_json(self, live_server):
        with urllib.request.urlopen(live_server + "/stats", timeout=10) as r:
            assert r.status == 200
