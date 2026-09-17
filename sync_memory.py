# -*- coding: utf-8 -*-
"""
sync_memory.py — 核心记忆同步（astra 版，2026-09-17 接入 hubguard 守卫）

作用：把 memory_hub 的"永久事实/关键结论"提炼成精简投影，**全量覆盖**同步到
      ~/.workbuddy/MEMORY.md，使 WorkBuddy 侧记忆成为中枢的"启动适配层"，
      不再是独立记忆库（唯一真源 = memory_hub/sink.json）。

与旧版区别：
  - 旧版：追加标记，导致 MEMORY.md 内容重复、拼接错乱。
  - 新版：全量覆盖（原子写），每次生成一份干净、结构固定的 MEMORY.md。

★★ 2026-09-17：为什么必须接 hubguard（本轮修复的真问题）
  `~/.workbuddy/MEMORY.md` 是**跨客户端共享文件** —— 国内版读它，国际版
  `~/.workbuddy-ai/MEMORY.md` 是指向它的符号链接，`gateway.py rebuild` 也在写它。
  而本脚本原来是**裸 `os.replace`**，同时缺四道闸门，每一条都能造成静默事故：

  ① **无并发守卫** → 我读完、正要写之间，另一客户端（或 rebuild）改了它，
     我这一 `os.replace` 把对方的新内容**整篇抹掉**且**双方都不报错**。
     —— 这就是"同一 inode 整体重写互覆"，本脚本是仓库里最后一处没接守卫的路径。
  ② **换行写死** → `open(tmp,'w')` 走 `os.linesep`（Windows = CRLF），
     靠"当前投影恰好是 CRLF"才没出事；目标一旦是 LF，内容不变、字节全变。
  ③ **无预算闸门** → 官方注入口约 4000 字符，超出是**整篇截断**。
     生成器必须自守，不能指望下游。
  ④ **无骤减闸门** → 本脚本的输入是 `<脚本目录>/profile.md`、`facts.md` 等。
     在 `memtether/` 目录下跑它（那些文件不存在）→ 生成**近乎空**的投影 →
     把线上真投影**整篇覆盖成空**。这是最危险的一条，因为它"跑起来不报错"。

★可预演：目标路径支持 `MEM_PROJ_PATH`（与 hubguard / attach_hubguard 同源）。
  原版把用户级投影路径写死 → 无法预演，一跑就动线上。
  （★路径一律用正斜杠书写。原因：普通字符串里出现无效转义会触发
    `SyntaxWarning: invalid escape sequence`，在 `-W error` 下直接 **SyntaxError 编译失败**。
    本段文档串里凡出现反斜杠都写了双份，就是为了不在文档里再犯一次。）

机制：
  1. 读 memory_hub 的 facts.md / decisions.md / incidents.md / active_tasks.md
  2. 与 profile.md（用户画像/硬规则）合并
  3. 生成精简投影，写到 <HUB>/projections/agent_workbuddy.md
  4. 经守卫原子覆盖 ~/.workbuddy/MEMORY.md（锁 + 快照 + 冲突拒写 + 预算/骤减闸门）

用法：
  python sync_memory.py                 # 同步一次（覆盖）
  python sync_memory.py --check         # 只看差异，不写（无副作用）
  python sync_memory.py --dry-run       # 跑完全部门禁但最终不落盘
  python sync_memory.py --on-conflict force|sidecar
  python sync_memory.py --force-shrink  # 明确同意把投影写小（跳过骤减闸门）

退出码：0 成功 / 2 用法错 / 3 超预算 / 4 骤减 / 5 并发冲突 / 6 守卫不可用(fail-closed)
"""
import os
import sys
import time

HUB = os.path.dirname(os.path.abspath(__file__))
# ★可预演：仓库内投影也能重定向，否则测试一跑就在仓库里造未跟踪文件（selfcheck C04 会报）。
PROJECTION = os.environ.get('MEM_SYNC_PROJECTION') or \
    os.path.join(HUB, 'projections', 'agent_workbuddy.md')

