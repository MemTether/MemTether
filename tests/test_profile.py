# -*- coding: utf-8 -*-
"""tests/test_profile.py -- deterministic subject representation (2026-10-19).

Motivation (capability audit, 2026-10-19): supermemory (31.3K stars) ships
`get_profile` ("~50ms user profiles") and honcho (7.7K stars) ships
`get_representation` ("contextualized snapshot insertable into a system
prompt"). Both aggregate everything known about one user/peer into a single
representation. MemTether had a `subject` column (filterable) but no
aggregation entry point.

Design constraint that these tests lock: the aggregation is **deterministic**
(zero LLM, zero hallucination) -- the same DB always yields the same profile,
and every line traces back to a uid. That is the project's "no花架子 / no AI
hallucination" axiom applied to a feature the competitors implement with an LLM.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gateway


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Sandboxed DB.

    ★必须 importlib.reload：gateway 在**import 时**就把 DB 路径定成模块常量，
      之后再设 MEM_DB 环境变量已经晚了（第一版就踩了这个坑 —— 测试连到
      生产库，total=38 而不是 5）。模式与 tests/test_gateway_deep.py::_setup 一致。
    """
    monkeypatch.setenv("MEM_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
    import importlib
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


def _seed(gw, subject="alice"):
    rows = [
        ("用户偏好暗色主题", "preference", 0.95),
        ("用户使用 Windows + PowerShell", "environment", 0.90),
        ("用户对费用敏感，烧钱动作要先报预算", "decision", 0.92),
        ("用户暴怒词=傻逼/操/滚/立刻", "fact", 0.88),
        ("用户要求不敷衍，必须有运行证据", "fact", 0.91),
    ]
    for content, typ, conf in rows:
        gw.remember(content, type=typ, source="probe",
                    confidence=conf, subject=subject)
    return rows


class TestProfileAggregation:
    def test_counts_by_type(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        assert p["total"] == 5
        assert p["counts"] == {"preference": 1, "environment": 1,
                               "decision": 1, "fact": 2}

    def test_sources_breakdown(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        assert p["sources"] == {"probe": 5}

    def test_high_confidence_count(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        # 0.95 / 0.90 / 0.92 / 0.91 are >= 0.9; 0.88 is not
        assert p["high_confidence_count"] == 4

    def test_time_span(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        assert p["oldest"] and p["newest"]
        assert p["oldest"] <= p["newest"]

    def test_l0_digest_has_one_line_per_top_fact(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        lines = [l for l in p["l0_digest"].split("\n") if l.strip()]
        assert len(lines) == len(p["top_facts"])
        assert all(l.startswith("- ") for l in lines)

    def test_digest_contains_conclusions_not_metadata(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        assert "暗色主题" in p["l0_digest"]
        assert "source=probe" not in p["l0_digest"]

    def test_top_facts_carry_uid_for_traceability(self, db):
        _seed(db)
        p = db.profile(subject="alice")
        for f in p["top_facts"]:
            assert f["uid"].startswith("fact-")
            assert f["lead"]


class TestProfileIsolation:
    def test_subject_isolation(self, db):
        _seed(db, subject="alice")
        db.remember("bob 的私密记忆", type="fact", source="probe", subject="bob")
        a = db.profile(subject="alice")
        b = db.profile(subject="bob")
        assert a["total"] == 5 and b["total"] == 1
        assert "暗色主题" not in b["l0_digest"]
        assert "bob 的私密记忆" in b["l0_digest"]

    def test_unknown_subject_is_empty_not_error(self, db):
        _seed(db)
        p = db.profile(subject="nobody-here")
        assert p["empty"] is True
        assert p.get("total", 0) == 0
        assert p["l0_digest"] == ""

    def test_retired_excluded_by_default(self, db):
        _seed(db)
        uid = db.profile(subject="alice")["top_facts"][0]["uid"]
        db.retire(uid, "probe")
        assert db.profile(subject="alice")["total"] == 4
        assert db.profile(subject="alice", include_retired=True)["total"] == 5

    def test_scope_filter(self, db):
        _seed(db)   # 5 条默认 scope='shared'
        # 再加 1 shared + 1 private
        db.remember("extra shared one", type="fact", source="probe",
                    subject="alice", scope="shared")
        db.remember("private one", type="fact", source="probe",
                    subject="alice", scope="private")
        assert db.profile(subject="alice")["total"] == 7
        p = db.profile(subject="alice", scope="shared")
        assert p["total"] == 6
        assert "private one" not in p["l0_digest"]
        assert "extra shared one" in p["l0_digest"]
        assert db.profile(subject="alice", scope="private")["total"] == 1


class TestProfileDeterminism:
    def test_same_db_same_profile(self, db):
        _seed(db)
        a = db.profile(subject="alice")
        b = db.profile(subject="alice")
        assert a == b

    def test_max_facts_respected(self, db):
        for i in range(30):
            db.remember("fact number %d" % i, type="fact",
                        source="probe", subject="alice")
        p = db.profile(subject="alice", max_facts=10)
        assert p["total"] == 10


class TestRevStrHelper:
    """_rev_str is what makes (q_value DESC, time DESC) sortable in one tuple."""

    def test_reverses_lexicographic_order(self):
        import memsearch  # noqa: F401  (ensure package importable)
        assert gateway._rev_str("b") < gateway._rev_str("a")

    def test_empty_string_is_safe(self):
        assert gateway._rev_str("") == ""
        assert gateway._rev_str(None) == ""
