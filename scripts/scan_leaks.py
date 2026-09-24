#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_leaks.py —— 开源前「引擎侧」泄密扫描（只读）

【为什么必须单独有这一份】
  `开源脱敏清单-20260916.md` 只扫了 `memory.db`（数据侧），结论是
  「数据文件不进仓库 → 安全」。这个结论**漏了半边**：
  数据不进仓库是对的，但**引擎文件自己就带着敏感内容**——
  实测 76 个跟踪文件里有真名、班级、安全事件词、密钥前 8 位、
  36 处本机账号绝对路径。它们会原样发布到 GitHub。

  ★教训：脱敏清单必须分「数据侧」和「引擎侧」两栏，
    只做数据侧 = 把最显眼的那堆藏起来，把写着名字的那页留着。

【只扫 git 跟踪文件】
  跟踪 = 会被发布。未跟踪的本地文件（memory.db / sink.json / models/）
  不在扫描范围——它们的处置原则是「不进仓库」，已在数据侧清单里。

【★2026-09-16 补洞：范围必须含「已暂存 + 未跟踪但未被忽略」】
  上面那句原本写成 `git ls-files`（= 索引）。实测踩坑：
  新写的 `scripts/make_demo_db.py` 在 `git add` **之前**跑扫描 → 它不在索引里
  → **完全没被扫**；紧接着 `git add -A && git commit` 就把它提交了。
  扫描"跑了"、结论"零命中"，但**扫描范围不含那个文件** —— 属
  「跑起来不报错、但结论错」的一类。
  现在改成 `--cached --others --exclude-standard`，即
  **「`git add -A` 之后会被发布的全集」**，与发布动作同口径。
  （`--exclude-standard` 仍遵守 .gitignore，所以 memory.db / models/ 照旧不在范围内。）

【判级】
  block : 必须清掉才可发布（真名 / 班级 / 事故**标识词** / 密钥）
  warn  : 只提示人工复核（事故**通用技术词** / 本机账号路径 / 第三方账号），不阻断发布
  ★2026-09-16：事故类词必须分两档 —— 判据是「这个词单独出现时，是否等于
    声明『这台机器被控过』」。详见下方 RULES 注释与 leak_terms.local.json 的 _readme。

【退出码】
  0  无 BLOCK 命中，且词表就位（可以发布）
  1  有 BLOCK 命中（必须清）
  2  词表缺失 → 真名 / 事故类未参与扫描 → **无法判定**（★不是"零命中"）
     ★为什么给 2 而不是 0：返回 0 会让调用方把「这几类没查」当成「查过且干净」，
       而本项目所有严重缺陷都是这一类「跑起来不报错、但结论错」。

用法：
  python scripts/scan_leaks.py            # 人读报告
  python scripts/scan_leaks.py --json     # 机器读
环境变量：
  MEM_SCAN_REPO   指定被扫仓库（默认 = 本脚本所在仓库的根）
  MEM_SCAN_TERMS  指定敏感词表路径（默认 = 本脚本同目录的 leak_terms.local.json）
                  ★发布库那份扫描器旁边没有词表 → 需指回真源那份，否则降级
