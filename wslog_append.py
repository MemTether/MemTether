# -*- coding: utf-8 -*-
"""
wslog_append.py —— 给「双方共写同一个 inode 的日志文件」用的原子追加工具。

为什么需要它
------------
两个 WorkBuddy 客户端的工作区 memory/ 目录是同一批物理文件（实测 11 个文件 inode 全同），
双方都往同一个 YYYY-MM-DD.md 日志里追加。而编辑器的 Edit 是「读全文 -> 替换 -> 整体写回」，
在我读完之后、写回之前，对方追加的内容会被静默抹掉，且日志没有快照、不可恢复。

本工具用 CreateFileW(FILE_APPEND_DATA) + WriteFile 做真正的原子追加：
不读全文、不 seek、不 truncate，因此永远不会抹掉别人的追加。

为什么不能用 Python 的 open(..., "a")
-------------------------------------
Windows CRT 下 O_APPEND 的实现是「先 seek 到末尾再 write」，seek 与 write 之间不是原子的，
多进程同时追加会互相覆盖（hubguard 铁律 5，实测踩过）。

用法
----
  python wslog_append.py <日志文件> --text "要追加的一段"
  python wslog_append.py <日志文件> --file 片段.txt
  python wslog_append.py --selftest            # 并发演练：8 进程 x 50 行，验证 0 丢行

选项
----
  --bak        追加前先做同目录 .bak-<时间戳> 快照
  --nl crlf|lf 强制换行（默认：跟随目标文件既有风格；文件不存在时用系统默认）
"""
import os
import sys
import time
import ctypes
import argparse
import subprocess
from ctypes import wintypes

# ---- Win32 原子追加 ----
FILE_APPEND_DATA = 0x00000004
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_CreateFileW = ctypes.windll.kernel32.CreateFileW
_CreateFileW.restype = wintypes.HANDLE
_WriteFile = ctypes.windll.kernel32.WriteFile
_CloseHandle = ctypes.windll.kernel32.CloseHandle
_GetLastError = ctypes.windll.kernel32.GetLastError


