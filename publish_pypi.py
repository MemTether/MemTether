#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
publish_pypi.py — MemTether 发布到 PyPI 的一键上传器（A2 收口）

设计要点（都是踩过的坑）：
1. token 只从环境变量读，绝不落盘、绝不进 git
   —— 本机 git 历史里已出过「提交身份泄露」事件，凭据更不能写进文件
2. 上传名单显式给出，绝不用 dist/*
   —— dist/ 里混着 .pre-hubguard 归档包，dist/* 会连它们一起传上去
3. 归档用改名（os.rename），绝不用 os.remove/rmtree
   —— 本机 safe-delete 是 fail-closed，删除动作一律不被允许
4. 子进程一律 CREATE_NO_WINDOW(0x08000000) 且捕获回显
   —— 屏幕占用铁律：不弹窗、不占屏
5. 先 twine check 再 upload，check 不过绝不上传

用法：
  set TWINE_PASSWORD=pypi-xxxx...
  python publish_pypi.py                 # 正式 PyPI（从环境变量读 token）
  python publish_pypi.py --test          # TestPyPI（需单独账号的 token）
  python publish_pypi.py --dry-run       # 只做检查+名单，不上传
  python publish_pypi.py --clipboard     # token 从 Windows 剪贴板读
  python publish_pypi.py --watch=30      # 等你复制 token，命中即自动跑完（默认 30 分钟）
                                         # （PyPI 显示 token 后点复制按钮
                                         # 立即跑这条；上传成功后剪贴板自动清空）
                                         # token 优先顺序: 环境变量 > --clipboard
"""
import os
import sys
import glob
import time
import shutil
import hashlib
import json
import subprocess

# Windows 剪贴板支持：subprocess 调 PowerShell Get/Set-Clipboard
# —— ctypes 在某些 Windows 配置上会让 Python 进程 SIGSEGV(exit 139)
# —— subprocess 路线慢一点(1-2s), 但稳定且不会崩进程
_clipboard = None
if sys.platform == "win32":
    try:
        def _clipboard_text():
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-Clipboard -Raw"],
                               capture_output=True, encoding="utf-8",
                               errors="replace", creationflags=CREATE_NO_WINDOW,
                               timeout=5)
            if r.returncode != 0:
                return ""
            return (r.stdout or "").rstrip("\r\n")

        def _clipboard_clear():
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Set-Clipboard -Value $null"],
                           capture_output=True, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW,
                           timeout=5)

        _clipboard = {"get": _clipboard_text, "clear": _clipboard_clear}
    except Exception:
        _clipboard = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
ARCHIVE_DIR = os.path.join(ROOT, "_archive")
CREATE_NO_WINDOW = 0x08000000

# 显式上传名单：只认这两个正式产物
TARGETS = [
    "memtether-0.1.0a2-py3-none-any.whl",
    "memtether-0.1.0a2.tar.gz",
]

PYPI_URL = "https://upload.pypi.org/legacy/"
TEST_URL = "https://test.pypi.org/legacy/"


def log(msg=""):
    print(msg, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_strays():
    """把 dist/ 里非上传目标的杂物改名归档（不删除）。

    目录也要归档：python -m build 会在 dist/ 下留 .tmp-* 目录，里面躺着
    上一轮的同名 tar.gz。本发布器用显式名单上传所以不受影响，但只要有人
    改成 dist/* 就会把旧包一起传上去。
    """
    if not os.path.isdir(DIST):
        return 0
    keep = set(TARGETS)
    moved = 0
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if name in keep:
            continue
        dst = os.path.join(ARCHIVE_DIR, "%s.arch-%s" % (name, ts))
        try:
            os.rename(p, dst)
        except FileExistsError:
            dst = os.path.join(ARCHIVE_DIR, "%s.arch-%s-%d" % (name, ts, moved))
            os.rename(p, dst)
        log("  归档 %s -> _archive/%s" % (name, os.path.basename(dst)))
        moved += 1
    return moved


def run(cmd, env=None, cwd=None):
    return subprocess.run(cmd, capture_output=True, encoding="utf-8",
                          errors="replace", creationflags=CREATE_NO_WINDOW,
                          env=env, cwd=cwd or ROOT)


# twine 装在中枢 venv 里，不在随便哪个 python 上 —— 必须探测，不能假设。
# 本机盘符/目录布局属个人隐私，绝不硬编码进开源包：
#   PUBLISH_PY   —— 直接指定解释器（最高优先级）
#   MEM_HUB_DIR  —— 记忆中枢根目录，自动拼 .venv-memory/Scripts/python.exe
def _candidate_py():
    cands = [os.environ.get("PUBLISH_PY", "")]
    hub = os.environ.get("MEM_HUB_DIR", "").strip()
    if hub:
        for v in (".venv-memory", ".venv-mem", ".venv", "venv"):
            cands.append(os.path.join(hub, v, "Scripts", "python.exe"))
            cands.append(os.path.join(hub, v, "bin", "python"))
    cands.append(sys.executable)
    return cands


CANDIDATE_PY = _candidate_py()


def find_twine_py():
    """返回第一个 import twine 成功的解释器；找不到返回 None。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for py in CANDIDATE_PY:
        if not py or not os.path.isfile(py):
            continue
        r = run([py, "-c", "import twine"], env=env)
        if r.returncode == 0:
            return py
    return None


def get_token(use_clipboard):
    """返回 (token, src)；拿不到则 ("", "")。token 只在内存，不落盘。"""
    tok = os.environ.get("TWINE_PASSWORD", "").strip()
    if tok:
        return tok, "环境变量 TWINE_PASSWORD"
    if use_clipboard and _clipboard:
        try:
            clip = _clipboard["get"]()
        except Exception:
            clip = ""
        clip = (clip or "").strip()
        if clip.startswith("pypi-") and len(clip) >= 30:
            return clip, "Windows 剪贴板"
    return "", ""


def write_result(ok, msg, url=""):
    """把结果落到 dist/.upload-result.json（dist 已在 .gitignore，绝不写 token）。"""
    try:
        with open(os.path.join(DIST, ".upload-result.json"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "ok": ok, "msg": msg, "url": url,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False, indent=2))
    except Exception:
        pass


def main():
    args = [a for a in sys.argv[1:]]
    use_test = "--test" in args
    dry = "--dry-run" in args
    use_clipboard = "--clipboard" in args
    watch_min = 0
    for a in args:
        if a.startswith("--watch"):
            watch_min = float(a.split("=")[1]) if "=" in a else 30.0
            use_clipboard = True

    log("=== MemTether 发布器 ===")
    log("目标：%s" % ("TestPyPI（演练）" if use_test else "正式 PyPI"))
    log("模式：%s" % ("dry-run 只检查不上传" if dry else "正式上传"))
    log()

    # 1. 归档杂物
    log("[1/4] 清理 dist/ 杂物（改名归档，绝不删除）")
    n = archive_strays()
    log("      归档 %d 个" % n)

    # 2. 校验上传名单
    log()
    log("[2/4] 校验上传产物")
    files = []
    for name in TARGETS:
        p = os.path.join(DIST, name)
        if not os.path.isfile(p):
            log("      !! 缺失：%s —— 中止（请先 python -m build --no-isolation）" % name)
            return 2
        h = sha256(p)
        log("      %-42s %8d B  sha256 %s…" % (name, os.path.getsize(p), h[:16]))
        files.append(p)
    if len(files) != len(TARGETS):
        return 2

    # 3. twine check
    log()
    log("[3/4] twine check（渲染合规体检）")
    twpy = find_twine_py()
    if not twpy:
        log("      !! 找不到装了 twine 的解释器 —— 中止")
        log("         可用：pip install twine，或设环境变量 PUBLISH_PY=<python.exe>")
        return 3
    log("      解释器：%s" % twpy)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = run([twpy, "-m", "twine", "check"] + files, env=env)
    tail = (r.stdout or "").strip().splitlines()
    for line in tail[-6:]:
        log("      %s" % line)
    if r.returncode != 0:
        log("      !! twine check 未通过 —— 中止，不上传")
        if r.stderr:
            log("      %s" % r.stderr.strip()[:400])
        return 3
    log("      check 通过")

    # 4. 上传
    log()
    if dry:
        log("[4/4] dry-run：跳过上传。正式执行去掉 --dry-run，并先设 TWINE_PASSWORD")
        log()
        log("完成（未上传）。")
        return 0

    if watch_min > 0:
        log("[4/4] 等你在 PyPI 页面点 Add API token 并复制（最多 %g 分钟）" % watch_min)
        log("      判据：剪贴板出现以 pypi- 开头、长度 >=30 的串；每 5 秒看一次")
        log("      前面 1~3 步已做完，token 一到立刻上传。现在就可以去复制。")
        deadline = time.time() + watch_min * 60
        waited = 0
        token, src = get_token(True)
        while not token and time.time() < deadline:
            time.sleep(5)
            waited += 5
            if waited % 60 == 0:
                log("      …已等 %d 分钟" % (waited // 60))
            token, src = get_token(True)
        if not token:
            log("      !! 超时仍未拿到 token —— 退出，未上传任何东西")
            write_result(False, "wait timeout %g min" % watch_min)
            return 4
        log("      拿到 token（来源：%s），开始上传" % src)
    else:
        token, src = get_token(use_clipboard)
        if not token:
            log("[4/4] !! 未拿到 token —— 中止")
            if use_clipboard:
                log("         --clipboard：剪贴板内容不是 pypi- 开头或太短")
                log("         可改用: set TWINE_PASSWORD=pypi-xxx && python publish_pypi.py")
            else:
                log("         请先执行： set TWINE_PASSWORD=pypi-xxxxxxxxxxxxxxxx")
                log("         或: python publish_pypi.py --clipboard")
            return 4
    if not token.startswith("pypi-"):
        log("      !! token 不以 pypi- 开头（来源：%s），请确认你复制完整（含前缀）" % src)
        return 4
    log("      token 来源：%s（上传成功后自动清剪贴板）" % src)

    env["TWINE_USERNAME"] = "__token__"
    env["TWINE_PASSWORD"] = token
    cmd = [twpy, "-m", "twine", "upload",
           "--repository-url", TEST_URL if use_test else PYPI_URL] + files
    log("[4/4] 上传中…")
    r = run(cmd, env=env)
    out = (r.stdout or "").strip()
    for line in out.splitlines()[-12:]:
        log("      %s" % line)
    if r.returncode != 0:
        log()
        log("      !! 上传失败 rc=%d" % r.returncode)
        err = (r.stderr or "").strip()
        if err:
            for line in err.splitlines()[-10:]:
                log("      %s" % line)
        if "403" in out + err:
            log()
            log("      403 最常见两个原因：")
            log("        ① token 的 scope 限定了项目 —— 首次上传新项目必须全账号 token")
            log("        ② 你还没验证邮箱，或没开 2FA")
        write_result(False, "upload failed rc=%d" % r.returncode)
        return 5

    url = ("https://test.pypi.org/project/memtether/"
           if use_test else "https://pypi.org/project/memtether/")
    log()
    log("上传成功。")
    log("  查看：%s" % url)
    write_result(True, "uploaded", url)
    # 上传成功后：剪贴板来源则清剪贴板；环境变量来源不动
    if src == "Windows 剪贴板" and _clipboard:
        try:
            _clipboard["clear"]()
            log("  剪贴板已清空。")
        except Exception as e:
            log("  清剪贴板失败（不影响上传结果）：%s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
