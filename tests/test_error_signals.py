"""Tests for error_signals capture and distillation (2026-10-19).

★2026-10-19 修正（本轮功能对标实测踩到）：原版硬编码读
`E:/RUANJIAN/memory_hub/gateway.py`。那是**本机 hub 仓**的绝对路径，而
error_signals 是 **hub-only by design**（dual_repo_guard 白名单里明确列出
`capture_signal` 等 5 个函数为 hub-only）。后果：
  1) 发布仓（memtether）里跑这个测试 = 读一个不存在/不属于本仓的文件
  2) 一旦两仓同步流程把发布版 gateway.py 覆盖到 hub，这些测试立刻全红
     —— 本轮就真实发生了（我用 memtether/gateway.py 覆盖 hub，10 个测试全红）
改为**双仓自适应**：发布仓上这些函数按设计不存在，测试应断言
"hub-only 声明成立"（即 dual_repo_guard 白名单里有它）；hub 仓上才断言
"函数与 SCHEMA 真的在"。这样测试表达的是**设计契约**，不再是本机路径耦合。
"""
import os
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GW = os.path.join(_REPO_ROOT, "gateway.py")
_IS_HUB_REPO = os.path.basename(_REPO_ROOT).lower() == "memory_hub"


def _gw_src():
    with open(_GW, encoding="utf-8") as f:
        return f.read()


# error_signals 是 **hub-only by design**（dual_repo_guard 白名单明列）。
# 发布仓（memtether）断言"契约成立"（函数不应出现 / 白名单有登记）；
# hub 仓断言"真的实现"。
_HUB_ONLY_FNS = ("capture_signal", "capture_from_user_message",
                 "capture_from_tool_result", "get_recent_signals")


def _assert_hub_only(needle):
    code = _gw_src()
    if _IS_HUB_REPO:
        assert needle in code, "hub repo must implement %r" % needle
    else:
        # 发布仓：error_signals 按设计不存在。断言它**确实不在**，
        # 防止将来有人误把 hub-only 代码同步进发布仓（那就是 scope 泄漏）。
        assert needle not in code, (
            "%r leaked into the release repo — error_signals is hub-only "
            "by design (see dual_repo_guard WHITELIST)" % needle)



class TestErrorSignalSchema:
    """Verify error_signals table exists in SCHEMA."""

    def test_schema_contains_error_signals(self):
        _assert_hub_only("CREATE TABLE IF NOT EXISTS error_signals")

    def test_schema_has_signal_column(self):
        _assert_hub_only("signal TEXT NOT NULL")

    def test_capture_signal_function_exists(self):
        _assert_hub_only("def capture_signal(")

    def test_capture_from_user_message_exists(self):
        _assert_hub_only("def capture_from_user_message(")

    def test_capture_from_tool_result_exists(self):
        _assert_hub_only("def capture_from_tool_result(")

    def test_get_recent_signals_exists(self):
        _assert_hub_only("def get_recent_signals(")


class TestErrorSignalRegex:
    """Verify the regex patterns used for signal detection."""

    def test_correction_chinese(self):
        import re
        pattern = r'(不对|不是|错了|改回|其实应该|实际上|我说的是|你理解错|重新做)'
        assert re.search(pattern, '这个不对')

    def test_correction_english(self):
        import re
        pattern = r'(this is wrong|not correct|instead of|stop doing)'
        assert re.search(pattern, 'this is wrong', re.IGNORECASE)

    def test_rage_chinese(self):
        import re
        pattern = r'(傻逼|操你|滚|立刻|他妈)'
        assert re.search(pattern, '操你妈的')

    def test_rage_english(self):
        import re
        pattern = r'(stupid|fucking|damn)'
        assert re.search(pattern, 'stupid', re.IGNORECASE)

    def test_remark_chinese(self):
        import re
        pattern = r'(因为|根因是|原因是|坑是)'
        assert re.search(pattern, '因为GUI会崩')

    def test_remark_english(self):
        import re
        pattern = r'(because|the reason|root cause|the gotcha)'
        assert re.search(pattern, 'because it breaks', re.IGNORECASE)


class TestErrorSignalDedup:
    """Verify dedup logic in capture_signal."""

    def test_dedup_logic_exists(self):
        _assert_hub_only("duplicate within 1h")

    def test_dedup_checks_same_signal_and_context(self):
        _assert_hub_only("signal=? AND context=?")


class TestMCPServerCaptureSignal:
    """Verify mcp_server.py has capture_signal tool."""

    def test_mcp_has_capture_signal(self):
        """capture_signal MCP 工具同为 hub-only（dual_repo_guard 白名单登记
        ('mcp_server','fn','hub'): {'_reset_idle_timer','tool_capture_signal'}）。
        发布仓不应出现；hub 仓必须出现。"""
        with open(os.path.join(_REPO_ROOT, "mcp_server.py"), encoding="utf-8") as f:
            code = f.read()
        if _IS_HUB_REPO:
            assert '"capture_signal"' in code
        else:
            assert '"capture_signal"' not in code

    def test_mcp_handlers_includes_capture(self):
        with open(os.path.join(_REPO_ROOT, "mcp_server.py"), encoding="utf-8") as f:
            code = f.read()
        if _IS_HUB_REPO:
            assert "tool_capture_signal" in code
        else:
            assert "tool_capture_signal" not in code


class TestProjectionHasRule:
    """Verify projection includes the error review rule."""

    def test_projection_has_log_before_fixing(self):
        code = _gw_src()
        if not _IS_HUB_REPO:
            pytest.skip("projection text is hub-only by design")
        assert "log-before-fixing" in code

    def test_projection_has_core_behaviors(self):
        code = _gw_src()
        if not _IS_HUB_REPO:
            pytest.skip("projection text is hub-only by design")
        assert "六条核心行为" in code
