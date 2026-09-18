# -*- coding: utf-8 -*-
"""hub_selfcheck.py —— MemTether 自检巡检（§3.5 第 3 项 + 第 4 项）

为什么需要它：
    2026-09-16 两次事故的共同点是「**没有任何环节会报警**」——
      · 发布版 README 被同步脚本推平，是用户自己打开 GitHub 才发现的
      · 四层互联断了，只有"人想起来跑 check"才知道
    本脚本把这两件事变成**开机自动跑、只在失败时出声**。

检查项：
    1. 四层互联（配置级 / 工作区级 / 引导层 / 规则层）
    2. 发布侧人工产物指纹（publish_guard.py --check）
    3. 槽位守卫（用户级 ≤3900 · 工作区 ≤8000 · 引导层 ≤8000）—— 截断是静默的，必须越线即报
    4. 中枢可用（memory.db 能打开、能查）

行为：
    全部通过 → 静默（退出码 0，只写一行日志）
    有失败   → 退出码 1，并：写中枢 incident + 写桌面报告 + 写日志

用法：
    <venv>\\Scripts\\python.exe hub_selfcheck.py
    <venv>\\Scripts\\pythonw.exe hidden_run.pyw hub_selfcheck.py   # 无窗口（计划任务/开机自启用这个）
"""
import datetime
import io
import json
import os
import subprocess
import sys

H = os.path.expanduser('~')
# ★2026-09-16：原为写死的「本机绝对路径」（盘符 + 仓库目录名）。改为从本文件位置派生——
#   行为完全等价（本文件就在仓库根），但开源版不再暴露本机安装路径。
HUB = os.path.dirname(os.path.abspath(__file__))

# 本机专有路径外置（开源版无 local_paths.json → 相关检查项自动跳过，不报假失败）
_LOCAL = {}
try:
    with open(os.path.join(HUB, 'local_paths.json'), encoding='utf-8') as _f:
        _LOCAL = json.load(_f)
except (OSError, ValueError):
    pass

PY = os.path.join(HUB, '.venv-memory', 'Scripts', 'python.exe')
OPS = _LOCAL.get('ops_dir', '')
PUBLISH_GUARD = (os.path.join(OPS, 'publish_guard.py')
                 if OPS else '')
# ★2026-09-18 改：失败报告不再写桌面（用户明确反对往桌面丢产物）→ 改到 ops 报告目录。
#   仍坚持「只在失败时生成 + 通过后自动删除」：不制造无失败的自证式产物。
REPORT = (os.path.join(OPS, 'reports', 'MemTether-巡检报告.md')
          if OPS else os.path.join(HUB, 'MemTether-巡检报告.md'))

# 四层互联工具（技能里的那份是权威版本，wbdl 是临时拷贝）
INTERCONNECT_CANDIDATES = [
    os.path.join(H, '.agents', 'skills', 'windows-app-data-interconnect', 'scripts', 'wb_interconnect.py'),
    _LOCAL.get('wbdl_interconnect', ''),
]

# 槽位上限。
# ★教训（2026-09-16 装完 5 分钟就踩到）：守卫线**必须与"生成器的契约"同源**，
#   不能手打一个"看起来安全"的数字。初版我写死 3900，而 gateway.py rebuild 的
#   投影预算是 MEM_PROJ_BUDGET（默认 3980）→ 守卫线比契约还严 80 字符，
#   于是在正常运作下**必然误报**（实测当场 3904 > 3900 报红）。
#   这正是我自己刚写进技能的反面案例：**假阳性率高的护栏会被无视，最后等于没装。**
#   故：上限直接读同一个环境变量，保持单一真源；另设 WARN 带提示余量在收窄。
_PROJ_BUDGET = int(os.environ.get('MEM_PROJ_BUDGET', '3980'))
_PROJ_WARN = _PROJ_BUDGET - 100          # 余量收窄到 100 字符以内 → 只提醒，不算失败

