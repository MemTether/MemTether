"""scan_history_leaks.py —— 补上 scan_leaks.py 的**历史盲区**

为什么需要这个：
    scan_leaks.py 只扫「当前跟踪的文件」。但 git 历史里躺着**曾经跟踪过、后来删掉**的文件——
    它们照样会被 `git clone` 拉下来。实测本仓库 1.0GB 历史里就有：
      · memory.db（341 条事实，含真名/学号/密钥/安全事件）
      · models/*.onnx（1.11GB + 91MB 模型权重，不该进代码仓）
    只扫工作区 = 只看了冰山一角。开源前必须把**历史**也过一遍。

做法：
    枚举 `git rev-list --objects --all` 的全部对象 → 取 blob → 单个 `git cat-file --batch`
    顺序读出 → 按 scan_leaks.py 同一套规则（含 leak_terms.local.json）匹配文本。

用法：
    python scripts/scan_history_leaks.py
    python scripts/scan_history_leaks.py --json
    python scripts/scan_history_leaks.py --max-mb 8      # 只扫小于 8MB 的 blob（默认 4）
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.environ.get('MEM_SCAN_REPO') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.abspath(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
# ★注意：scan_leaks 必须在 MEM_SCAN_REPO 设好之后再 import —— 它的 HERE 是模块级常量，
#   先 import 再改环境变量是无效的（顺序陷阱）。
import scan_leaks as SL  # noqa: E402

# 二进制/大文件不必逐字节搜（ONNX 里不会有真名）
_SKIP_EXT = ('.onnx', '.pt', '.bin', '.safetensors', '.png', '.jpg', '.jpeg',
             '.gif', '.zip', '.gz', '.7z', '.exe', '.dll', '.so', '.pyd',
             '.db', '.sqlite', '.sqlite3', '.parquet', '.pdf')


def _git(*args, **kw):
    return subprocess.run(['git'] + list(args), cwd=HERE, capture_output=True, **kw)


def list_objects():
    """→ [(sha, path, size)]，含历史里已删除的路径。"""
    r = _git('rev-list', '--objects', '--all', text=True)
    pairs = []
    for line in (r.stdout or '').splitlines():
        if ' ' in line:
            sha, path = line.split(' ', 1)
            pairs.append((sha, path))
        else:
            pairs.append((line, ''))
    if not pairs:
        return []
    # 批量取类型 + 体积
    inp = '\n'.join(p[0] for p in pairs) + '\n'
    r2 = subprocess.run(['git', 'cat-file', '--batch-check=%(objecttype) %(objectsize)'],
                        cwd=HERE, input=inp, capture_output=True, text=True)
    out = []
    for (sha, path), line in zip(pairs, (r2.stdout or '').splitlines()):
        parts = line.split()
        if len(parts) != 2:
            continue
        typ, size = parts[0], int(parts[1])
        if typ != 'blob':
            continue
        out.append((sha, path, size))
    return out


def read_blob(sha):
    r = subprocess.run(['git', 'cat-file', 'blob', sha], cwd=HERE, capture_output=True)
    return r.stdout or b''


def main():
    as_json = '--json' in sys.argv
    max_mb = 4.0
    if '--max-mb' in sys.argv:
        try:
            max_mb = float(sys.argv[sys.argv.index('--max-mb') + 1])
        except (IndexError, ValueError):
            pass
    cap = int(max_mb * 1024 * 1024)

    objs = list_objects()
    if not objs:
        print('没有历史对象（或不在 git 仓库里）')
        return 0

    total = sum(s for _, _, s in objs)
    skipped_ext = 0
    skipped_big = 0
    findings = []
    n_scanned = 0

    # 去重：同一 blob 可能挂在多个路径上
    seen = {}
    for sha, path, size in objs:
        seen.setdefault(sha, []).append(path)

    for sha, paths in seen.items():
        size = next(s for s2, p2, s in objs if s2 == sha)
        name = paths[0]
        if name.lower().endswith(_SKIP_EXT):
            skipped_ext += 1
            continue
        if size > cap:
            skipped_big += 1
            continue
        raw = read_blob(sha)
        if b'\x00' in raw[:4096]:
            skipped_ext += 1
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = raw.decode('gbk', 'replace')
            except Exception:
                skipped_ext += 1
                continue
        n_scanned += 1
        # ★字段顺序陷阱（2026-09-16 实测踩到）：
        #   SL.RULES 的元素是 (类别, 正则, 级别, 说明)，**正则在第 2 位、级别在第 3 位**。
        #   我第一版按 (label, sev, pat) 解包 → pat 拿到的是级别字符串 'block'
        #   → re.finditer('block', ...) 把 memoryBlock/blocks/blocked 全命中，
        #     报出 228 条假阳性；同时 sev 拿到正则串，永远 != 'BLOCK'，全被降级成 WARN。
        #   教训：跨模块复用常量时，**别按"看起来合理"的顺序解包，回源头数一遍**。
        #
        # ★另两点必须与 scan_leaks.py 对齐，否则结论不可比：
        #   ① 不传 re.I —— 原实现是大小写敏感的（加了 re.I 会把 'lock' 类词炸出来）
        #   ② 逐行走 BENIGN_LINE 过滤 —— 否则扫描器会命中**自己的规则文案**，
        #      实测「安全事件」那一类的说明里就写着被查的词，自找命中。
        for i, line in enumerate(text.splitlines(), 1):
            if any(b.search(line) for b in SL.BENIGN_LINE):
                continue
            for cat, pat, lvl, _note in SL.RULES:
                if not pat:
                    continue
                for m in re.finditer(pat, line):
                    findings.append({
                        'severity': lvl.upper(), 'rule': cat, 'blob': sha[:12],
                        'size': size, 'paths': paths[:4], 'line': i,
                        'hit': m.group(0)[:60], 'context': line.strip()[:110],
                    })
                    break   # 同一行同一规则只报一次

    # ★2026-09-17：降级必须失败闭锁，与 scan_leaks.py 对齐。
    #   原实现：词表缺失 → 打一行 ⚠ 降级，然后照样「✓ 零命中」+ return 0。
    #   调用方（含 CI / 发布闸门）看到 exit 0 就会把「这几类没查」当成「查过且干净」
    #   —— 实测正是这么发生的：发布库副本没有词表，历史扫描一路绿灯，
    #     而真名/班级学号/安全事件三类**一条都没查**。
    #   scan_leaks.py 早就修成 exit 2 了，这个历史扫描器漏了同一刀。
    DEGRADED = bool(getattr(SL, '_MISSING', None))
    EXIT_DEGRADED = 2   # 与 scan_leaks.main() 的降级码保持一致

    if as_json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return EXIT_DEGRADED if DEGRADED else 0

    print('=' * 96)
    print('git 历史泄密扫描（含已删除路径 —— scan_leaks.py 的盲区）')
    print('=' * 96)
    print('  历史对象 %d 个 · 合计 %.1f MB' % (len(objs), total / 1024 / 1024))
    print('  去重后 blob %d 个 → 实扫 %d 个文本 blob' % (len(seen), n_scanned))
    print('  跳过：扩展名不可搜 %d · 超过 %.1f MB %d' % (skipped_ext, max_mb, skipped_big))
    if SL._MISSING:
        # ★不能用 ⚠（看起来像"提示"）—— 这是**结论不可信**，不是"稍微注意下"。
        #   与 scan_leaks.report() 同一口径：打 ✗ + 「无法判定」，且不打 ✓ 零命中。
        print('  ✗ 无法判定（降级）：本地敏感词表缺失，以下类别**未参与扫描**：%s'
              % ' / '.join(SL._MISSING))
        print('     期望位置：%s' % getattr(SL, '_TERMS', '(未取到)'))
        print('     ★这不是「零命中」，是「这几类没查」。用 MEM_SCAN_TERMS '
              '指向真源词表后重跑，结论才成立。')

    # 大对象单独点名（不进文本扫描，但发布前必须处理）
    big = sorted(((s, p) for _, p, s in objs if s > 1024 * 1024), reverse=True)
    if big:
        print('\n  【大对象 >1MB】—— 历史里的大文件，clone 会全量拉下来')
        for s, p in big[:12]:
            print('    %8.2f MB  %s' % (s / 1024 / 1024, p or '(无路径)'))

    if not findings:
        if DEGRADED:
            print('\n  — 已扫类别零命中（整体结论仍为「无法判定」，词表缺失）')
            print('=' * 96)
            return EXIT_DEGRADED
        print('\n  ✓ 历史文本 blob 零命中')
        print('=' * 96)
        return 0

    blocks = [f for f in findings if f['severity'] == 'BLOCK']
    warns = [f for f in findings if f['severity'] != 'BLOCK']
    print('\n  命中 %d 条（BLOCK %d · WARN %d）' % (len(findings), len(blocks), len(warns)))
    for f in sorted(findings, key=lambda x: (x['severity'] != 'BLOCK', x['rule'])):
        print('  [%s] %s  blob=%s  %s' % (
            f['severity'], f['rule'], f['blob'], ' / '.join(p or '(无路径)' for p in f['paths'])))
        print('        行 %d: %s' % (f['line'], f['context']))
    print('=' * 96)
    print('★提示：历史里的命中**无法靠改文件消除** —— 必须重写历史（git filter-repo）。')
    print('  改文件只改"最新快照"，历史快照原样保留，clone 照样拿得到。')
    if blocks:
        return 1
    # 无 BLOCK 但降级 → 仍是「无法判定」，不能报成功
    return EXIT_DEGRADED if DEGRADED else 0


if __name__ == '__main__':
    sys.exit(main())
