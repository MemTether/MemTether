#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attach_hubguard.py —— 把 hubguard 的三项守卫**零侵入**接入任意一版 gateway.py。

为什么需要它
------------
`memory_hub/gateway.py` 是「国内版未提交在制品」，一个字节都不能改；
可三问题的最终受益方恰恰是它。所以本工具**默认只读、只产出补丁**，
真接入必须显式 `--mode=apply --yes`。

三个问题 → 三处最小改动
------------------------
① 并发写静默覆盖     → 末尾块 `install_guards()`：只依赖函数名，对两版同构
② 投影不保留来源     → 投影行改调 `_hg_fact_line()`，输出 `- [日期|类型·来源]`
③ 整体重写互相覆盖   → 写盘段换 `atomic_write()`（跟随换行）+ 格式回退闸门；
                       顺带补 `MEM_PROJ_PATH` 支持 —— 原版 L1145 硬编码
                       `os.path.expanduser(r'~\\.workbuddy\\MEMORY.md')`，
                       导致**该版本的 rebuild 根本无法预演**（一跑就动线上）。

★设计原则：**插入点唯一化**
    所有辅助函数集中定义在文件**末尾同一个块**里，正文只做 3 处字符串级
    最小替换。这样对「不同源的另一个版本」（72550 B vs 60632 B）也能适用 ——
    逐行打补丁必然对不齐，锚点替换则只要求那几行字面一致。

★踩过的坑（三条，都是「静默回归」——只有跑到特定命令才暴露）
------------------------------------------------------------
1) **模块遮蔽**（2026-09-17 实测，修了两次才对）：末尾块原用
   `sys.path.insert(0, <hubguard 所在目录>)`。而 `memtether/` 与 `memory_hub/`
   各有一份同名 `governance.py` → `import governance` 被「提到最优先」的
   memtether 那份命中；它硬编码 `DB = HERE/memory.db`（**不认 MEM_DB**）→
   连到不存在的文件被 sqlite3 当场建成 0 字节空库 →
   `sqlite3.OperationalError: no such table: facts` →
   `gateway.py govern / stale / conflicts_exact` 三个命令**全部崩溃**
   （线上 `memory_hub/gateway.py` 同样崩，而 rebuild / remember 一切正常）。
   **第一次修**：改 `sys.path.append`（放最后，脚本自身目录仍优先）——
   ★**只降概率、没除根**：只要「目标目录缺同名模块」，仍旧静默命中
   memtether 的同名文件。沙箱（只复制 gateway.py + memory.db）当场复现，
   traceback 依旧指向 `memtether/governance.py`。
   **第二次修（最终）**：**根本不碰 `sys.path`** —— `hubguard.py` 只依赖
   标准库，改用 `importlib.util.spec_from_file_location` 按显式文件路径加载。
   遮蔽面归零。`check` 判据相应升级为
   「路径插入：importlib ✔ / append △ / insert(0) ✘」。
   ★教训：往 `sys.path` 里塞目录 = 交出同名模块的解释权；能按路径加载就别动搜索路径。
2) **沙箱不完整**（2026-09-17）：`gateway.py` 在**函数内**延迟 `import
   governance / memsearch / mem0_config / tool_audit / asset_bench ...`，
   所以「沙箱只带 gateway.py + memory.db」是错的 —— 一跑 `govern` 就崩。
   把 `memory_hub/*.py` 一起复制进沙箱即可（`governance.py` 的 `HERE` 随
   脚本位置偏移，正好指向沙箱库，天然隔离）。
3) **git apply 行尾改写**：见下文 `patch` 说明。