"""
import os
import re
import sys
import json
import subprocess

HERE = os.environ.get('MEM_SCAN_REPO') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ★2026-09-16：加 MEM_SCAN_REPO 环境变量 —— 扫描器不该只能扫"自己所在的仓库"。
#   实际需求：用 memory_hub 里这一份，去扫**发布库 memtether** 的历史。
#   没有这个开关时，把脚本拷过去会让 HERE 指到 memtether 的父目录，扫错仓库还看不出来。
#   （踩坑：第一版直接 cp 过去跑，HERE 变成 E:\RUANJIAN，静默扫错目标。）
HERE = os.path.abspath(HERE)

# ★2026-09-16：项目专属敏感词（真名 / 班级 / 安全事件词）改为**外部加载**。
#   原因：这份扫描器本身也是要开源的文件。把真名和安全事件词写死在代码里，
#   等于**用泄密清单去泄密**——实测它自己就贡献了 3 处 block 命中。
#   外置后：扫描器可原样发布，而「你的真名是什么」留在被 .gitignore 排除的本地文件里。
_TERMS = (os.environ.get('MEM_SCAN_TERMS')
          or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'leak_terms.local.json'))
# ★2026-09-16：加 MEM_SCAN_TERMS 开关。动机与 MEM_SCAN_REPO 同源 ——
#   真源只有一份词表（在 memory_hub/scripts/），发布库那份扫描器旁边**没有**词表，
#   于是「在发布库跑扫描器自检」必然降级。有这个开关就能把发布库副本指向真源词表，
#   让「发布库自检」这件事真正可做，而不是每次都看到一句降级提示后不了了之。


def _local_terms():
    """读本地敏感词表；文件缺失 → 四类降级为空（只跑通用规则），不报错。

    ★2026-09-16 增第 4 类 incident_soft_patterns：事故词**必须分两档**。
      原先「通用技术词」与「样本唯一标识」同列 BLOCK，而替换器又无差别替换
      → 把正常内容里的通用技术词改写成不存在的说法，破坏内容却全流程绿灯。
      详见 leak_terms.local.json 的 _readme。
      ★本注释本身也不能写出被查的词（第一版写了，当场自命中 3 处）。
    """
    try:
        with open(_TERMS, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return [], [], [], []
    pick = lambda k: [x for x in (d.get(k) or []) if isinstance(x, str) and x]
    return (pick('names'), pick('class_patterns'),
            pick('incident_patterns'), pick('incident_soft_patterns'))


_NAMES, _CLASSES, _INCIDENTS, _INCIDENTS_SOFT = _local_terms()

# (类别, 正则, 级别, 说明) —— **类别顺序即报告顺序，勿改**
RULES = [
    ('真名', '|'.join(re.escape(x) for x in _NAMES), 'block',
     '真实姓名，公开发布等于实名上网'),
    ('班级/学号', '|'.join(_CLASSES + [r'学号\s*[:：]?\s*\d+']), 'block',
     '班级与学号是校内唯一标识'),
    # ★2026-09-16：事故词**必须分两档**。判据是：
    #   「这个词单独出现时，是否等于声明『这台机器被控过』？」
    #     是 → BLOCK（样本名 / 驱动名 / 服务名这类**唯一标识**，必须阻断发布）
    #     否 → WARN（远程控制 / 内核级隐藏程序 / 自我复制程序 / 隐蔽通道等**通用技术词**，
    #              正常技术讨论里到处都有，只提示人工复核，不阻断）
    #   ★原先两档混在一起全列 BLOCK，而 memtether_sync.py 的替换器又无差别改写
    #     → 把正常内容里的通用技术词改成不存在的说法，破坏内容却全流程绿灯
    #     （2026-09-16 实测事故，污染已入库）。
    ('安全事件(标识词)', '|'.join(_INCIDENTS), 'block',
     # ★说明文案本身不能出现被查的词 —— 否则扫描器**自己命中自己**
     #   （实测两次：一次是这里写了被查的英文词名，一次是写了通用技术词的中文名，
     #    预演提交时当场多出 block 命中，纯属自找）。
     '本机安全事故的**唯一标识**（样本名 / 驱动名 / 服务名）：'
     '公开等于声明「这台机器被控过」，对个人是实打实的风险，不是技术细节'),
    ('安全事件(通用词)', '|'.join(_INCIDENTS_SOFT), 'warn',
     # ★按 BLOCK 处理有两重害处，这是本次事故最可复用的教训：
     #   ① 阻断正常发布 → 人会习惯性加 `--yes` 绕过闸门，真命中时也照过；
     #   ② 更糟：替换器会无差别改写它们，把正常说法改成不存在的说法，
     #      破坏内容而全流程绿灯（这就是 2026-09-16 那 3 处污染）。
     '通用技术词（远程控制 / 内核级隐藏程序 / 自我复制程序 / 隐蔽通道等）：'
     '单独出现不构成泄密声明，仅提示人工复核，不阻断发布'),
    ('密钥明文/前缀', r'(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{4,}', 'block',
     '★哪怕是前 8 位也是泄漏：它把爆破空间砍掉了几个数量级'),
    ('本机账号路径', r'[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}(?:Administrator|USER-\d+)', 'warn',
     '绝对路径暴露本机账户名；开源版应改为 os.path.expanduser("~")'),
    ('邮箱/手机/QQ', r'[\w.+-]+@(?:gmail|qq|163|outlook|foxmail)\.[\w.]+|(?<!\d)1[3-9]\d{9}(?!\d)', 'warn', ''),
]

# 🔴 必须剔除空正则：本地词表缺失时 `|'.join([])` 得到空串，
#    而 `re.finditer('', line)` 会在**每个字符位置**命中 → 报告瞬间爆掉。
#    顺带在报告里把降级这件事说清楚，避免"扫出 0 处"被误读成"很干净"。
_MISSING = [c for c, p, _, _ in RULES if not p]
RULES = [r for r in RULES if r[1]]

# 命中这些的整行视为良性（脱敏工具自身 / 正则模式串 / 占位符）
BENIGN_LINE = [
    re.compile(r're\.compile'),
    re.compile(r'\{20,\}'),
    re.compile(r'<见vault'),
    re.compile(r'脱敏'),
    re.compile(r'扫描|扫一遍|正则确认'),
]


def tracked_files():
    """「会被发布的全集」= 已跟踪 + 已暂存 + 未跟踪但未被忽略。

    ★不要退回 `git ls-files`（只含索引）：新增文件在 `git add` 之前扫不到，
      而 add 之后往往不会再扫一遍 → 静默漏检（2026-09-16 实测踩过）。
    """
    # 2026-09-22 修：不用 text=True。text=True 时 Python 按 locale 解码 stdout，
    # 某些环境下（实测 memory_hub 仓）会让 r.stdout 直接变 None，
    # 下游 r.stdout.split 抛 AttributeError —— 闸门自己崩，比闸门失效更危险。
    # 改 bytes 模式拿原始输出、按 b'\0' 拆，再逐个解码，绕开 locale 解码。
    r = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others',
                        '--exclude-standard'], cwd=HERE,
                       capture_output=True)
    if r.returncode != 0:
        return []
    out = set()
    for p in (r.stdout or b'').split(b'\0'):
        if p:
            try:
                out.add(p.decode('utf-8'))
            except Exception:
                out.add(p.decode('utf-8', 'replace'))
    return sorted(out)


def untracked_files():
    """其中「还没进索引」的那部分 —— 单独报出来，让"扫了哪些"可核。"""
    r = subprocess.run(['git', 'ls-files', '-z', '--others', '--exclude-standard'],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return sorted({p for p in r.stdout.split('\0') if p})


def scan():
    findings = []
    for rel in tracked_files():
        full = os.path.join(HERE, rel)
        try:
            with open(full, 'rb') as f:
                raw = f.read()
        except OSError:
            continue
        if b'\0' in raw[:4096]:          # 二进制跳过
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('utf-8', 'replace')
        for i, line in enumerate(text.splitlines(), 1):
            if any(b.search(line) for b in BENIGN_LINE):
                continue
            for cat, pat, lvl, note in RULES:
                for m in re.finditer(pat, line):
                    findings.append({
                        'file': rel, 'line': i, 'cat': cat, 'level': lvl,
                        'match': m.group(0)[:60],
                        'snippet': line.strip()[:110], 'note': note,
                    })
    return findings


def report(findings):
    by_cat = {}
    for f in findings:
        by_cat.setdefault(f['cat'], []).append(f)
    print('=' * 84)
    print('开源前「引擎侧」泄密扫描 —— 范围 =「会被发布的全集」（索引 + 未跟踪非忽略）')
    print('=' * 84)
    if _MISSING:
        # ★避免「扫出 0 处」被误读成「很干净」——降级必须显式说出来，
        #   而且**不能**再打「✓ 零命中」那个对勾：三类根本没查，打勾就是自欺。
        #   （这正是本项目头号缺陷型态「跑起来不报错、但结论错」的显示层版本。）
        print('  ✗ 无法判定（降级）：本地敏感词表缺失，以下类别**未参与扫描**：')
        print('       %s' % ' / '.join(_MISSING))
        print('     期望位置：%s' % _TERMS)
        print('     ★这不是「零命中」，是「这几类没查」。修好词表再跑，')
        print('       或用 MEM_SCAN_TERMS 指向词表（发布库副本没有词表，需指回真源那份）。')
    else:
        print('  规则来源：%s' % _TERMS)
    if not findings:
        print('  ✓ 零命中' if not _MISSING else '  — 已扫类别零命中（整体结论仍为「无法判定」）')
        return
    order = [r[0] for r in RULES]
    for cat in order:
        fs = by_cat.get(cat)
        if not fs:
            continue
        lvl = fs[0]['level']
        files = sorted({f['file'] for f in fs})
        print('\n[%s] %s —— %d 处 / %d 个文件' % (
            'BLOCK' if lvl == 'block' else 'WARN ', cat, len(fs), len(files)))
        if fs[0]['note']:
            print('   %s' % fs[0]['note'])
        shown = {}
        for f in fs:
            shown.setdefault(f['file'], []).append(f)
        for fn, items in sorted(shown.items(), key=lambda x: -len(x[1])):
            print('   %-42s %d 处' % (fn, len(items)))
            for it in items[:3]:
                print('      L%-5d %s' % (it['line'], it['snippet']))
            if len(items) > 3:
                print('      …另 %d 处' % (len(items) - 3))
    nblock = sum(1 for f in findings if f['level'] == 'block')
    nwarn = len(findings) - nblock
    print('\n' + '=' * 84)
    print('  合计 %d 处：BLOCK %d 处（必须清）· WARN %d 处（建议清）' % (len(findings), nblock, nwarn))
    _ut = untracked_files()
    print('  扫描范围：%d 个文件（会被发布的全集 = 索引 + 未跟踪非忽略）' % len(tracked_files()))
    if _ut:
        print('    其中未跟踪（尚未 git add）%d 个 —— 这些也**会被扫**，'
              '避免"add 之前扫不到"的漏检：' % len(_ut))
        for p in _ut[:8]:
            print('      + %s' % p)
        if len(_ut) > 8:
            print('      …另 %d 个' % (len(_ut) - 8))
    print('=' * 84)


def _fix_stdio():
    """stdout/stderr UTF-8 reconfigure (errors=replace).
    Windows GBK console causes UnicodeEncodeError on unicode symbols in report(),
    exit 1 misread as "leak found" when actually scan did not finish.
    Ported from memtether copy 2026-09-24.
    """
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        try:
            if _s is not None and hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    if "--commit-msg" in sys.argv:
        # P0-10: scan commit message from stdin, block on sensitive hits.
        msg = sys.stdin.read()
        findings = []
        for i, line in enumerate(msg.splitlines(), 1):
            if any(b.search(line) for b in BENIGN_LINE):
                continue
            for cat, pat, lvl, note in RULES:
                for m in re.finditer(pat, line):
                    findings.append({
                        "file": "<commit-msg>", "line": i, "cat": cat,
                        "level": lvl, "match": m.group(0)[:60],
                        "snippet": line.strip()[:110], "note": note,
                    })
        _fix_stdio()
        if findings:
            print("commit-msg scan: %d hit(s)" % len(findings), file=sys.stderr)
            for f in findings:
                print("  [%s] L%d %s: %s" % (f["level"], f["line"], f["cat"], f["match"]), file=sys.stderr)
        if _MISSING:
            print("commit-msg scan: terms file missing -> cannot verify", file=sys.stderr)
            sys.exit(2)
        sys.exit(1 if any(f["level"] == "block" for f in findings) else 0)

    fs = scan()
    if '--json' in sys.argv:
        print(json.dumps(fs, ensure_ascii=False, indent=2))
    else:
        report(fs)
    if _MISSING:
        # ★降级 → 结论是「无法判定」→ 非零退出（失败闭锁）。
        #   返回 0 会让调用方把「三类没查」当成「查过且干净」——
        #   正是本项目反复踩的那类静默失败（L1 全绿、结论全错）。
        return 2
    return 1 if any(f['level'] == 'block' for f in fs) else 0


if __name__ == '__main__':
    sys.exit(main())
