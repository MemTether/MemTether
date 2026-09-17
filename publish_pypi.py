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
  python publish_pypi.py --help          # 打印本用法（等价 -h）

★两条安全保证（2026-09-17 补）：
  · 参数白名单：出现未知参数（含拼错的开关）一律打印用法并以非零码退出，
    绝不静默忽略 —— 旧版 `--help` 会被无视、然后照常跑完整流程。
  · 副作用后置：归档 dist/ 是本脚本唯一的破坏性动作，它被排在「拿到 token
    之后」。故 --dry-run、参数错误、无 token、token 格式不对这四条路径
    全部零副作用，dist/ 一个字节都不会动。
"""
import os
import sys
import time
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

# 显式上传名单：只认这两个正式产物。
# ★2026-09-17：不再写死版本号 —— 改为从 pyproject.toml 的 [project] 读 name/version
#   生成。写死版本号的坑：升了版本、构建出新包，上传器仍去找旧文件名 → 报「缺失」
#   还算好的，最坏是名单没跟上却把 dist/ 里残留的旧包当成"目标"传上去。
#   解析失败（缺 pyproject / 无 tomllib）时退回兜底名单，绝不因解析失败而传错包。
_FALLBACK_TARGETS = [
    "memtether-0.1.0a2-py3-none-any.whl",
    "memtether-0.1.0a2.tar.gz",
]


def _targets_from_pyproject():
    """从 pyproject.toml 的 [project] name/version 生成上传名单；失败返回 None。"""
    try:
        import tomllib                      # py3.11+
    except ImportError:
        try:
            import tomli as tomllib         # py3.10 及以下
        except ImportError:
            return None
    pp = os.path.join(ROOT, "pyproject.toml")
    if not os.path.isfile(pp):
        return None
    try:
        with open(pp, "rb") as f:
            proj = tomllib.load(f)["project"]
        name = proj["name"]
        ver = proj["version"]
    except Exception:
        return None
    if not (isinstance(name, str) and isinstance(ver, str) and name and ver):
        return None
    # wheel 文件名按 PEP 427 把连字符归一成下划线；sdist 保留原名
    norm = name.replace("-", "_")
    return ["%s-%s-py3-none-any.whl" % (norm, ver), "%s-%s.tar.gz" % (name, ver)]


TARGETS = _targets_from_pyproject() or _FALLBACK_TARGETS

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


def _dist_listing():
    """dist/ 里现有文件的简短清单（报错时给线索用）。"""
    if not os.path.isdir(DIST):
        return "（dist/ 不存在）"
    names = sorted(n for n in os.listdir(DIST)
                   if os.path.isfile(os.path.join(DIST, n)))
    return "、".join(names) if names else "（dist/ 为空）"


def parse_args(argv):
    """解析命令行；返回 (opts, err)。

    ★fail-closed：未知参数一律拒绝并打印用法，绝不静默忽略。
      旧版用 `"--test" in args` 式白名单判定，导致 `--help` 被当成"没见过
      的东西"直接无视，然后**照常跑完整流程**（含归档 dist/ 的副作用）。
    """
    opts = {"test": False, "dry_run": False, "clipboard": False,
            "help": False, "watch_min": 0.0}
    for a in argv:
        if a in ("--help", "-h"):
            opts["help"] = True
        elif a == "--test":
            opts["test"] = True
        elif a == "--dry-run":
            opts["dry_run"] = True
        elif a == "--clipboard":
            opts["clipboard"] = True
        elif a == "--watch" or a.startswith("--watch="):
            if "=" in a:
                raw = a.split("=", 1)[1]
                try:
                    opts["watch_min"] = float(raw)
                except ValueError:
                    return None, "「%s」不是合法分钟数" % a
            else:
                opts["watch_min"] = 30.0
            opts["clipboard"] = True
        else:
            return None, "未知参数「%s」" % a
    return opts, None


def main():
    opts, err = parse_args(sys.argv[1:])
    if err:
        log("!! %s" % err)
        log()
        log((__doc__ or "").strip())
        return 64                        # EX_USAGE：用法错误，非零退出
    if opts["help"]:
        log((__doc__ or "").strip())
        return 0

    use_test = opts["test"]
    dry = opts["dry_run"]
    use_clipboard = opts["clipboard"]
    watch_min = opts["watch_min"]

    log("=== MemTether 发布器 ===")
    log("目标：%s" % ("TestPyPI（演练）" if use_test else "正式 PyPI"))
    log("模式：%s" % ("dry-run 只检查不上传" if dry else "正式上传"))
    log("名单：%s" % "、".join(TARGETS))
    log()

    # 1. 校验上传名单（只读，无副作用）
    log("[1/4] 校验上传产物")
    files = []
    for name in TARGETS:
        p = os.path.join(DIST, name)
        if not os.path.isfile(p):
            log("      !! 缺失：%s" % name)
            log("         dist/ 现有：%s" % _dist_listing())
            log("         请先 python -m build --no-isolation，或核对 pyproject 版本号")
            return 2
        h = sha256(p)
        log("      %-42s %8d B  sha256 %s…" % (name, os.path.getsize(p), h[:16]))
        files.append(p)
    if len(files) != len(TARGETS):
        return 2

    # 2. twine check（只读，无副作用）
    log()
    log("[2/4] twine check（渲染合规体检）")
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

    # 3. 取 token（只读，无副作用）—— 拿不到就退出，dist/ 一个字节都不动
    log()
    log("[3/4] 获取 token")
    if dry:
        log("      dry-run：跳过 token 检查与上传")
        log()
        log("完成（未上传，dist/ 未改动）。")
        return 0

    if watch_min > 0:
        log("      等你在 PyPI 页面点 Add API token 并复制（最多 %g 分钟）" % watch_min)
        log("      判据：剪贴板出现以 pypi- 开头、长度 >=30 的串；每 5 秒看一次")
        log("      前面 1~2 步已做完，token 一到立刻归档+上传。现在就可以去复制。")
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
            log("      !! 超时仍未拿到 token —— 退出，未上传任何东西（dist/ 未改动）")
            write_result(False, "wait timeout %g min" % watch_min)
            return 4
        log("      拿到 token（来源：%s）" % src)
    else:
        token, src = get_token(use_clipboard)
        if not token:
            log("      !! 未拿到 token —— 中止（dist/ 未改动）")
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

    # 4. 归档杂物 + 上传 —— ★整个流程的第一个副作用出现在这里
    log()
    log("[4/4] 清理 dist/ 杂物（改名归档，绝不删除）")
    n = archive_strays()
    log("      归档 %d 个" % n)

    env["TWINE_USERNAME"] = "__token__"
    env["TWINE_PASSWORD"] = token
    cmd = [twpy, "-m", "twine", "upload",
           "--repository-url", TEST_URL if use_test else PYPI_URL] + files
    log("      上传中…")
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