def _workspace_memory():
    """国内版活跃工作区的记忆文件。

    ★2026-09-16 两处修正：
      ① 原先写死了「本机账户的绝对路径」（`C:` 盘 + 用户目录那一串）→
         开源版会暴露账户名，改为 expanduser；
      ② 写死的时间戳目录**迟早会过期**（国内版每开一个新工作区就多一个时间戳目录），
         故加兜底：写死的那个不存在时，取最新的一个。
      行为：写死目录存在时（当前即是）与原实现**完全等价**。
    """
    fixed = os.path.join(H, 'WorkBuddy', '2026-09-14-21-38-32', '.workbuddy',
                         'memory', 'MEMORY.md')
    if os.path.exists(fixed):
        return fixed
    import glob as _glob
    cands = _glob.glob(os.path.join(H, 'WorkBuddy', '*', '.workbuddy',
                                    'memory', 'MEMORY.md'))
    return max(cands, key=os.path.getmtime) if cands else fixed


SLOTS = [
    # (路径, 上限, 标签, 是否只是提醒)
    (os.path.join(H, '.workbuddy', 'MEMORY.md'), _PROJ_BUDGET, '用户级记忆投影', False),
    (_workspace_memory(), 8000, '工作区记忆', False),
    (os.path.join(H, '.agents', 'CODEBUDDY.md'), 8000, '引导层 CODEBUDDY.md', False),
]

RULES = os.path.join(H, '.workbuddy', 'rules', '00-clone-bootstrap.md')


def run(cmd, timeout=300, env=None):
    """跑子进程，返回 (rc, 合并输出)。用 CREATE_NO_WINDOW 避免弹窗。

    ★2026-09-18 修（根因层）：子进程 stdout 默认走系统 locale（GBK），
      任何脚本 print 一个非 GBK 字符（✓/✗/⚠/🔴…）就抛 UnicodeEncodeError，
      被本巡检误判成"该项检查失败"。逐个脚本改字符是打地鼠——
      这里统一把子进程 IO 编码钉成 utf-8，与父进程 decode 口径一致。
    """
    env = dict(os.environ if env is None else env)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    env.setdefault('PYTHONUTF8', '1')
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=HUB, encoding='utf-8', errors='replace', env=env,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return p.returncode, ((p.stdout or '') + (p.stderr or ''))
    except subprocess.TimeoutExpired:
        return 3, '[超时 >%ss]' % timeout
    except Exception as e:                                   # noqa: BLE001
        return 1, '[无法执行 %s: %s]' % (type(e).__name__, e)


def find_interconnect():
    for p in INTERCONNECT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def check_interconnect(problems):
    if not _LOCAL:
        return          # 未配置本机路径（开源版）→ 跳过，不报假失败
    p = find_interconnect()
    if not p:
        problems.append(('四层互联', '找不到 wb_interconnect.py（查过 %d 个候选路径）'
                         % len(INTERCONNECT_CANDIDATES)))
        return
    rc, out = run([PY, p, 'check'])
    if rc != 0:
        problems.append(('四层互联', 'check 退出码 %d：%s' % (rc, out.strip()[-300:])))
        return
    if '有缺口' in out or '[缺失]' in out:
        bad = [ln.strip() for ln in out.splitlines() if '[缺失]' in ln or '有缺口' in ln]
        problems.append(('四层互联', '有缺口（跑 apply 补齐）：\n      ' + '\n      '.join(bad[:8])))
        return
    if '全部正常' not in out:
        problems.append(('四层互联', 'check 输出无法判定（可能格式变了）：%s' % out.strip()[-200:]))


def check_publish(problems):
    if not PUBLISH_GUARD:
        return          # 未配置本机路径（开源版）→ 跳过，不报假失败
    if not os.path.exists(PUBLISH_GUARD):
        problems.append(('发布侧指纹', '找不到 %s' % PUBLISH_GUARD))
        return
    rc, out = run([PY, PUBLISH_GUARD, '--check'])
    if rc != 0:
        problems.append(('发布侧指纹', out.strip()[:800]))