用法
----
    python attach_hubguard.py check  [--target <gateway.py>] [--self-prove]
    python attach_hubguard.py patch  [--target <gateway.py>] [--out <x.diff>]
    python attach_hubguard.py apply  [--target <gateway.py>] --yes
    python attach_hubguard.py revert [--target <gateway.py>] --yes [--expect-sha <sha>]

    check  —— 只读：报告锚点命中数、是否已接入、缺什么。`--self-prove`
              额外对目标目录做前后 sha256 清单对比，自证**零落盘**。
    patch  —— 生成 unified diff（不碰目标文件）。★用 git 打时必须带
              `-c core.autocrlf=false`：本仓库 `core.autocrlf=true`，裸
              `git apply` 会把**整个文件**行尾改成 CRLF（实测 1423 行全变、
              72550 B → 78211 B），结果与接入器不一致。补丁文件自身是纯 LF、
              内容无误，错的是 apply 时的行尾转换。
    apply  —— 真改；自动备份 `gateway.py.bak-<ts>`；幂等（已接入则跳过）。
    revert —— 按标记块还原。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

MARK = 'hubguard-attach'
DEFAULT_TARGET = r'E:\RUANJIAN\memory_hub\gateway.py'
CREATE_NO_WINDOW = 0x08000000


def _warn_autocrlf(cwd: str) -> None:
    """检测 core.autocrlf，为 true/input 时明确警告 `git apply` 会改写行尾。

    ★★2026-09-17 实测踩到的坑（补丁交付物可用性的最后一环）：
      `memory_hub` 仓库 `core.autocrlf = true`。此时裸跑

          git apply patch-memory_hub-hubguard.diff

      git 会按 autocrlf 把**整个工作区文件**的行尾转成 CRLF —— 实测
      1423 行 LF → 1511 行全 CRLF、72550 B → 78211 B，与接入器 apply 的
      76700 B / sha 366dab29… **不一致**，还会产生「全文件重写」的巨型 diff。
      **补丁文件自身是纯 LF、内容完全正确**，错的是 apply 那一刻的行尾转换。
      正确姿势：`git -c core.autocrlf=false apply <diff>`（实测得到
      sha 366dab29b14358f7 / 76700 B，与接入器**字节一致**）。

    ★只读：仅执行 `git config --get`，不写文件、不创建文件。
    """
    try:
        p = subprocess.run(['git', 'config', '--get', 'core.autocrlf'],
                           cwd=cwd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           creationflags=CREATE_NO_WINDOW)
        v = (p.stdout or '').strip().lower()
    except Exception:
        return
    if v in ('true', 'input'):
        print('  ⚠ 该仓库 core.autocrlf=%s —— 裸 `git apply` 会把整个文件行尾'
              '改写成 CRLF（全文件重写）。必须带 -c core.autocrlf=false。' % v)


# --------------------------------------------------------------------------
# 待插入的块
# --------------------------------------------------------------------------

_B_PROJ_WRITE = '''        # >>> {mark}:proj-write
        # 由 memtether/attach_hubguard.py 注入：原子写 + 格式回退闸门 + 投影路径可隔离
        import time as _hg_t
        _wb = (os.environ.get('MEM_PROJ_PATH')
               or os.path.expanduser(r'~\\.workbuddy\\MEMORY.md'))
        # ★保留原名 wb：rebuild 的返回体（`'mem': wb`）与后续日志都可能引用它。
        #   在隔离副本上真打补丁时才暴露出来 —— 少这一行就 NameError。
        wb = _wb
        os.makedirs(os.path.dirname(_wb), exist_ok=True)
        _hg_downgrade = None
        _hg_writes = []
        if HG is not None:
            try:
                with open(_wb, encoding='utf-8') as _hg_f:
                    _hg_old = _hg_f.read()
                _hg_no, _hg_nn = _count_tagged(_hg_old), _count_tagged(mem_text)
                if _hg_no and not _hg_nn:
                    # 磁盘上现有投影有来源标记、本次产物 0 条 => 本次生成器更旧，
                    # 直接覆盖等于把对方的格式升级抹掉。另存后再降级，且显式告警。
                    _hg_downgrade = _hg_no
                    HG.atomic_write('%s.tagged-%s' % (_wb, _hg_t.strftime('%Y%m%d-%H%M%S')),
                                    _hg_old)
                    sys.stderr.write(
                        '[rebuild][warn] ★★格式回退：磁盘现有投影有 %d 条来源标记，'
                        '本次产物 0 条（生成器更旧）。已另存旧版。\\n' % _hg_no)
            except Exception as _hg_e:
                sys.stderr.write('[rebuild][warn] 格式回退检查跳过：%s\\n' % _hg_e)
            _hg_r = HG.atomic_write(_wb, mem_text)
            _hg_writes = [{k: _hg_r.get(k) for k in
                           ('path', 'bytes', 'atomic', 'newline', 'why')
                           if _hg_r.get(k) is not None}]
            if _hg_downgrade:
                _hg_writes[0]['format_downgrade_from'] = _hg_downgrade
        else:
            with open(_wb, 'w', encoding='utf-8') as _hg_f:
                _hg_f.write(mem_text)
            _hg_writes = [{'path': _wb, 'bytes': len(mem_text.encode('utf-8')),
                           'atomic': False}]
        globals()['_HG_LAST_WRITES'] = _hg_writes
        # <<< {mark}:proj-write
'''

