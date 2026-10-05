# -*- coding: utf-8 -*-
"""Federation: resolve_conflict + peer pull/push roundtrip (P-TA, 2026-10-05)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(prefix="mt_fed_"), "t.db")

import memtether_exchange as mx


class TestResolveConflict:
    def test_latest_wins(self):
        a = {"uid": "a", "updated_at": "2026-01-02", "content": "a", "source": "x"}
        b = {"uid": "b", "updated_at": "2026-01-03", "content": "b", "source": "x"}
        keep, retire, reason = mx.resolve_conflict(a, b, "latest_wins")
        assert keep is b and retire is a

    def test_source_priority(self):
        a = {"uid": "a", "updated_at": "2026-01-02", "content": "a", "source": "codex"}
        b = {"uid": "b", "updated_at": "2026-01-03", "content": "b", "source": "random"}
        keep, retire, _ = mx.resolve_conflict(a, b, "source_priority")
        assert keep is a  # codex outranks random even though b is newer

    def test_merge_concat(self):
        a = {"uid": "a", "updated_at": "2026-01-02", "content": "aaa", "source": "x"}
        b = {"uid": "b", "updated_at": "2026-01-03", "content": "bbb", "source": "x"}
        keep, retire, reason = mx.resolve_conflict(a, b, "merge_concat")
        assert "aaa" in keep["content"] and "bbb" in keep["content"]
        assert retire is b

    def test_human_review_declines(self):
        a = {"uid": "a", "updated_at": "x", "content": "c", "source": "s"}
        keep, retire, reason = mx.resolve_conflict(a, a, "human_review")
        assert keep is None and retire is None


class TestFederationDemo:
    def test_demo_passes(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEM_")}
        r = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "scripts", "demo_federation.py")],
            capture_output=True, text=True, timeout=120, env=env)
        assert "RESULT: PASS" in r.stdout, r.stdout[-500:] + r.stderr[-300:]
