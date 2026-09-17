#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selfcheck.py —— 发布前**一键自检**（只读 · 编排已有闸门 + 自实现缺失项）

为什么需要它
------------
2026-09-17 发布 v0.1.0a2 时，所有核验都是**手工**做的：发布回读、附件 digest 比对、
正文段落完整性、a1 未被改动、日志/技能字符硬顶……一共十几项，散在几十条命令里。
手工核验的问题是：**做没做、做全没做，事后无法证明**。
本项目的头号卖点是「可验证性」，而可验证性的最低要求是
**「一条命令跑出报告，退出码真的代表结论」**。

本脚本把这些核验固化成一条命令。它**不重新实现**已有的闸门 ——
`check_packaging.py` / `scan_leaks.py` / `scan_history_leaks.py` 已经写好且退出码有契约，
这里只做**编排 + 聚合 + 补上它们没覆盖的项**。

编排的三个子闸门（退出码契约照搬，不解释）
------------------------------------------
    check_packaging.py      0=一致 / 1=有差异 / 2=读不到配置
    scan_leaks.py           0=干净 / 1=有 BLOCK / 2=词表缺失降级
    scan_history_leaks.py   0=干净 / 1=有 BLOCK / 2=词表缺失降级

★为什么必须有「2」
------------------
2 = **无法判定**，不是「干净」。本项目所有严重缺陷都是同一型态：
「跑起来不报错、但结论错」。发布库副本旁边没有敏感词表 → 真名/学号/事故词
三类**一条都没查**，此时若返回 0，调用方会把「没查」当成「查过且干净」。
所以本脚本聚合时，**DEGRADED 绝不等价于 PASS**：有 DEGRADED → 退出码 2。

退出码（本脚本）
----------------
    0  全绿：无 FAIL、无 DEGRADED（WARN/SKIP 不影响）
    1  有 FAIL（阻断项，必须处理）
    2  无 FAIL，但有 DEGRADED（有项**无法判定** → 结论不成立）
    3  用法错误（未知参数 / 缺参数值）

用法
----
    python scripts/selfcheck.py                    # 本地全量（不联网）
    python scripts/selfcheck.py --remote           # 追加 GitHub Release 回读核验
    python scripts/selfcheck.py --md report.md     # 同时落一份 Markdown 报告
    python scripts/selfcheck.py --json             # 机器读（stdout 出 JSON）
    python scripts/selfcheck.py --quiet            # 只出结论行
    python scripts/selfcheck.py --repo E:/path     # 指定被检仓库（默认=本脚本所在仓库）
    python scripts/selfcheck.py --update-baseline  # ★唯一会写文件的选项（见下）

★`--update-baseline` 是唯一写操作
---------------------------------
`.release-baseline.json` 是**发布侧人工产物的指纹基线**（README / SECURITY / LICENSE /
.gitignore 的 md5）。它的用意是：**改动它必须是有意的，并在 git diff 里可见** ——
防止自动化流程（格式化、同步、发布脚本）静默把人工写好的 README 推平。
所以本脚本默认**只比对、不更新**；确认改动是有意的之后，用 `--update-baseline`
刷新基线，并在 git diff 里 review 这次刷新。换行跟随原文件（不写死 \\n）。

★只读保证
---------
除 `--update-baseline` 外，本脚本不创建、不修改、不删除任何文件；
所有子进程都用 `CREATE_NO_WINDOW`（不弹黑窗、不抢屏幕）。
`--help` 无任何副作用。
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time

try:
    import getpass
except ImportError:                                     # pragma: no cover
    getpass = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ★Windows：子进程一律不弹窗。Git Bash / 计划任务里跑时黑窗会打断用户。
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