_B_FACT_LINE = """            _item = _hg_fact_line(_d, _typ, _r['source'], _lead(_r['content']))  # >>> {mark}:fact-line
"""

_B_TAIL = '''# >>> {mark}:begin
# 由 memtether/attach_hubguard.py 注入 —— 并发锁 + 投影来源标记 + 原子写。
# 整块可整段删除（或跑 `attach_hubguard.py revert`）以回到原行为。
# 块内所有辅助函数集中定义在此，正文只做最小替换，故对不同源版本都适用。
try:
    import sys as _hg_sys
except Exception:                                   # pragma: no cover
    _hg_sys = None

HG = None
for _hg_p in (os.environ.get('MEM_HUBGUARD_PATH'), r'E:\\RUANJIAN\\memtether'):
    if not _hg_p:
        continue
    _hg_f = os.path.join(_hg_p, 'hubguard.py')
    if not os.path.isfile(_hg_f):
        continue
    try:
        # ★★按**显式文件路径**加载，绝不改 sys.path。
        #   实测事故（2026-09-17，修了两次）：memtether/ 与 memory_hub/ 各有
        #   一份同名 governance.py，而 gateway.py 在函数内 `import governance`。
        #   只要 hubguard 所在目录进了 sys.path，目标目录一旦缺同名模块就会
        #   静默命中 memtether 那份；它第 42 行硬编码
        #       DB = os.path.join(HERE, 'memory.db')
        #   （不认 MEM_DB）→ 连到 memtether/memory.db（不存在，被 sqlite3.connect
        #   当场建成 0 字节空库）→ sqlite3.OperationalError: no such table: facts。
        #   后果：govern / stale / conflicts_exact 三个命令全部崩溃（含线上），
        #   而 rebuild / remember 全都不报错 —— **静默回归**。
        #   先改 append 只降概率，沙箱仍复现；最终改 spec_from_file_location：
        #   hubguard.py 只依赖标准库，可脱离 sys.path 直接按路径加载，
        #   从此不存在任何模块遮蔽面。
        import importlib.util as _hg_iu
        _hg_spec = _hg_iu.spec_from_file_location('hubguard', _hg_f)
        _hg_mod = _hg_iu.module_from_spec(_hg_spec)
        _hg_spec.loader.exec_module(_hg_mod)
        HG = _hg_mod
        break
    except Exception as _hg_e:                      # pragma: no cover
        HG = None
        if _hg_sys is not None:
            _hg_sys.stderr.write('[hubguard] 载入失败（已降级为原行为）：%s\\n' % _hg_e)


def _hg_fact_line(date10, typ, source, lead):
    """投影事实行的唯一出口。有 hubguard 时带 `类型·来源` 标记。"""
    if HG is not None:
        return HG.format_fact_line(date10, typ, source, lead)
    return '- [%s|%s] %s' % (date10, typ, lead)


_HG_TAG_RE = None


def _count_tagged(text):
    """★刻意不依赖 hubguard：最需要被抓的场景正是「hubguard.py 没装」，
    若这里调 HG.parse_fact_line，那个场景反而检查不了（自证盲区）。"""
    global _HG_TAG_RE
    if _HG_TAG_RE is None:
        import re as _hg_re
        _HG_TAG_RE = _hg_re.compile(r'^- \\[\\d{4}-\\d{2}-\\d{2}\\|[^\\]·]+·[^\\]·]+\\]')
    return sum(1 for _l in (text or '').split('\\n') if _HG_TAG_RE.match(_l))


_HG_LAST_WRITES = []
_HG_GUARDED = HG.install_guards(globals()) if HG is not None else []
if HG is not None:
    sys.stderr.write('[hubguard] 已接管 %d 个写函数：%s\\n'
                     % (len(_HG_GUARDED), ', '.join(_HG_GUARDED)))
# <<< {mark}:end
'''


