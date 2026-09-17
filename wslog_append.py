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

★与 hubguard 的关系（2026-09-17 收编）
------------------------------------
本模块**只保留公开契约**（`atomic_append` 返回 int、`detect_newline` 探测不到返回 None）
与**自带降级实现**（保证单文件可跑、不因缺 hubguard 而失效）。规则本体一律在 hubguard：

  · `atomic_append`   -> `hubguard.safe_append_bytes`（原子追加全仓库只有一份实现）
  · `detect_newline`  -> `hubguard.detect_newline`（换行跟随只有一条规则）
  · `snapshot`        -> 改名 `make_bak`（与 `hubguard.snapshot` 的**指纹 dict**同名异义）

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

# ---- Win32 原子追加（自带降级实现用）----
FILE_APPEND_DATA = 0x00000004
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
CNW = 0x08000000                      # CREATE_NO_WINDOW：一律不弹窗、不抢屏

_CreateFileW = ctypes.windll.kernel32.CreateFileW
_CreateFileW.restype = wintypes.HANDLE
_WriteFile = ctypes.windll.kernel32.WriteFile
_CloseHandle = ctypes.windll.kernel32.CloseHandle
_GetLastError = ctypes.windll.kernel32.GetLastError

try:
    import hubguard as _hg
except ImportError:                   # 单文件直接跑（未装包 / 不在仓库根）
    _hg = None