STATUSES = ('PASS', 'FAIL', 'WARN', 'SKIP', 'DEGRADED')
ICON = {'PASS': 'PASS', 'FAIL': 'FAIL', 'WARN': 'WARN',
        'SKIP': 'SKIP', 'DEGRADED': 'DEGR'}


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def _reconfigure_io():
    """中文输出在 Windows 重定向时默认走 GBK → 乱码/报错。强制 UTF-8。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except Exception:
            pass


def _run(cmd, cwd=None, env=None):
    """跑子进程 → (rc, stdout, stderr, 秒)。失败（命令不存在等）返回 rc=None。

    ★不用 shell=True：路径含空格/中文时 shell 引号规则与 Windows 不一致。
    ★capture_output + 固定 encoding：避免按系统默认编码解码子进程输出。
    """
    e = os.environ.copy()
    e['PYTHONIOENCODING'] = 'utf-8'      # 子脚本的中文回显也要能读回来
    if env:
        e.update(env)
    kw = dict(cwd=cwd, capture_output=True, text=True, encoding='utf-8',
              errors='replace', env=e)
    if _NO_WINDOW:
        kw['creationflags'] = _NO_WINDOW
    t0 = time.time()
    try:
        r = subprocess.run(cmd, **kw)
    except OSError as ex:
        return None, '', '%s: %s' % (type(ex).__name__, ex), time.time() - t0
    return r.returncode, r.stdout or '', r.stderr or '', time.time() - t0


def _res(cid, title, status, summary, evidence=None, seconds=None):
    assert status in STATUSES, status
    return {'id': cid, 'title': title, 'status': status, 'summary': summary,
            'evidence': list(evidence or []), 'seconds': seconds or 0.0}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()


def _read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    return raw.decode('utf-8', 'replace'), raw


def _git(ctx, key, args):
    """带缓存的 git 调用（同一份输出被多个检查项复用，避免重复起进程）。"""
    if key not in ctx['cache']:
        ctx['cache'][key] = _run(['git'] + list(args), cwd=ctx['repo'])
    return ctx['cache'][key]


def _basename(p):
    return p.replace('\\', '/').rsplit('/', 1)[-1]


def _vkey(v):
    """PEP 440 的极小可用子集 → 可比较元组（本项目只用 x.y.z + aN/bN/rcN）。

    ★为什么不用字符串比大小：'0.1.0a10' < '0.1.0a9' 是错的。
    """
    m = re.match(r'^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?$', v)
    if not m:
        return None
    base = [int(x) for x in m.group(1).split('.')]
    base += [0] * (4 - len(base))
    pre = {'a': 0, 'b': 1, 'rc': 2, None: 3}[m.group(2)]
    return (tuple(base), pre, int(m.group(3) or 0))


# ---------------------------------------------------------------------------
# C01 ~ C05 仓库状态类（自实现）
# ---------------------------------------------------------------------------
def c01_worktree_clean(ctx):
    """工作区必须干净 —— 有未提交改动时，「自检通过」不代表发布的内容通过。"""
    rc, out, err, _ = _git(ctx, 'status', ['status', '--porcelain'])
    if rc is None or rc != 0:
        return _res('C01', 'git 工作区干净', 'DEGRADED',
                    'git 不可用或该目录不是仓库', [err.strip()[:200]])
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return _res('C01', 'git 工作区干净', 'PASS', '无未提交改动')
    ev = lines[:10]
    if len(lines) > 10:
        ev.append('…另 %d 条' % (len(lines) - 10))
    ev.append('★未提交的改动不进发布集，但会让「自检通过」与「发布内容」脱钩。')
    return _res('C01', 'git 工作区干净', 'FAIL',
                '%d 处未提交改动' % len(lines), ev)


def c02_no_tracked_bak(ctx):
    """被 git 跟踪的 .bak* 快照 → 会被原样发布出去（实测踩过：.bak 里含本机路径）。"""
    rc, out, _, _ = _git(ctx, 'ls', ['ls-files'])
    if rc != 0:
        return _res('C02', '无被跟踪的 .bak 快照', 'DEGRADED', 'git ls-files 失败')
    baks = [l for l in out.splitlines() if '.bak' in _basename(l)]
    if not baks:
        return _res('C02', '无被跟踪的 .bak 快照', 'PASS', '0 个')
    return _res('C02', '无被跟踪的 .bak 快照', 'FAIL',
                '%d 个 .bak 已被跟踪（会被发布）' % len(baks), baks[:10])


def c03_bak_ignored(ctx):
    """`.gitignore` 必须含 `*.bak*`，且工作区里每个 .bak* 都真的被忽略。

    ★为什么两个判据都要：
      · 只查「有没有 .bak 文件」→ 文件一删就恒绿，规则坏了也看不出来；
      · 只查「规则在不在」→ 规则写了但被后面的 `!` 反选、或目录专属模式不生效，
        实测踩过：`*.bak` 匹配不到 `x.bak-20260917-163753`（带连字符的快照名）。
    """
    gi = os.path.join(ctx['repo'], '.gitignore')
    rule_ok = False
    if os.path.isfile(gi):
        text, _ = _read_text(gi)
        rule_ok = any(l.strip() in ('*.bak*', '*.bak') or
                      l.strip().startswith('*.bak')
                      for l in text.splitlines())
    rc_u, untr, _, _ = _git(ctx, 'untracked', ['ls-files', '--others',
                                               '--exclude-standard'])
    rc_i, ign, _, _ = _git(ctx, 'ignored', ['ls-files', '--others', '--ignored',
                                            '--exclude-standard'])
    untr_bak = [l for l in (untr or '').splitlines() if '.bak' in _basename(l)]
    ign_bak = [l for l in (ign or '').splitlines() if '.bak' in _basename(l)]
    ev = ['.gitignore 含 *.bak* 规则：%s' % ('是' if rule_ok else '★否'),
          '已被忽略的 .bak* 文件：%d 个' % len(ign_bak)]
    if rc_u != 0 or rc_i != 0:
        return _res('C03', '.bak 快照全部被忽略', 'DEGRADED', 'git 查询失败', ev)
    if untr_bak:
        return _res('C03', '.bak 快照全部被忽略', 'FAIL',
                    '%d 个 .bak* 未被忽略（会被发布）' % len(untr_bak),
                    ev + untr_bak[:10])
    if not rule_ok:
        return _res('C03', '.bak 快照全部被忽略', 'FAIL',
                    '.gitignore 缺少 *.bak* 规则（下次造快照就会漏进发布集）', ev)
    return _res('C03', '.bak 快照全部被忽略', 'PASS',
                '规则就位 · %d 个快照全部被忽略' % len(ign_bak), ev)


def c04_no_untracked(ctx):
    """未跟踪且未被忽略的文件 → 它**不进**发布集，但会被泄密扫描器扫到。

    ★实测踩过（scan_leaks.py 的 docstring 里记着）：新写的模块在 `git add`
      **之前**跑扫描 → 不在索引里 → 完全没被扫；紧接着 `git add -A` 就提交了。
      两边口径不一致 = 「跑起来不报错、但结论错」。
    """
    rc, out, err, _ = _git(ctx, 'untracked', ['ls-files', '--others',
                                              '--exclude-standard'])
    if rc != 0:
        return _res('C04', '无未跟踪文件（发布集不漏文件）', 'DEGRADED',
                    'git 查询失败', [err.strip()[:200]])
    files = [l for l in out.splitlines()
             if l.strip() and '.bak' not in _basename(l)]   # .bak 由 C03 负责
    if not files:
        return _res('C04', '无未跟踪文件（发布集不漏文件）', 'PASS', '0 个')
    return _res('C04', '无未跟踪文件（发布集不漏文件）', 'FAIL',
                '%d 个文件未跟踪（不在发布集里，但会被扫描器扫到 → 口径不一致）'
                % len(files), files[:10])


def c05_identity_neutral(ctx):
    """git 提交身份不得含本机用户名/主机名。

    ★为什么是硬闸门：git 默认身份会拼成 `用户名@主机名` 并**永久写进对象库**。
      本机主机名恰好在项目 BLOCK 档词表里 → 直接 commit 等于把 BLOCK 档内容
      写进 git 历史，而且**改文件消不掉**（只能重写历史）。

    ★判据是**运行时算出来的**（socket.gethostname / getpass.getuser），
      本文件里不出现任何本机标识的字面量 —— 否则扫描器要扫的这份自检脚本
      自己就成了泄露源（实测踩过：扫描器把真名写在代码里，自己贡献 3 处命中）。
    """
    host = (socket.gethostname() or '').strip()
    user = ''
    if getpass is not None:
        try:
            user = (getpass.getuser() or '').strip()
        except Exception:
            user = ''
    rc_n, name, _, _ = _git(ctx, 'cfgname', ['config', 'user.name'])
    rc_e, email, _, _ = _git(ctx, 'cfgemail', ['config', 'user.email'])
    name = (name or '').strip()
    email = (email or '').strip()
    if rc_n != 0 or rc_e != 0:
        return _res('C05', '提交身份已中性化', 'DEGRADED',
                    '读不到 git 身份配置', ['git config user.name/email 失败'])
    bad = []
    if not name:
        bad.append('user.name 未设置')
    if not email:
        bad.append('user.email 未设置')
    if user:
        if user.lower() in name.lower():
            bad.append('user.name 含本机用户名')
        if user.lower() in email.lower():
            bad.append('user.email 含本机用户名')
    if host:
        if host.lower() in name.lower():
            bad.append('user.name 含本机主机名')
        if host.lower() in email.lower():
            bad.append('user.email 含本机主机名')
    ev = ['user.name  = %s' % name, 'user.email = %s' % email,
          '（判据：与运行时的用户名/主机名比对，脚本内无字面量）']
    if bad:
        return _res('C05', '提交身份已中性化', 'FAIL',
                    '；'.join(bad) + ' → 提交会把本机标识写进 git 历史', ev)
    # 身份配置对了，但历史里最后一条可能是在中性化之前写的 → 单独提示，不当 FAIL
    rc_l, last, _, _ = _git(ctx, 'lastauth', ['log', '-1', '--format=%an <%ae>'])
    ev.append('最后一次提交：%s' % (last or '').strip())
    if rc_l == 0 and last and user and user.lower() in last.lower():
        return _res('C05', '提交身份已中性化', 'WARN',
                    '配置已中性化，但最后一次提交的作者仍含本机用户名', ev)
    if rc_l == 0 and last and host and host.lower() in last.lower():
        return _res('C05', '提交身份已中性化', 'WARN',
                    '配置已中性化，但最后一次提交的作者仍含本机主机名', ev)
    return _res('C05', '提交身份已中性化', 'PASS', '配置与最后一次提交均不含本机标识', ev)


# ---------------------------------------------------------------------------
# C06 人工产物指纹基线（自实现）
# ---------------------------------------------------------------------------
def _baseline_path(ctx):
    return os.path.join(ctx['repo'], '.release-baseline.json')


def c06_baseline(ctx):
    """人工产物指纹基线比对 —— 防自动化流程静默推平 README。

    ★设计意图：改动必须**是有意的**，并在 git diff 里可见。
      所以基线不一致**不是**"坏了"，而是"需要你确认"。
      确认后跑 `--update-baseline` 刷新，刷新动作本身也进 git diff。
    """
    bp = _baseline_path(ctx)
    if not os.path.isfile(bp):
        return _res('C06', '人工产物指纹基线', 'DEGRADED',
                    '找不到 .release-baseline.json → 无基线可比', [bp])
    try:
        base, _ = _read_text(bp)
        obj = json.loads(base)
    except (OSError, ValueError) as ex:
        return _res('C06', '人工产物指纹基线', 'DEGRADED',
                    '基线文件读不出/不是合法 JSON：%s' % ex, [bp])
    files = obj.get('files') or {}
    if not files:
        return _res('C06', '人工产物指纹基线', 'DEGRADED', '基线里没有 files 项', [bp])
    bad, missing, ev = [], [], []
    for rel in sorted(files):
        want = str(files[rel]).lower()
        p = os.path.join(ctx['repo'], rel)
        if not os.path.isfile(p):
            missing.append(rel)
            ev.append('%-14s ★文件不存在' % rel)
            continue
        got = _md5(p)
        if got == want:
            ev.append('%-14s 一致  %s' % (rel, got[:12]))
        else:
            bad.append(rel)
            ev.append('%-14s ★不一致  基线 %s → 实测 %s'
                      % (rel, want[:12], got[:12]))
    head_base = str(obj.get('head_at_baseline') or '')[:7]
    rc_h, head, _, _ = _git(ctx, 'head', ['rev-parse', 'HEAD'])
    ev.append('基线建立于 %s · 当前 HEAD %s'
              % (head_base or '(未记)', (head or '').strip()[:7]))
    if bad or missing:
        ev.append('★修法：确认改动是有意的 → python scripts/selfcheck.py --update-baseline')
        ev.append('  刷新动作会写进 .release-baseline.json，在 git diff 里可见。')
        return _res('C06', '人工产物指纹基线', 'FAIL',
                    '%d 个文件与基线不一致%s'
                    % (len(bad), '（%d 个缺失）' % len(missing) if missing else ''),
                    ev)
    return _res('C06', '人工产物指纹基线', 'PASS',
                '%d 个人工产物与基线逐字节一致' % len(files), ev)


def update_baseline(ctx, out=sys.stdout):
    """重算基线并原子写回。★唯一会写文件的动作。"""
    bp = _baseline_path(ctx)
    if not os.path.isfile(bp):
        out.write('★找不到 %s\n' % bp)
        return 2
    raw, rb = _read_text(bp)
    try:
        obj = json.loads(raw)
    except ValueError as ex:
        out.write('★基线不是合法 JSON：%s\n' % ex)
        return 2
    files = obj.get('files') or {}
    if not files:
        out.write('★基线里没有 files 项，拒绝猜。\n')
        return 2
    old = {k: str(files[k]).lower() for k in files}
    new = {}
    changed, absent = [], []
    for rel in sorted(old):
        p = os.path.join(ctx['repo'], rel)
        if not os.path.isfile(p):
            absent.append(rel)
            new[rel] = old[rel]          # 缺文件时保留旧值，不静默清空
            continue
        new[rel] = _md5(p)
        if new[rel] != old[rel]:
            changed.append(rel)
    rc, head, _, _ = _git(ctx, 'head', ['rev-parse', 'HEAD'])
    if rc != 0:
        out.write('★取不到 HEAD，拒绝更新（head_at_baseline 会写错）\n')
        return 2
    obj['files'] = new
    obj['head_at_baseline'] = (head or '').strip()
    if absent:
        out.write('★以下文件不存在，保留旧指纹（不静默清空）：%s\n' % ', '.join(absent))
    if not changed:
        out.write('基线无需更新：%d 个文件全部一致（head_at_baseline 已指向 %s）\n'
                  % (len(new), obj['head_at_baseline'][:7]))
    for rel in changed:
        out.write('  %-14s %s → %s\n' % (rel, old[rel][:12], new[rel][:12]))
    # 换行跟随原文件（★不写死 \n：本项目反复踩过换行写死导致整文件 diff）
    nl = '\r\n' if b'\r\n' in rb[:65536] else '\n'
    text = json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
    data = text.replace('\n', nl).encode('utf-8')
    d = os.path.dirname(bp) or '.'
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix='_selfcheck_base_', dir=d)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, bp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    out.write('已写回 %s（换行=%s · %d 字节 · head_at_baseline=%s）\n'
              % (os.path.basename(bp), 'CRLF' if nl == '\r\n' else 'LF',
                 len(data), obj['head_at_baseline'][:7]))
    out.write('★请在 git diff 里 review 这次刷新 —— 基线变更必须是有意的。\n')
    return 0


# ---------------------------------------------------------------------------
# C07 版本号三处一致（自实现）
# ---------------------------------------------------------------------------
def _version_pyproject(ctx):
    p = os.path.join(ctx['repo'], 'pyproject.toml')
    if not os.path.isfile(p):
        return None, 'pyproject.toml 不存在'
    text, _ = _read_text(p)
    try:
        import tomllib
        with open(p, 'rb') as f:
            return tomllib.load(f)['project']['version'], None
    except ImportError:
        pass
    except (KeyError, ValueError, OSError) as ex:
        return None, 'pyproject 解析失败：%s' % ex
    m = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', text)
    return (m.group(1), None) if m else (None, 'pyproject 里找不到 version')


def _version_facade(ctx):
    p = os.path.join(ctx['repo'], 'memtether.py')
    if not os.path.isfile(p):
        return None, 'memtether.py 不存在'
    text, _ = _read_text(p)
    m = re.search(r'(?m)^\s*__version__\s*=\s*["\']([^"\']+)["\']', text)
    return (m.group(1), None) if m else (None, 'memtether.py 里找不到 __version__')


def _version_changelog(ctx):
    p = os.path.join(ctx['repo'], 'CHANGELOG.md')
    if not os.path.isfile(p):
        return None, 'CHANGELOG.md 不存在'
    text, _ = _read_text(p)
    m = re.search(r'(?m)^##\s*\[([0-9][^\]]*)\]', text)
    return (m.group(1), None) if m else (None, 'CHANGELOG.md 里找不到 ## [x.y.z] 标题')


def c07_version_consistent(ctx):
    """版本号三处来源必须一致：pyproject / 门面 / CHANGELOG 最新标题。

    ★为什么单独立项：版本只在两处一致、第三处忘了改，是**静默**的 ——
      `pip install` 成功、`memtether --version` 也正常，只有 CHANGELOG 顶部
      写着上一版。手工发版时最容易漏的就是它。
    """
    src = [('pyproject.toml', _version_pyproject),
           ('memtether.py', _version_facade),
           ('CHANGELOG.md', _version_changelog)]
    got, ev, errs = {}, [], []
    for label, fn in src:
        v, err = fn(ctx)
        if err:
            errs.append('%s：%s' % (label, err))
            ev.append('%-14s ★%s' % (label, err))
        else:
            got[label] = v
            ev.append('%-14s %s' % (label, v))
    if errs:
        return _res('C07', '版本号三处一致', 'DEGRADED',
                    '读不全版本来源 → 无法判定', ev)
    uniq = sorted(set(got.values()))
    if len(uniq) == 1:
        return _res('C07', '版本号三处一致', 'PASS', uniq[0], ev)
    return _res('C07', '版本号三处一致', 'FAIL',
                '三处不一致：%s' % ' / '.join('%s=%s' % (k, v) for k, v in got.items()),
                ev)


# ---------------------------------------------------------------------------
# C08 dist/ 构建产物（自实现）
# ---------------------------------------------------------------------------
def _parse_artifacts(dist):
    """→ (whl_or_sdist 列表, 其它条目列表)。条目 = (文件名, 版本)。"""
    arts, other = [], []
    for name in sorted(os.listdir(dist)):
        p = os.path.join(dist, name)
        if not os.path.isfile(p):
            other.append(name + '/')                  # 目录（构建临时残留）
            continue
        m = re.match(r'^memtether-(.+?)-py3-none-any\.whl$', name)
        if m:
            arts.append((name, m.group(1)))
            continue
        m = re.match(r'^memtether-(.+)\.tar\.gz$', name)
        if m:
            arts.append((name, m.group(1)))
            continue
        other.append(name)
    return arts, other


def c08_dist(ctx):
    """dist/ 必须含当前版本的 wheel + sdist；不得有临时残留、不得有更高版本产物。

    ★实测踩过两条：
      · 构建中间目录 `dist/.tmp-i2kxh8qg` 混进 dist/ → 会被 `twine upload dist/*`
        一起传上去（后来改名归档处理）；
      · 版本号回退（改了 pyproject 又拿旧产物发）→ 附件与 tag 版本对不上。
    """
    dist = os.path.join(ctx['repo'], 'dist')
    if not os.path.isdir(dist):
        return _res('C08', 'dist/ 构建产物与版本一致', 'SKIP',
                    'dist/ 不存在（尚未构建）', [dist])
    ver, err = _version_pyproject(ctx)
    if not ver:
        return _res('C08', 'dist/ 构建产物与版本一致', 'DEGRADED',
                    '取不到当前版本 → 无法比对：%s' % err)
    arts, other = _parse_artifacts(dist)
    ev, fails = [], []
    cur = [a for a in arts if a[1] == ver]
    kinds = {('whl' if a[0].endswith('.whl') else 'sdist') for a in cur}
    for name, v in cur:
        p = os.path.join(dist, name)
        ev.append('%s  %d B  sha256=%s' % (name, os.path.getsize(p), _sha256(p)[:16]))
    ev.append('当前版本 %s：找到 %d 个产物（%s）'
              % (ver, len(cur), ' + '.join(sorted(kinds)) or '无'))
    if not cur:
        fails.append('没有当前版本 %s 的产物' % ver)
    elif kinds != {'whl', 'sdist'}:
        fails.append('当前版本缺 %s' % ('wheel' if 'whl' not in kinds else 'sdist'))
    curk = _vkey(ver)
    for name, v in arts:
        k = _vkey(v)
        if curk and k and k > curk:
            fails.append('产物版本 %s 高于当前 %s：%s' % (v, ver, name))
    if other:
        fails.append('%d 个非产物条目（构建临时残留会被一起上传）：%s'
                     % (len(other), ', '.join(other[:5])))
        ev.append('★非产物条目：%s' % ', '.join(other[:8]))
    if fails:
        return _res('C08', 'dist/ 构建产物与版本一致', 'FAIL', '；'.join(fails), ev)
    return _res('C08', 'dist/ 构建产物与版本一致', 'PASS',
                '当前版本 wheel + sdist 齐备（另含 %d 个历史版本）'
                % len({a[1] for a in arts} - {ver}), ev)


# ---------------------------------------------------------------------------
# C09 ~ C11 编排已有闸门
# ---------------------------------------------------------------------------
def _orchestrate(ctx, cid, title, script, extra_env=None, extra_args=None,
                 expect=None, tail=True):
    """跑 scripts/<script> → 按 expect 映射退出码到状态。

    ★降级码必须映射到 DEGRADED，**不能**映射到 PASS —— 否则「没查」被当成「干净」。
    ★tail=False：调用方会自己把子进程输出解析成人读摘要时用 ——
      否则原始 JSON 片段会和摘要**重复出现**，报告越读越糊。
    ★返回的结果里带 raw = 子进程 stdout 原文：调用方要结构化解析时直接取，
      **不要再跑一遍子闸门**（实测重跑一次多花 2~4 秒，还多一份状态漂移风险）。
    """
    path = os.path.join(HERE, script)
    if not os.path.isfile(path):
        r = _res(cid, title, 'DEGRADED', '子闸门不存在：%s' % script, [path])
        r['raw'] = ''
        return r
    cmd = [sys.executable, path] + list(extra_args or [])
    rc, out, err, sec = _run(cmd, cwd=ctx['repo'], env=extra_env)
    if rc is None:
        r = _res(cid, title, 'DEGRADED', '起不了子进程',
                 [err.strip()[:200]], sec)
        r['raw'] = ''
        return r
    # ★变量名不能叫 tail：会把同名参数遮蔽掉 → `if tail:` 恒假 → 证据永远为空
    lines = []
    if tail:
        lines = [l[:160] for l in (out or '').splitlines() if l.strip()][-6:]
    if err.strip():
        lines.append('stderr: ' + err.strip().splitlines()[-1][:160])
    mapping = dict(expect or {})
    status = mapping.get(rc)
    if status is None:
        r = _res(cid, title, 'DEGRADED',
                 '%s 退出码 %s（未在契约内）' % (script, rc), lines, sec)
    elif status == 'PASS':
        r = _res(cid, title, 'PASS', '退出码 0', lines, sec)
    elif status == 'FAIL':
        r = _res(cid, title, 'FAIL', '退出码 %s' % rc, lines, sec)
    else:
        r = _res(cid, title, 'DEGRADED', '退出码 %s → 无法判定' % rc, lines, sec)
    r['raw'] = out or ''
    return r


def c09_packaging(ctx):
    return _orchestrate(ctx, 'C09', '打包清单与仓库一致（check_packaging）',
                        'check_packaging.py', expect={0: 'PASS', 1: 'FAIL',
                                                      2: 'DEGRADED'})


def _resolve_terms(ctx):
    """找敏感词表。★词表外置是刻意的：扫描器本身要开源，把真名写死在代码里
    = 用泄密清单去泄密（实测它自己贡献了 3 处 BLOCK 命中）。"""
    cands = []
    env = os.environ.get('MEM_SCAN_TERMS')
    if env:
        cands.append(env)
    cands.append(os.path.join(ctx['repo'], 'scripts', 'leak_terms.local.json'))
    cands.append(os.path.join(ctx['repo'], 'leak_terms.local.json'))
    # 兄弟仓库（真源在别处时：发布库副本旁边没有词表 → 需指回真源那份）
    parent = os.path.dirname(os.path.abspath(ctx['repo']))
    try:
        for name in sorted(os.listdir(parent)):
            cands.append(os.path.join(parent, name, 'scripts',
                                      'leak_terms.local.json'))
    except OSError:
        pass
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def _loc_label(f, key_file):
    """给一处命中挑个「人看得懂的位置名」。

    ★历史扫描给的是 blob sha（形如 2923d98d8a38），光看哈希认不出是哪个文件
      —— 那种证据等于没给。优先用 paths 里的文件名，退化顺序：
      key_file 字段 → paths[0] 的 basename（多路径时带 +N） → blob sha。
    """
    v = f.get(key_file)
    if key_file != 'blob' and isinstance(v, str) and v:
        return v
    paths = f.get('paths')
    if isinstance(paths, list) and paths:
        base = os.path.basename(str(paths[0]).replace('\\', '/'))
        if len(paths) > 1:
            base += ' +%d' % (len(paths) - 1)
        return base
    if isinstance(v, str) and v:
        return v
    return '?'


def _leak_evidence(out, key_level, key_cat, key_file):
    """把子扫描器的 --json 结果压成几行人读证据。"""
    ev = []
    try:
        fs = json.loads(out)
    except ValueError:
        return ['（--json 输出无法解析，见上方原文）']
    if not isinstance(fs, list):
        return ['（--json 输出不是列表）']
    blocks = [f for f in fs if str(f.get(key_level, '')).lower() == 'block']
    warns = [f for f in fs if f not in blocks]
    ev.append('命中 %d 处：BLOCK %d · WARN %d' % (len(fs), len(blocks), len(warns)))
    for f in (blocks + warns)[:6]:
        ev.append('  [%s] %s  %s:%s'
                  % (str(f.get(key_level, '')).upper(), f.get(key_cat, '?'),
                     _loc_label(f, key_file), f.get('line', '?')))
    if len(fs) > 6:
        ev.append('  …另 %d 处（跑子闸门看全文）' % (len(fs) - 6))
    return ev


def c10_leaks(ctx):
    """引擎侧泄密扫描（当前发布集）。"""
    terms = _resolve_terms(ctx)
    env = {}
    if terms:
        env['MEM_SCAN_TERMS'] = terms
    r = _orchestrate(ctx, 'C10', '引擎侧泄密扫描（scan_leaks）', 'scan_leaks.py',
                     extra_env=env, extra_args=['--json'],
                     expect={0: 'PASS', 1: 'FAIL', 2: 'DEGRADED'},
                     tail=False)          # ★原始 JSON 由 _leak_evidence 解析，别再抄一份
    if terms:
        r['evidence'].insert(0, '词表：%s' % terms)
    else:
        r['evidence'].insert(0, '★未找到敏感词表 → 真名/学号/事故词三类未参与扫描')
    if r['status'] in ('PASS', 'FAIL'):
        r['evidence'] += _leak_evidence(r.get('raw', ''), 'level', 'cat', 'file')
    return r


def c11_history(ctx):
    """git 历史泄密扫描（scan_leaks 的盲区：曾跟踪过、后来删掉的文件）。"""
    terms = _resolve_terms(ctx)
    env = {}
    if terms:
        env['MEM_SCAN_TERMS'] = terms
    r = _orchestrate(ctx, 'C11', 'git 历史泄密扫描（scan_history_leaks）',
                     'scan_history_leaks.py', extra_env=env, extra_args=['--json'],
                     expect={0: 'PASS', 1: 'FAIL', 2: 'DEGRADED'},
                     tail=False)          # ★同 C10：原文自己解析
    if not terms:
        r['evidence'].insert(0, '★未找到敏感词表 → 历史里的真名/学号/事故词未参与扫描')
    if r['status'] in ('PASS', 'FAIL'):
        r['evidence'] += _leak_evidence(r.get('raw', ''), 'severity', 'rule', 'blob')
        # ★历史里的命中改文件消不掉 → 提示必须与「当前文件」区分开
        if r['status'] == 'FAIL':
            r['evidence'].append('★历史命中无法靠改文件消除，只能重写历史（本项目不自动做）。')
    return r


# ---------------------------------------------------------------------------
# C12 远端回读（可选 · 匿名只读 API）
# ---------------------------------------------------------------------------
def _remote_slug(ctx):
    rc, out, _, _ = _git(ctx, 'remote', ['remote', 'get-url', 'origin'])
    if rc != 0 or not out.strip():
        return None
    m = re.search(r'github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?\s*$', out.strip())
    return m.group(1) if m else None


def _api_get(url):
    """匿名读 GitHub REST。

    ★两个必带头：
      · Accept: application/vnd.github+json
      · User-Agent: <非空>  —— **缺 User-Agent 直接返回 403**，而不是 401。
        这个 403 极易被误读成「仓库不存在 / 是私有库」，从而得出完全错误的结论。
    """
    import urllib.request
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'memtether-selfcheck',
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def c12_remote(ctx):
    """GitHub Release 回读核验：tag / draft / prerelease / 附件 digest。

    ★为什么必须回读：上传成功 ≠ 传对了。附件 digest 与本地 sha256 一致
      才是「发布终点」。本项**只读**，不需要任何 token。
    """
    slug = _remote_slug(ctx)
    if not slug:
        return _res('C12', 'GitHub Release 回读核验', 'SKIP',
                    'origin 不是 GitHub 仓库（无 remote 或走别处）')
    ver, err = _version_pyproject(ctx)
    if not ver:
        return _res('C12', 'GitHub Release 回读核验', 'DEGRADED',
                    '取不到当前版本：%s' % err)
    tag = 'v' + ver
    try:
        rels = _api_get('https://api.github.com/repos/%s/releases?per_page=30' % slug)
    except Exception as ex:
        return _res('C12', 'GitHub Release 回读核验', 'DEGRADED',
                    '匿名读 API 失败（网络/限流）：%s: %s' % (type(ex).__name__, ex),
                    ['slug=%s' % slug])
    if not isinstance(rels, list):
        return _res('C12', 'GitHub Release 回读核验', 'DEGRADED',
                    'API 返回不是列表', ['slug=%s' % slug])
    ev = ['仓库 %s · 远端 release 共 %d 个' % (slug, len(rels))]
    hit = None
    for r in rels:
        if r.get('tag_name') == tag:
            hit = r
            break
    if hit is None:
        return _res('C12', 'GitHub Release 回读核验', 'WARN',
                    '远端还没有 %s 这个 release' % tag,
                    ev + ['已有 tag：%s' % ', '.join(sorted(
                        str(x.get('tag_name')) for x in rels)[:8])])
    ev += ['tag=%s · draft=%s · prerelease=%s · published_at=%s'
           % (hit.get('tag_name'), hit.get('draft'), hit.get('prerelease'),
              hit.get('published_at')),
           'html_url=%s' % hit.get('html_url')]
    body = hit.get('body') or ''
    ev.append('正文 %d 字符' % len(body))
    # 附件 digest 与本地逐字节比对
    assets = {a.get('name'): a for a in (hit.get('assets') or [])}
    dist = os.path.join(ctx['repo'], 'dist')
    local = []
    if os.path.isdir(dist):
        for name in sorted(os.listdir(dist)):
            p = os.path.join(dist, name)
            if os.path.isfile(p) and ver in name and \
                    (name.endswith('.whl') or name.endswith('.tar.gz')):
                local.append((name, _sha256(p), os.path.getsize(p)))
    bad = []
    for name, sha, size in local:
        a = assets.get(name)
        if not a:
            bad.append('远端缺附件 %s' % name)
            ev.append('%-34s ★远端缺失' % name)
            continue
        dg = str(a.get('digest') or '')
        ok = dg.lower() == ('sha256:' + sha).lower()
        ev.append('%-34s %s  digest=%s  本地=%s'
                  % (name, '一致' if ok else '★不一致', dg[:19], ('sha256:' + sha)[:19]))
        if not ok:
            bad.append('%s digest 不一致' % name)
        if a.get('size') and int(a['size']) != size:
            bad.append('%s 体积不一致（远端 %s / 本地 %s）' % (name, a['size'], size))
    if not local:
        ev.append('（本地 dist/ 没有当前版本的产物可比对）')
    if hit.get('draft') is True:
        bad.append('release 仍是 draft（未发布）')
    if bad:
        return _res('C12', 'GitHub Release 回读核验', 'FAIL', '；'.join(bad), ev)
    return _res('C12', 'GitHub Release 回读核验', 'PASS',
                '%s 已发布 · %d 个附件 digest 与本地逐字节一致' % (tag, len(local)), ev)


# ---------------------------------------------------------------------------
# 编排 / 渲染
# ---------------------------------------------------------------------------
def _checks(with_remote):
    cs = [
        ('C01', 'git 工作区干净', c01_worktree_clean),
        ('C02', '无被跟踪的 .bak 快照', c02_no_tracked_bak),
        ('C03', '.bak 快照全部被忽略', c03_bak_ignored),
        ('C04', '无未跟踪文件（发布集不漏文件）', c04_no_untracked),
        ('C05', '提交身份已中性化', c05_identity_neutral),
        ('C06', '人工产物指纹基线', c06_baseline),
        ('C07', '版本号三处一致', c07_version_consistent),
        ('C08', 'dist/ 构建产物与版本一致', c08_dist),
        ('C09', '打包清单与仓库一致', c09_packaging),
        ('C10', '引擎侧泄密扫描', c10_leaks),
        ('C11', 'git 历史泄密扫描', c11_history),
    ]
    if with_remote:
        cs.append(('C12', 'GitHub Release 回读核验', c12_remote))
    return cs


def run_all(ctx):
    results = []
    for cid, title, fn in _checks(ctx['opts']['remote']):
        t0 = time.time()
        try:
            r = fn(ctx)
            r.setdefault('id', cid)
            r.setdefault('title', title)
            if not r.get('seconds'):
                r['seconds'] = time.time() - t0
        except Exception as ex:                    # 检查自身异常 → 无法判定，不能算过
            r = _res(cid, title, 'DEGRADED',
                     '检查项自身异常：%s: %s' % (type(ex).__name__, ex),
                     seconds=time.time() - t0)
        results.append(r)
    return results


def exit_code(results):
    st = {r['status'] for r in results}
    if 'FAIL' in st:
        return 1
    if 'DEGRADED' in st:
        return 2
    return 0


def _counts(results):
    return {s: sum(1 for r in results if r['status'] == s) for s in STATUSES}


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _verdict(code):
    return {0: '全绿（未发现阻断项，可发布）',
            1: '有 FAIL —— 阻断项必须处理',
            2: '无 FAIL，但有 DEGRADED —— 存在**无法判定**的项，结论不成立',
            3: '用法错误'}.get(code, '未知')


def render_text(results, ctx, out, quiet=False):
    c = _counts(results)
    head = (ctx.get('head') or '?')[:7]
    if not quiet:
        out.write('=' * 88 + '\n')
        out.write('MemTether 发布前自检 · %s\n' % ctx['repo'])
        out.write('  HEAD %s · %s · 共 %d 项%s\n'
                  % (head, _now(), len(results),
                     '（含远端回读）' if ctx['opts']['remote'] else ''))
        out.write('=' * 88 + '\n')
        for r in results:
            out.write('[%s] %s %s\n' % (ICON[r['status']], r['id'], r['title']))
            out.write('       %s\n' % r['summary'])
            for e in r['evidence']:
                out.write('       %s\n' % e)
        out.write('-' * 88 + '\n')
    code = exit_code(results)
    out.write('结果：%d 项 · PASS %d · WARN %d · SKIP %d · FAIL %d · DEGRADED %d\n'
              % (len(results), c['PASS'], c['WARN'], c['SKIP'], c['FAIL'],
                 c['DEGRADED']))
    out.write('退出码 %d —— %s\n' % (code, _verdict(code)))
    if c['DEGRADED']:
        out.write('★DEGRADED ≠ PASS：这些项「没查/无法判定」，不是「查过且干净」。\n')
    return code


def render_md(results, ctx):
    c = _counts(results)
    code = exit_code(results)
    L = ['# MemTether 发布前自检报告', '',
         '- 仓库：`%s`' % ctx['repo'],
         '- HEAD：`%s`' % (ctx.get('head') or '?')[:12],
         '- 时间：%s' % _now(),
         '- 退出码：**%d** —— %s' % (code, _verdict(code)),
         '- 计数：PASS %d · WARN %d · SKIP %d · FAIL %d · DEGRADED %d'
         % (c['PASS'], c['WARN'], c['SKIP'], c['FAIL'], c['DEGRADED']), '',
         '| 项 | 状态 | 结论 |', '|---|---|---|']
    for r in results:
        L.append('| %s %s | %s | %s |'
                 % (r['id'], r['title'], r['status'],
                    r['summary'].replace('|', '\\|')))
    L += ['', '## 逐项证据', '']
    for r in results:
        L.append('### %s %s —— %s' % (r['id'], r['title'], r['status']))
        L.append('')
        L.append('%s' % r['summary'])
        if r['evidence']:
            L.append('')
            L.append('```')
            L += list(r['evidence'])
            L.append('```')
        L.append('')
    if c['DEGRADED']:
        L += ['> ★DEGRADED ≠ PASS：这些项「没查/无法判定」，不是「查过且干净」。', '']
    return '\n'.join(L) + '\n'


def _write_following_newline(path, text):
    nl = '\n'
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if b'\r\n' in f.read(65536):
                nl = '\r\n'
    data = text.replace('\n', nl).encode('utf-8')
    d = os.path.dirname(os.path.abspath(path)) or '.'
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix='_selfcheck_out_', dir=d)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data), ('CRLF' if nl == '\r\n' else 'LF')


USAGE = """用法：python scripts/selfcheck.py [选项]