# --------------------------------------------------------------------------
# 锚点（原文精确片段）
# --------------------------------------------------------------------------

_A_PROJ_WRITE = r'''        wb = os.path.expanduser(r'~\.workbuddy\MEMORY.md')
        os.makedirs(os.path.dirname(wb), exist_ok=True)
        with open(wb, 'w', encoding='utf-8') as f:
            f.write(mem_text)
'''

_A_FACT_LINE = """            _item = '- [%s|%s] %s' % (_d, _typ, _lead(_r['content']))
"""

_A_TAIL = """if __name__ == '__main__':
    main()
"""

ANCHORS = (
    ('proj-write', _A_PROJ_WRITE),
    ('fact-line', _A_FACT_LINE),
    ('tail', _A_TAIL),
)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def detect_newline(text: str) -> str:
    """跟随目标文件的换行风格，避免制造满屏假 diff。"""
    head = text[:65536]
    return '\r\n' if '\r\n' in head else '\n'


def read_text(path: str):
    """按原文读（newline='' 不做翻译），返回 (text, newline)。"""
    with open(path, encoding='utf-8', newline='') as f:
        raw = f.read()
    return raw, detect_newline(raw)


def write_text(path: str, text: str, newline: str):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def _norm(s: str, nl: str) -> str:
    return s.replace('\n', nl)


def dir_fingerprint(d: str):
    """目录内文件 sha256 清单（只读，不创建任何东西）。"""
    out = {}
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in ('.git', '__pycache__', '.venv-memory')]
        for fn in files:
            p = os.path.join(root, fn)
            try:
                with open(p, 'rb') as f:
                    out[os.path.relpath(p, d)] = hashlib.sha256(f.read()).hexdigest()[:16]
            except Exception:
                out[os.path.relpath(p, d)] = 'ERR'
    return out


# --------------------------------------------------------------------------
# 分析 / 构建
# --------------------------------------------------------------------------

# 三档载入方式的可读标签（见 docstring「踩过的坑 1)」）
_SHADOW_TAG = {
    'importlib': 'importlib ✔（按显式文件路径加载，遮蔽面为零）',
    'append': '△ append（放最后，仅降概率 —— 目标目录缺同名模块时仍会静默命中）',
    'insert(0)': '✘ insert(0)（★会抢占同名模块的解释权）',
}


def _shadow_tag(sh):
    return _SHADOW_TAG.get(sh, '★%s —— 未知载入方式' % sh)