def atomic_append(path, data, retries=30):
    """
    真正的原子追加。返回写入字节数。

    ★必须重试：实测 8 进程并发各自 CreateFileW 时，会偶发打开失败（安全软件/索引器
    短暂持锁）。演练时表现为「整帧丢失一个进程的全部行」——不是丢行，是整个进程没写进去。
    单次调用失败就抛出，等于把一次瞬时争用变成永久丢数据。
    """
    last_err = None
    for attempt in range(retries):
        h = _CreateFileW(path, FILE_APPEND_DATA,
                         FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                         OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
        if h is not None and h != INVALID_HANDLE_VALUE:
            try:
                written = wintypes.DWORD(0)
                ok = _WriteFile(h, data, len(data), ctypes.byref(written), None)
                if not ok:
                    last_err = _GetLastError()
                else:
                    return written.value
            finally:
                _CloseHandle(h)
            # WriteFile 失败也走重试
        else:
            last_err = _GetLastError()
        time.sleep(min(0.002 * (attempt + 1), 0.05))
    raise OSError(last_err or 0, "CreateFileW/WriteFile 重试 %d 次仍失败: %s" % (retries, path))


def detect_newline(path, probe=65536):
    """跟随目标文件的既有换行风格；文件不存在时返回 None（调用方决定）。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, "rb") as f:
        head = f.read(probe)
    crlf = head.count(b"\r\n")
    lf = head.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def snapshot(path):
    """同目录 .bak-<时间戳> 快照。返回备份路径。"""
    bak = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "rb") as src, open(bak, "wb") as dst:
        while True:
            b = src.read(1 << 20)
            if not b:
                break
            dst.write(b)
    return bak


def append(path, text, do_bak=False, force_nl=None):
    nl = force_nl or detect_newline(path) or os.linesep.replace("\r", "\n")
    if nl not in ("\r\n", "\n"):
        nl = "\r\n"
    # 目标已有内容且不以换行结尾时，先补一个换行，避免粘连
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) not in (b"\n", b"\r"):
                prefix = nl
    data = (prefix + text.rstrip("\r\n") + nl).encode("utf-8")
    bak = snapshot(path) if (do_bak and os.path.exists(path)) else None
    n = atomic_append(path, data)
    # 回读断言：确认这段真的在文件末尾（或至少在文件里）
    with open(path, "rb") as f:
        tail = f.read()
    ok = data.strip() in tail
    return {"bytes": n, "bak": bak, "verify": ok}


# ---- 并发演练 ----
def selftest(nproc=8, nline=50):
    """故意制造并发覆盖，看它丢不丢行。同时给出 Read-Modify-Write 的对照。"""
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="wslog-st-")
    f_atomic = os.path.join(tmpdir, "atomic.log")
    f_rmw = os.path.join(tmpdir, "rmw.log")

    def worker_atomic(idx):
        for j in range(nline):
            atomic_append(f_atomic, ("P%02d-L%03d\n" % (idx, j)).encode("ascii"))

    def worker_rmw(idx):
        # 模拟编辑器的 Edit：读全文 -> 拼 -> 整体写回
        for j in range(nline):
            try:
                old = open(f_rmw, "r", encoding="utf-8").read()
            except FileNotFoundError:
                old = ""
            with open(f_rmw, "w", encoding="utf-8") as f:
                f.write(old + "P%02d-L%03d\n" % (idx, j))

    here = os.path.abspath(__file__)
    CNW = 0x08000000
    procs = []
    for i in range(nproc):
        for which, path in (("atomic", f_atomic), ("rmw", f_rmw)):
            p = subprocess.Popen([sys.executable, here, "--child", which, path,
                                  str(i), str(nline)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=CNW)
            procs.append(p)
    errs = []
    for p in procs:
        out, err = p.communicate()
        if p.returncode != 0 and err:
            errs.append((p.returncode, err.decode("utf-8", "replace").strip()[-300:]))
    if errs:
        print("  子进程非零退出 %d 个，样例：" % len(errs))
        for rc, e in errs[:3]:
            print("      rc=%d  %s" % (rc, e.replace("\n", " | ")))

    def check(path, label):
        lines = [l for l in open(path, "r", encoding="utf-8").read().splitlines() if l.strip()]
        uniq = len(set(lines))
        expect = nproc * nline
        print("  %-28s 实到 %d 行 / 唯一 %d / 期望 %d  -> 丢行 %d  %s"
              % (label, len(lines), uniq, expect, expect - uniq,
                 "PASS" if uniq == expect else "FAIL（发生静默覆盖）"))
        return uniq == expect

    print("并发演练：%d 进程 x %d 行" % (nproc, nline))
    a = check(f_atomic, "atomic_append（本工具）")
    b = check(f_rmw, "读-改-写（模拟 Edit）")
    print("  目录 %s" % tmpdir)
    return 0 if a else 1


if __name__ == "__main__":
    # child 分支必须在 argparse 之前处理：--child 后面跟的是位置参数，会被 argparse 当成未识别项
    if "--child" in sys.argv:
        # --child <which> <path> <idx> <nline>
        which, path = sys.argv[2], sys.argv[3]
        idx, n = int(sys.argv[4]), int(sys.argv[5])
        for j in range(n):
            if which == "atomic":
                atomic_append(path, ("P%02d-L%03d\n" % (idx, j)).encode("ascii"))
            else:
                try:
                    old = open(path, "r", encoding="utf-8").read()
                except FileNotFoundError:
                    old = ""
                with open(path, "w", encoding="utf-8") as f:
                    f.write(old + "P%02d-L%03d\n" % (idx, j))
        sys.exit(0)

    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="目标日志文件")
    ap.add_argument("--text", help="要追加的文本")
    ap.add_argument("--file", help="从文件读取要追加的内容")
    ap.add_argument("--bak", action="store_true", help="追加前先做 .bak 快照")
    ap.add_argument("--nl", choices=["crlf", "lf"], help="强制换行")
    ap.add_argument("--selftest", action="store_true", help="并发演练")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if not args.path:
        ap.error("需要 <日志文件>，或用 --selftest 做演练")
    text = args.text
    if args.file:
        text = open(args.file, "r", encoding="utf-8").read()
    if not text:
        ap.error("需要 --text 或 --file")
    force_nl = {"crlf": "\r\n", "lf": "\n"}.get(args.nl)
    r = append(args.path, text, do_bak=args.bak, force_nl=force_nl)
    print("追加 %d 字节 -> %s" % (r["bytes"], args.path))
    if r["bak"]:
        print("快照 -> %s" % r["bak"])
    print("回读断言 = %s" % ("PASS" if r["verify"] else "FAIL"))
    sys.exit(0 if r["verify"] else 1)