# ★守卫（同目录同包，正常必可导入；导入失败时**拒绝写共享文件**，见 _refuse）
try:
    import hubguard as _hg
except Exception:                                            # noqa: BLE001
    _hg = None

# ★目标可预演：MEM_PROJ_PATH 与 hubguard.proj_paths 同源（可能含多个，取第一个）
_ENV_PROJ = os.environ.get('MEM_PROJ_PATH') or ''
WORKBUDDY_MEM = (_ENV_PROJ.split(os.pathsep)[0] if _ENV_PROJ
                 else os.path.expanduser(r'~\.workbuddy\MEMORY.md'))

# 骤减闸门：现状不小（>=500）时，新内容不足现状一半 → 拒写。
# 门槛取"一半"而不是"必须增长"：合法重建确实可能变小（条目合并/裁剪），
# 但**腰斩**几乎只可能是生成器输入缺失（本脚本最危险的那个场景）。
SHRINK_MIN_OLD = 500
SHRINK_FLOOR = 200
SHRINK_RATIO = 0.5

# 中枢各文件（缺失则跳过）
SECTION_FILES = [
    ('facts.md', '## 永久事实'),
    ('decisions.md', '## 关键结论'),
    ('incidents.md', '## 问题诊断'),
    ('active_tasks.md', '## 当前任务'),
]


def read_if_exists(path):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    return ''


def _missing_inputs():
    """返回缺失的生成器输入文件。

    ★为什么需要它：本脚本的输入（profile.md + 四类 md）在**部署目录**下，
      即 `memory_hub/`。若在别处（例如仓库 `memtether/`）运行，输入全部缺失，
      生成的是**近乎空**的投影。旧版对此**完全静默** —— 这是病根 ④。
      骤减闸门能拦住"覆盖已有内容"这一半，但"目标本来是空的"那一半拦不住，
      所以这里必须再补一条**显式告警**，把静默变小变成可解释。
    """
    names = ['profile.md'] + [f for f, _ in SECTION_FILES]
    return [n for n in names if not os.path.exists(os.path.join(HUB, n))]


def build_projection():
    parts = []
    parts.append('# MEMORY.md — WorkBuddy 侧记忆（启动适配层）\n')
    parts.append('<!-- 真源：<HUB>（唯一事实源） -->\n')
    parts.append('<!-- 本文件由 sync_memory.py 全量生成，勿手改；改记忆走 mem.py add -->\n')
    parts.append('<!-- 生成时间 %s -->\n' % time.strftime('%Y-%m-%d %H:%M:%S'))

    # 用户画像（最稳定，置顶）
    profile = read_if_exists(os.path.join(HUB, 'profile.md'))
    if profile:
        parts.append('\n## 用户画像（真源 profile.md）\n')
        parts.append(profile)
        parts.append('')

    # 各分类记忆
    for fname, title in SECTION_FILES:
        content = read_if_exists(os.path.join(HUB, fname))
        if content:
            parts.append(title + '\n')
            parts.append(content)
            parts.append('')

    parts.append('## 工作规则（硬性，不可丢）\n')
    parts.append('- 唯一事实源 = <HUB>（sink.json + facts/decisions/incidents/active_tasks）。\n')
    parts.append('- 会话启动/摘要压缩后，优先重读本文件与 memory_hub 的 DIGEST.md，覆盖摘要里的冲突信息。\n')
    parts.append('- 关键结论必须写入 memory_hub（走 mem.py add），绝不只留在对话里。\n')
    parts.append('- 拿不准"记没记住"时，读文件，不靠回忆；下全称否定结论前先全盘搜索（含 E 盘等非系统盘）。\n')
    parts.append('- 每轮回答前执行 preflight.py 扫描中枢；每轮末尾执行 post_turn.py 反思沉淀。\n')

    return '\n'.join(parts)


# ---------------------------------------------------------------- 守卫层