def analyze(path: str):
    """只读分析。返回 dict，不改任何文件。"""
    if not os.path.isfile(path):
        return {'ok': False, 'why': 'target not found: %s' % path}
    text, nl = read_text(path)
    installed = ('%s:begin' % MARK) in text

    hits = {}
    for aid, anchor in ANCHORS:
        if ('%s:%s' % (MARK, aid)) in text:
            hits[aid] = 'already'
        else:
            hits[aid] = text.count(_norm(anchor, nl))

    problems = []
    if not installed:
        for aid, cnt in hits.items():
            if cnt == 'already':
                continue
            if cnt == 0:
                problems.append('锚点未命中：%s（该版本此行字面不同，需人工确认）' % aid)
            elif cnt > 1:
                problems.append('锚点不唯一：%s 命中 %d 次（拒绝自动替换）' % (aid, cnt))

    # 依赖检查
    deps = {}
    for name in ('os', 'sys'):
        deps[name] = bool(re.search(r'^\s*(import\s+%s\b|from\s+%s\s+import)' % (name, name),
                                    text, re.M))
    if not deps['sys']:
        problems.append('缺少 `import sys`（proj-write 块要用 sys.stderr 告警）')

    # ★模块遮蔽风险（文本级判据，零副作用）—— 见 _B_TAIL 里的实测事故记录。
    #   判据刻意做成纯文本匹配：真去 `import gateway` 验证会执行 install_guards，
    #   有副作用，而这里只需要抓「载入方式」这一个根因。
    #   三档：importlib（按路径加载，遮蔽面为零）> append（只降概率）> insert(0)（抢占）。
    shadow = None
    if installed:
        if re.search(r'sys\.path\.insert\(\s*0\s*,\s*_hg_p\s*\)', text):
            shadow = 'insert(0)'
            problems.append('★模块遮蔽风险：hubguard 目录被 sys.path.insert(0, ...) '
                            '提到最优先，会遮蔽目标目录同名模块'
                            '（实测后果：govern / stale / conflicts_exact 全部崩溃）')
        elif re.search(r'sys\.path\.append\(\s*_hg_p\s*\)', text):
            shadow = 'append'
            problems.append('△模块遮蔽残留风险：仍把 hubguard 目录塞进 sys.path —— '
                            '目标目录一旦缺同名模块（如 governance）会静默命中 '
                            'hubguard 侧同名文件。建议改用 spec_from_file_location')
        elif 'spec_from_file_location' in text:
            shadow = 'importlib'

    return {
        'ok': not problems,
        'path': path,
        'bytes': os.path.getsize(path),
        'lines': text.count('\n') + 1,
        'newline': repr(nl),
        'installed': installed,
        'hits': hits,
        'deps': deps,
        'shadow': shadow,
        'problems': problems,
        'sha256': hashlib.sha256(open(path, 'rb').read()).hexdigest()[:16],
        '_text': text,
        '_nl': nl,
    }


def build_new_text(text: str, nl: str):
    """在内存里构造接入后的文本。返回 (new_text, applied_ids)。"""
    applied = []
    if ('%s:begin' % MARK) in text:
        return text, ['<already-installed>']

    t = text
    for aid, anchor in ANCHORS:
        if ('%s:%s' % (MARK, aid)) in t:
            continue
        a = _norm(anchor, nl)
        if t.count(a) != 1:
            raise ValueError('锚点 %s 命中 %d 次，拒绝替换' % (aid, t.count(a)))
        # ★必须用 replace 而非 .format()：块内有 `{k: ...}` 字典推导与
        #   `\d{4}` 正则量词，.format() 会把它们当占位符（KeyError: 'k'）。
        if aid == 'proj-write':
            rep = _norm(_B_PROJ_WRITE.replace('{mark}', MARK), nl)
        elif aid == 'fact-line':
            rep = _norm(_B_FACT_LINE.replace('{mark}', MARK), nl)
        elif aid == 'tail':
            # ★★块首尾的分隔空行**由这里显式给出**，块字符串自身不带多余换行。
            #   踩过的坑（2026-09-17）：原来 `_B_TAIL` 头尾各带空行，apply 后是
            #   `\n\n# >>> begin … # <<< end\n\n`，而 revert 的正则只吃 `:end` 后那一个
            #   换行 → 还原后**多出 3 个空行**，sha256 对不回原版（1424 行 → 1427 行）。
            #   回滚不能逐字节还原，就等于「打得进、退不干净」，不能对外宣称可回滚。
            rep = (_norm('\n', nl) + _norm(_B_TAIL.replace('{mark}', MARK), nl)
                   + _norm('\n', nl) + a)
        else:                                       # pragma: no cover
            raise ValueError('未知锚点 %s' % aid)
        t = t.replace(a, rep, 1)
        applied.append(aid)
    return t, applied


# --------------------------------------------------------------------------
# 模式
# --------------------------------------------------------------------------