选项：
  -h, --help            显示本帮助并退出（无任何副作用）
  --remote              追加 C12：GitHub Release 回读核验（匿名只读 API）
  --md PATH             把 Markdown 报告写到 PATH（换行跟随已存在的文件）
  --json                以 JSON 输出到 stdout（替代人读报告）
  --quiet               只输出结论行
  --repo PATH           指定被检仓库（默认 = 本脚本所在仓库）
  --update-baseline     ★唯一会写文件的选项：刷新 .release-baseline.json

退出码：
  0 全绿 / 1 有 FAIL / 2 无 FAIL 但有 DEGRADED / 3 用法错误

★DEGRADED ≠ PASS：它表示该项「无法判定」（例如敏感词表缺失 → 真名/学号/事故词
  三类一条都没查）。返回 0 会让调用方把「没查」当成「查过且干净」。
"""


def parse_args(argv):
    o = {'remote': False, 'json': False, 'md': None, 'quiet': False,
         'repo': None, 'update_baseline': False, 'help': False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('-h', '--help'):
            o['help'] = True
        elif a == '--remote':
            o['remote'] = True
        elif a == '--json':
            o['json'] = True
        elif a == '--quiet':
            o['quiet'] = True
        elif a == '--update-baseline':
            o['update_baseline'] = True
        elif a in ('--md', '--repo'):
            i += 1
            if i >= len(argv):
                return None, '%s 需要一个参数值' % a
            o['md' if a == '--md' else 'repo'] = argv[i]
        else:
            return None, '未知参数：%s' % a
        i += 1
    return o, None


def main(argv=None):
    _reconfigure_io()
    argv = sys.argv[1:] if argv is None else list(argv)
    opts, err = parse_args(argv)
    if err:
        sys.stderr.write('★%s\n\n%s' % (err, USAGE))
        return 3
    if opts['help']:
        sys.stdout.write(USAGE)
        return 0

    repo = os.path.abspath(opts['repo'] or ROOT)
    if not os.path.isdir(repo):
        sys.stderr.write('★仓库目录不存在：%s\n' % repo)
        return 3
    ctx = {'repo': repo, 'opts': opts, 'cache': {}}
    rc, head, _, _ = _git(ctx, 'head', ['rev-parse', 'HEAD'])
    ctx['head'] = (head or '').strip()

    if opts['update_baseline']:
        return update_baseline(ctx)

    results = run_all(ctx)

    if opts['json']:
        sys.stdout.write(json.dumps(
            {'repo': repo, 'head': ctx['head'], 'time': _now(),
             'exit_code': exit_code(results), 'counts': _counts(results),
             'checks': results}, ensure_ascii=False, indent=2) + '\n')
    else:
        render_text(results, ctx, sys.stdout, quiet=opts['quiet'])

    if opts['md']:
        n, nl = _write_following_newline(opts['md'], render_md(results, ctx))
        if not opts['json']:
            sys.stdout.write('Markdown 报告已写入 %s（%d 字节 · 换行=%s）\n'
                             % (opts['md'], n, nl))
    return exit_code(results)


if __name__ == '__main__':
    sys.exit(main())