def _refuse(msg, code):
    """拒绝写：**fail-closed**。返回 **(code, msg)** —— 与 `guarded_write` 的
    成功路径 `(0, 'ok')` / `(0, 'dry-run')` **同一种形状**。

    ★铁律：降级必须报错，不能报绿。守卫不可用 / 越线时宁可"什么都没写"，
      也绝不退化成一次裸写 —— 裸写正是本脚本原来的病根。

    ★★ 2026-09-17 修的契约断裂（实测抓出，不是读代码猜的）：
      本函数原来 `return code`（裸 int），而成功路径 `return 0, 'dry-run'`（二元组）。
      于是 `main()` 里的 `rc, _ = guarded_write(...)` 一旦走到任何拒绝分支，
      就会 `TypeError: cannot unpack non-iterable int object` →
      **Python 默认 exit(1)**，把 rc=3/4/5/6/7 的语义**全部吃掉**。
      后果不是"报错"，而是**"fail-closed 退化成崩溃"**：调用方拿不到区分度，
      也看不出到底是超预算、骤减还是守卫不可用。
      探针 `E:/Temp/probe_refuse_contract.py` 实测：`_refuse` → `42 int`、
      预算闸门 → `3 int`、解包 → `TypeError`；修后全部为二元组。
    """
    sys.stderr.write('[sync_memory][拒绝] %s\n' % msg)
    return code, msg


def _budget(path):
    if _hg is not None:
        return _hg.slot_budget(path)
    return int(os.environ.get('MEM_PROJ_BUDGET', '3980'))