def mode_check(target: str, self_prove: bool = False):
    d = os.path.dirname(os.path.abspath(target))
    before = dir_fingerprint(d) if self_prove else None
    info = analyze(target)
    after = dir_fingerprint(d) if self_prove else None

    print('目标：%s' % target)
    if not info.get('ok') and 'bytes' not in info:
        print('✘ %s' % info['why'])
        return 1
    print('大小：%d B / %d 行 / 换行 %s / sha256 %s'
          % (info['bytes'], info['lines'], info['newline'], info['sha256']))
    print('已接入：%s' % ('是' if info['installed'] else '否'))
    print('锚点命中：')
    for aid, _ in ANCHORS:
        v = info['hits'][aid]
        tag = '已接入' if v == 'already' else ('命中 %d' % v if v == 1 else '★命中 %s' % v)
        print('  - %-11s %s' % (aid, tag))
    print('依赖：%s' % info['deps'])
    if info.get('shadow'):
        print('路径插入：%s' % _shadow_tag(info['shadow']))
    if info['problems']:
        print('问题：')
        for p in info['problems']:
            print('  ✘ %s' % p)
    else:
        print('问题：无 ✔')

    if self_prove:
        same = (before == after)
        print('\n--- 零落盘自证 ---')
        print('目录文件数：%d' % len(before))
        diff = sorted(set(before) ^ set(after)) + \
               sorted(k for k in set(before) & set(after) if before[k] != after[k])
        print('前后差异：%s' % (diff if diff else '无'))
        print('★零落盘 = %s' % same)
        if not same:
            return 1
    return 0 if info['ok'] or info['installed'] else 1


def mode_patch(target: str, out: str | None):
    info = analyze(target)
    if 'bytes' not in info:
        print('✘ %s' % info['why'])
        return 1
    if info['installed']:
        print('已接入，无需补丁。')
        return 0
    if not info['ok']:
        for p in info['problems']:
            print('✘ %s' % p)
        return 1
    new_text, applied = build_new_text(info['_text'], info['_nl'])
    diff = ''.join(difflib.unified_diff(
        info['_text'].splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile='a/gateway.py', tofile='b/gateway.py'))
    if out:
        with open(out, 'w', encoding='utf-8', newline='') as f:
            f.write(diff)
        print('已写出补丁：%s（%d 字符 / 应用锚点 %s）'
              % (out, len(diff), ','.join(applied)))
        # ★补丁交付物必须自带「怎么打」的说明 —— 否则用户裸跑 git apply 会踩
        #   autocrlf 的行尾改写坑（见 _warn_autocrlf 的实测记录）。
        print('  ★ 应用方式（二选一，结果等价）：')
        print('     ① 推荐（可回滚）：python attach_hubguard.py apply --yes --target <gateway.py>')
        print('     ② 用 git：git -c core.autocrlf=false apply %s'
              % os.path.basename(out))
        _warn_autocrlf(os.path.dirname(os.path.abspath(target)))
    else:
        sys.stdout.write(diff)
    return 0


