# -*- coding: utf-8 -*-
"""cli_out.py — Windows 下安全输出到 stdout 的共用助手

为什么需要它（2026-10-21 实测，本机 4 个脚本真实崩溃）：
  Windows 默认 locale 是 cp936/gbk，print() 遇到 ✓ ✗ 🦞 ✅ ⇒ 等字符
  直接 UnicodeEncodeError 崩溃，stdout 全空。已中招的四个：
    memory_hub/bootstrap.py            (L102, 🦞)
    memory_hub/smoke_bitemporal.py     (L68,  ✓/✗)
    memtether/scripts/scan_leaks.py    (L222, ✗)
    memtether/scripts/scan_history_leaks.py (L171, ✗)
  后两个尤其危险：它们是**发布前安全扫描器**，崩在"无法判断"分支上，
  等于把本该报出的 "未参与扫描" 告警吞掉了 —— 典型的静默失败。

用法：
    from cli_out import emit
    emit("...")
  或保留 print 调用形式：
    from cli_out import print
"""
import sys


def emit(text=''):
    """把文本以 UTF-8 写到 stdout，绕开 Windows locale 编码坑。

    - 走 sys.stdout.buffer 直接写字节，不经 text layer
    - buffer 不可用（被替换/重定向）时退化为 replace 编码的文本写
    - 全部异常吞掉：输出失败绝不该让业务逻辑崩掉
    - 无参调用合法（等价于裸 print() 输出一个换行）
    """
    if not isinstance(text, str):
        text = str(text)
    data = text.encode('utf-8', 'replace')
    try:
        buf = getattr(sys.stdout, 'buffer', None)
        if buf is not None:
            buf.write(data)
            buf.flush()
            return
    except Exception:
        pass
    try:
        sys.stdout.write(data.decode('utf-8', 'replace'))
    except Exception:
        pass


def print(*args, **kwargs):  # noqa: A001
    """drop-in 替换内置 print，UTF-8 安全。"""
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    emit(sep.join(str(a) for a in args) + end)