def _fallback_atomic_append(path, data, retries=30):
    """hubguard 不可用时的自带实现。返回写入字节数。

    ★与 `hubguard._win_append` 保持同一套边界（2026-09-17 对齐）：
      · **只有"打开失败"才重试** —— 安全软件/索引器短暂持锁是**瞬时争用**，重试有意义；
        单次失败就抛出，等于把一次瞬时争用变成永久丢数据（实测表现为"整帧丢失一个进程"）。
      · 拿到句柄后 `WriteFile` 失败 -> **立即抛出、绝不重试**。

    ★旧版本这里是"WriteFile 失败也走重试"，那是个**真 bug**：WriteFile 返回 FALSE 时
      Windows 并不保证 lpNumberOfBytesWritten 有效，我们无法知道已落了多少字节 ——
      按 0 重发整段**可能产出重复内容**（长度对得上、格式也对、就是多了半条），
      比丢行难发现得多。故 fail-closed：抛异常，让调用方自己处置。
    """
    last_err = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        h = _CreateFileW(path, FILE_APPEND_DATA,
                         FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                         OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
        if h is None or h == INVALID_HANDLE_VALUE:
            last_err = _GetLastError()
            time.sleep(min(0.002 * (attempt + 1), 0.05))
            continue
        try:
            written = wintypes.DWORD(0)
            if not _WriteFile(h, data, len(data), ctypes.byref(written), None):
                raise OSError(
                    _GetLastError(),
                    "原子追加写入失败：已获得句柄后写失败，无法确定已落盘字节数，"
                    "拒绝重试以免内容重复 -> %s" % path)
            if written.value == 0:
                raise OSError(0, "WriteFile 写入 0 字节 -> %s" % path)
            return written.value
        finally:
            _CloseHandle(h)
    raise OSError(last_err or 0,
                  "CreateFileW 重试 %d 次仍无法打开: %s" % (attempts, path))


def atomic_append(path, data, retries=30):
    """真正的原子追加。**返回写入字节数（int）**。

    ★委托 `hubguard.safe_append_bytes` —— 保证"原子追加"在仓库里只有一份实现。
      hubguard 那边返回的是**结果 dict**，这里必须解包出 `bytes`，
      才能保住本模块既有的 **int 契约**（老调用方可能直接拿返回值当字节数用）。
    ★`retries` 必须**透传**：默认 30 是承载行为（瞬时争用重试），
      委托时把它丢掉 = 悄悄把门禁从 30 次降到 1 次，属静默降级。
    """
    if _hg is not None:
        return _hg.safe_append_bytes(path, data, retries=retries)['bytes']
    return _fallback_atomic_append(path, data, retries=retries)


def detect_newline(path, probe=65536):
    """跟随目标文件的既有换行风格；**文件不存在或为空时返回 None**（调用方决定）。

    ★契约与 hubguard **不同、且必须保住**：`hubguard.detect_newline` 在目标不存在时
      返回 `os.linesep` 的归一形式（Windows = CRLF），而本模块的 CLI 需要
      "探测不到就让调用方决定"，所以先判 exists/空、再委托。
    ★规则本体已收归 hubguard（**前 64KB 里出现 CRLF 就判 CRLF**）。旧版本这里是
      "CRLF 与 LF 谁多听谁的"——与 `hubguard` / `slot_update` 的判据不一致。
      同一个文件被两个工具按两种规则追加换行，正是本仓库最想根除的那类静默分叉，故统一。
      （`probe` 参数仅为兼容旧签名保留，委托路径下不再使用。）
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    if _hg is not None:
        return _hg.detect_newline(path)
    with open(path, "rb") as f:
        head = f.read(probe)
    return "\r\n" if b"\r\n" in head else "\n"


def make_bak(path):
    """同目录 `.bak-<时间戳>` 快照。**返回备份文件路径**。

    ★2026-09-17 从 `snapshot` 改名。原因：`hubguard.snapshot(path)` 返回的是
      **指纹 dict**（sha256/size/mtime_ns/st_ino/...），本函数返回的是**备份路径** ——
      同一个包里两个 `snapshot`、同名异义，正是"看着像在用守卫、其实调错语义"的陷阱。
      （已确证全仓库对本模块**零程序化 import**，改名不影响任何调用方。）
    """
    bak = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "rb") as src, open(bak, "wb") as dst:
        while True:
            b = src.read(1 << 20)
            if not b:
                break
            dst.write(b)
    return bak


def append(path, text, do_bak=False, force_nl=None):
    # 换行优先级：显式指定 > 跟随目标 > 系统默认（Windows = CRLF）。
    # ★旧版这里写的是 `os.linesep.replace("\r", "\n")`，在 Windows 上会算出 '\n\n'，
    #   靠下面那句 `if nl not in (...)` 兜回 CRLF —— 结果对，但纯属巧合、读不出意图。
    #   现在把兜底写成显式常量，意图与结果一致。
    nl = force_nl or detect_newline(path) or ("\r\n" if os.name == "nt" else "\n")
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
    bak = make_bak(path) if (do_bak and os.path.exists(path)) else None
    n = atomic_append(path, data)
    # 回读断言：确认这段真的在文件末尾（或至少在文件里）
    with open(path, "rb") as f:
        tail = f.read()
    ok = data.strip() in tail
    return {"bytes": n, "bak": bak, "verify": ok}


def _make_junction(link, target):
    """建目录联接（不需要管理员）。返回 `(ok, 说明)`。

    ★两个必须踩过的坑（2026-09-17 实测，演练里两例双双 SKIP 就是这两条）：
      ① **路径必须 normpath 成反斜杠**：`cmd.exe` 的内建命令 `mklink` 不认正斜杠 ——
         传 `E:/Temp/x/alias` 会回「无效开关 - "/"」，命令**根本没执行**；
         而 Python 侧 `os.path` 用正斜杠照样能跑，所以这个坑只在 subprocess 到 cmd 时暴露。
      ② **回显必须按 GBK 解码**：cmd 输出的是 OEM 代码页（中文机 = GBK/936），
         按 utf-8 解会得到 `��Ч����` 这种乱码，看不见真正的失败原因。
    """
    link, target = os.path.normpath(link), os.path.normpath(target)
    if os.path.exists(link):
        return False, "已存在"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, creationflags=CNW)
    ok = os.path.isdir(link) and \
        os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(target))
    detail = (r.stdout or b"").decode("gbk", "replace").strip() or \
             (r.stderr or b"").decode("gbk", "replace").strip()
    return ok, detail


# ---- 并发演练 ----
def selftest(nproc=8, nline=50):
    """故意制造并发覆盖，看它丢不丢行。同时给出 Read-Modify-Write 的对照。

    三段：
      ① `atomic_append` 直写       -> 必须 0 丢行
      ② 读-改-写（模拟编辑器 Edit）-> **必须丢行**（否则这段对照本身失效，
         说明"故意制造的覆盖"没造出来，演练结论作废 —— 受控演练的前提是故障真注入）
      ③ `atomic_append` **经 junction 别名** -> 必须 0 丢行
         （证明"接进跨客户端共享文件"之后，走别名路径同样不丢）
    """
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="wslog-st-")
    f_atomic = os.path.join(tmpdir, "atomic.log")
    f_rmw = os.path.join(tmpdir, "rmw.log")

    # 第三段：junction（目录级别名）。这是跨客户端共享的真实形态 ——
    # 实测本机 15 条工作区路径共享同一 inode，靠的正是"父目录是 junction"。
    j_real = os.path.join(tmpdir, "jreal")
    j_link = os.path.join(tmpdir, "jlink")
    os.makedirs(j_real, exist_ok=True)
    junc_applicable = (os.name == "nt")
    if junc_applicable:
        j_ok, j_detail = _make_junction(j_link, j_real)
        f_junc = os.path.join(j_link if j_ok else j_real, "junc.log")
    else:
        j_ok, j_detail, f_junc = True, "非 Windows，本段不适用", os.path.join(j_real, "junc.log")

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
    procs = []
    for i in range(nproc):
        for which, path in (("atomic", f_atomic), ("rmw", f_rmw),
                            ("atomic", f_junc)):
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
    c = check(f_junc, "atomic_append 经 junction")
    if not j_ok:
        print("  %-28s 建 junction 失败（%s）" % ("junction 环境", j_detail))
    print("  目录 %s" % tmpdir)

    # ★对照段必须真的**丢行**：如果读-改-写竟然一行不丢，说明这次没造出覆盖，
    #   那么"本工具 0 丢行"就没有对照组可比 —— 结论不能算数（fail-closed）。
    if b:
        print("  ★对照段未丢行 -> 本次未造出覆盖，演练结论不成立（不计通过）")
    if not junc_applicable:
        print("  ★junction 段不适用（非 Windows）—— 该段未验证，不计入通过")
    return 0 if (a and not b and c and j_ok and junc_applicable) else 1


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