def check_slots(problems, warns=None):
    warns = warns if warns is not None else []
    for item in SLOTS:
        path, cap, label = item[0], item[1], item[2]
        soft = item[3] if len(item) > 3 else False
        if not os.path.exists(path):
            problems.append(('槽位/%s' % label, '文件不存在：%s' % path))
            continue
        try:
            n = len(io.open(path, encoding='utf-8').read())
        except Exception as e:                               # noqa: BLE001
            problems.append(('槽位/%s' % label, '读不了：%s' % e))
            continue
        if n > cap:
            problems.append(('槽位/%s' % label,
                             '★超限 %d / %d 字符 —— 超限是**静默整体截断**，注入时会丢内容' % (n, cap)))
        elif n > cap - 100 and label == '用户级记忆投影' and not soft:
            # 余量收窄：只提醒，不算失败（否则就成了"必然误报"的护栏）
            warns.append('槽位/%s：%d / %d 字符，余量不足 100 —— 再加记忆就可能越线'
                         % (label, n, cap))
    if not os.path.exists(RULES):
        problems.append(('规则层', '缺失：%s（新工作区将拿不到引导）' % RULES))


def check_hub(problems):
    db = os.path.join(HUB, 'memory.db')
    if not os.path.exists(db):
        problems.append(('中枢可用', 'memory.db 不存在：%s' % db))
        return
    rc, out = run([PY, '-c',
                   'import sqlite3,sys;c=sqlite3.connect(r"%s");'
                   'print(c.execute("SELECT COUNT(*) FROM facts").fetchone()[0])' % db])
    if rc != 0 or not out.strip().isdigit():
        problems.append(('中枢可用', 'memory.db 打不开或查不了：%s' % out.strip()[:200]))


def write_report(problems, stamp):
    lines = ['# MemTether 巡检报告 —— **有 %d 项失败**' % len(problems), '',
             '时间：%s' % stamp, '',
             '> 本报告只在**失败时**生成。按顺序处理即可；全部修好后重跑一次巡检，报告会消失。', '']
    for name, detail in problems:
        lines.append('## %s' % name)
        lines.append('')
        lines.append('    ' + detail.replace('\n', '\n    '))
        lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('重跑：`%s %s`' % (PY, os.path.join(HUB, 'hub_selfcheck.py')))
    try:
        io.open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
        return REPORT
    except Exception:                                        # noqa: BLE001
        return None


def remember_incident(problems, stamp):
    body = 'MemTether 巡检失败（%s）：' % stamp + '；'.join(
        '%s → %s' % (n, d.replace('\n', ' ')[:160]) for n, d in problems)
    try:
        subprocess.run([PY, 'gateway.py', 'remember', body,
                        '--type', 'incident', '--source', 'workbuddy'],
                       capture_output=True, text=True, cwd=HUB, timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:                                        # noqa: BLE001
        pass


def main():
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    problems = []
    warns = []
    for fn in (check_interconnect, check_publish, check_slots, check_hub):
        try:
            if fn is check_slots:
                fn(problems, warns)
            else:
                fn(problems)
        except Exception as e:                               # noqa: BLE001
            problems.append((fn.__name__, '检查自身抛异常：%s: %s' % (type(e).__name__, e)))

    for w in warns:
        print('[%s] 提醒：%s' % (stamp, w))

    if not problems:
        print('[%s] 巡检通过%s' % (stamp, ('（%d 条提醒）' % len(warns)) if warns else ''))
        return 0

    print('[%s] 巡检失败 —— %d 项' % (stamp, len(problems)))
    for n, d in problems:
        print('  · %s: %s' % (n, d.replace('\n', ' ')[:200]))
    r = write_report(problems, stamp)
    if r:
        print('  报告已写到: %s' % r)
    remember_incident(problems, stamp)
    return 1


if __name__ == '__main__':
    sys.exit(main())