def guarded_write(path, text, on_conflict='abort', force_shrink=False,
                  dry_run=False):
    """把 `text` 经守卫写进共享文件。返回 (rc, 说明)。rc==0 才算写成。"""
    # ① 读前指纹（冲突检测的基准）
    before = _hg.snapshot(path) if _hg is not None else None

    # ② 预算闸门
    cap = _budget(path)
    n = len(text)
    if n > cap:
        return _refuse('投影 %d 字符 > 槽位硬顶 %d（超出会被**整篇截断**）；'
                       '请先裁剪再写。目标：%s' % (n, cap, path), 3)

    # ③ 骤减闸门
    old_chars = (before or {}).get('chars', 0) if before else 0
    if not force_shrink and old_chars >= SHRINK_MIN_OLD and \
            (n < SHRINK_FLOOR or n < old_chars * SHRINK_RATIO):
        return _refuse('投影骤减：现状 %d 字符 → 新内容 %d 字符（不足一半）。'
                       '通常是生成器输入缺失（例如在 %s 下跑，读不到 '
                       'profile.md/facts.md）。若确属有意，请加 --force-shrink。'
                       % (old_chars, n, HUB), 4)

    if dry_run:
        print('[dry-run] 全部门禁通过：%d/%d 字符，现状 %d 字符，未落盘'
              % (n, cap, old_chars))
        return 0, 'dry-run'

    if _hg is None:
        return _refuse('hubguard 不可导入，无法为共享文件提供守卫 → 拒绝写 %s。'
                       '（旧版会直接裸写，这正是要修掉的病）' % path, 6)

    # ④ 并发闸门 + 原子写（符号链接 / 硬链接 / 换行跟随 / 权限降级全在 atomic_write 内）
    # ★★ 2026-09-17：这里原来是 `except _hg.ConcurrentModification as e:`。
    #    Python 只在**真的抛异常**时才求值 except 子句，所以只要第 183 行的
    #    `if _hg is None: return` 挡在前面，就永远走不到 —— 但这是**顺序依赖**的
    #    隐患：`_hg` 为 None 时属性访问会抛
    #    `AttributeError: 'NoneType' object has no attribute 'ConcurrentModification'`，
    #    把真正的错误（守卫不可用）盖掉，fail-closed 就变成了"崩在一个不相干的异常上"。
    #    探针实测已确认该 AttributeError 会真实发生（[5b] 行）。
    #    改法：类对象用 getattr 安全取（取不到就退回 None），单 except + isinstance 判定，
    #    结构上**不依赖 `_hg` 非空、也不依赖 try/except 的书写顺序**。
    _cm_cls = getattr(_hg, 'ConcurrentModification', None) if _hg is not None else None
    try:
        r = _hg.commit_guarded(path, text, before, on_conflict=on_conflict,
                               tag='sync_memory')
    except Exception as e:                                   # noqa: BLE001
        if _cm_cls is not None and isinstance(e, _cm_cls):
            # ★hubguard.ConcurrentModification 把 hint 拼进了消息本体，
            #   **没有** `self.hint` 属性（实测 `getattr(e,'hint','')` 恒为空）。
            #   所以取 str(e) —— 里面已含路径与 before/after 指纹，对排障才有用。
            return _refuse('并发冲突：另一客户端在你读取之后改过 %s。\n'
                           '  %s\n  已**拒写**，对方版本完好。请重新运行，'
                           '或显式 --on-conflict force/sidecar。'
                           % (path, str(e)), 5)
        return _refuse('写入失败（%s: %s）：%s' % (type(e).__name__, e, path), 7)

    if isinstance(r, dict) and r.get('conflict'):
        return _refuse('并发冲突（%s）：我的版本已另存 %s，原文件未被覆盖。'
                       % (on_conflict, r.get('wrote')), 5)
    flags = []
    for k in ('atomic', 'symlink', 'hardlink'):
        if isinstance(r, dict) and r.get(k):
            flags.append(k)
    print('已写共享槽位：%s (%d 字符，%s)'
          % (path, n, '/'.join(flags) or 'atomic'))
    return 0, 'ok'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    do_check = '--check' in argv
    dry_run = '--dry-run' in argv
    force_shrink = '--force-shrink' in argv
    on_conflict = 'abort'
    if '--on-conflict' in argv:
        i = argv.index('--on-conflict')
        if i + 1 >= len(argv) or argv[i + 1] not in ('abort', 'force', 'sidecar'):
            sys.stderr.write('用法：--on-conflict abort|force|sidecar\n')
            return 2
        on_conflict = argv[i + 1]

    projection = build_projection()

    # ★把"投影为什么变小"从静默变成可解释（见 _missing_inputs 的说明）
    miss = _missing_inputs()
    if len(miss) == len(SECTION_FILES) + 1:
        sys.stderr.write(
            '[sync_memory][warn] 生成器输入**全部缺失**：%s 下 profile.md/facts.md/'
            'decisions.md/incidents.md/active_tasks.md 一个都没有 → 本次投影仅 %d 字符。\n'
            '  正确做法：在 memory_hub/ 目录下运行本脚本。'
            '若目标槽位已有内容，骤减闸门会拒写（rc=4）。\n'
            % (HUB, len(projection)))

    if do_check:
        # ★只读：不得创建任何文件（含临时文件）
        old = read_if_exists(WORKBUDDY_MEM)
        print('目标：%s' % WORKBUDDY_MEM)
        print('差异：' + ('有变化' if old != projection else '无变化'))
        print('投影长度：%d 字符（槽位硬顶 %d）'
              % (len(projection), _budget(WORKBUDDY_MEM)))
        return 0

    # 仓库内投影（非共享文件，不注入、无预算约束）
    # ★--dry-run 必须**什么都不写**：dry-run 的语义是"预演"，不是"写一半"。
    #   旧写法在 --dry-run 时仍会写这个文件 —— 与"只读命令不得创建文件"相冲突。
    if dry_run:
        print('[dry-run] 跳过仓库内投影写入：%s' % PROJECTION)
    else:
        try:
            os.makedirs(os.path.dirname(PROJECTION), exist_ok=True)
            if _hg is not None:
                _hg.atomic_write(PROJECTION, projection)
            else:
                with open(PROJECTION, 'w', encoding='utf-8') as f:
                    f.write(projection)
            print('已写投影：%s (%d 字符)' % (PROJECTION, len(projection)))
        except Exception as e:                               # noqa: BLE001
            sys.stderr.write('[sync_memory][warn] 仓库内投影写入失败：%s\n' % e)

    rc, _ = guarded_write(WORKBUDDY_MEM, projection,
                          on_conflict=on_conflict,
                          force_shrink=force_shrink, dry_run=dry_run)
    return rc


if __name__ == '__main__':
    sys.exit(main())