def mode_apply(target: str, yes: bool):
    if not yes:
        print('拒绝：apply 会修改在制品文件，必须显式 --yes。')
        return 2
    info = analyze(target)
    if 'bytes' not in info:
        print('✘ %s' % info['why'])
        return 1
    if info['installed']:
        print('已接入，跳过（幂等）。')
        return 0
    if not info['ok']:
        for p in info['problems']:
            print('✘ %s' % p)
        return 1
    new_text, applied = build_new_text(info['_text'], info['_nl'])
    bak = '%s.bak-%s' % (target, time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(target, bak)
    write_text(target, new_text, info['_nl'])
    after = analyze(target)
    print('✔ 已接入 %s → %s' % (target, ','.join(applied)))
    print('  备份：%s' % bak)
    print('  sha256 %s → %s' % (info['sha256'], after['sha256']))
    print('  复核：已接入=%s / 问题=%s' % (after['installed'], after['problems'] or '无'))
    sh = after.get('shadow')
    print('  路径插入：%s' % _shadow_tag(sh))
    return 0


def mode_revert(target: str, yes: bool, expect_sha: str = None):
    """把 apply 打进去的三处改动逐字还原。

    ★★替换串必须走 lambda（2026-09-17 实测踩到）：
      `_A_PROJ_WRITE` 里含 `r'~\\.workbuddy\\MEMORY.md'`，反斜杠在 `re.sub` 的**替换模板**里
      是转义语法 → `re.error: bad escape \\.` → revert 直接崩，补丁就"打得进、退不出"。
      传函数（`lambda m: repl`）可完全关掉模板转义解析。三条替换一律照此办理。
    """
    if not yes:
        print('拒绝：revert 会修改文件，必须显式 --yes。')
        return 2
    text, nl = read_text(target)
    if ('%s:begin' % MARK) not in text:
        print('未检测到接入标记，无需还原。')
        return 0
    t = text
    # 1) 末尾整块（含 apply 时显式加上的前后分隔空行，见 build_new_text 的 tail 分支）
    t = re.sub(re.escape(_norm('\n# >>> %s:begin' % MARK, nl)) + r'.*?'
               + re.escape(_norm('# <<< %s:end\n\n' % MARK, nl)),
               lambda m: '', t, flags=re.S)
    # 2) proj-write 块 → 原 4 行
    t = re.sub(re.escape(_norm('        # >>> %s:proj-write' % MARK, nl)) + r'.*?'
               + re.escape(_norm('        # <<< %s:proj-write' % MARK, nl)) + r'\r?\n',
               lambda m: _norm(_A_PROJ_WRITE, nl), t, flags=re.S)
    # 3) fact-line 行 → 原行
    t = re.sub(re.escape(_norm("            _item = _hg_fact_line(", nl)) + r'[^\r\n]*'
               + re.escape(_norm("  # >>> %s:fact-line" % MARK, nl)),
               lambda m: _norm(_A_FACT_LINE.rstrip('\n'), nl), t)

    # ---- 还原后硬自检（不通过就不写盘）----
    problems = []
    if MARK in t:
        problems.append('仍有 %d 处 %s 标记残留' % (t.count(MARK), MARK))
    for aid, anchor in ANCHORS:
        n = t.count(_norm(anchor, nl))
        if n != 1:
            problems.append('锚点 %s 还原后命中 %d 次（应为 1）' % (aid, n))
    if not t.rstrip('\r\n').endswith('main()'):
        problems.append("文件尾部未回到 `if __name__ == '__main__': main()`")
    if expect_sha:
        got = hashlib.sha256(t.encode('utf-8')).hexdigest()
        if not got.startswith(expect_sha.lower()):
            problems.append('sha256 不符：got %s… / expect %s…' % (got[:16], expect_sha[:16]))
    if problems:
        print('✘ 还原自检未通过，已放弃写盘（原文件未动）：')
        for p in problems:
            print('   - %s' % p)
        return 1

    bak = '%s.bak-%s' % (target, time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(target, bak)
    write_text(target, t, nl)
    after = analyze(target)
    print('✔ 已还原：%s' % target)
    print('  备份：%s' % bak)
    print('  sha256 %s → %s' % (after['sha256'], '（与 --expect-sha 一致）' if expect_sha else ''))
    print('  复核：已接入=%s / 残留标记=%d / 锚点=%s'
          % (after['installed'], t.count('%s:' % MARK),
             {k: after['hits'].get(k) for k, _ in ANCHORS}))
    return 0


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description='把 hubguard 零侵入接入 gateway.py')
    ap.add_argument('mode', choices=('check', 'patch', 'apply', 'revert'))
    ap.add_argument('--target', default=DEFAULT_TARGET)
    ap.add_argument('--out', default=None, help='patch 模式输出文件')
    ap.add_argument('--yes', action='store_true', help='apply/revert 必须显式确认')
    ap.add_argument('--self-prove', action='store_true', help='check 模式自证零落盘')
    ap.add_argument('--expect-sha', default=None,
                    help='revert 模式：**还原后**文本必须匹配的 sha256 前缀'
                         '（★填「原版」的 sha，不是当前文件的 sha。'
                         '例：--expect-sha 09c96d6e3b8d9c4e）')
    a = ap.parse_args(argv)

    if a.mode == 'check':
        return mode_check(a.target, a.self_prove)
    if a.mode == 'patch':
        return mode_patch(a.target, a.out)
    if a.mode == 'apply':
        return mode_apply(a.target, a.yes)
    return mode_revert(a.target, a.yes, a.expect_sha)


if __name__ == '__main__':
    sys.exit(main())
