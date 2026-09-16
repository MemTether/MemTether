"""refuse_gate.py —— 拒答判据第二轮：把「词形证据」做对，再看还剩多少空间

第一轮（refuse_bench.py cover）的结论是「零误拒下最多拦 23%」，但那份实现有两处**方法缺陷**，
会让结论偏悲观。本轮先把缺陷修掉，再重新标定，避免把「实现没做对」误判成「原理不可行」。

缺陷 1 · ASCII 走字符二元组 → 噪声爆炸
    "Python 的 GIL 是什么机制？" 的二元组含 py/yt/th/ho/on，而库里有 Python310 路径
    → 噪声命中把覆盖率抬到 0.62（实测）。工具名/文件名必须以**整词**匹配。

缺陷 2 · 证据取自 top-10 拼接串 → 稀释
    10 篇文档拼一起，随便哪个词都能撞上。应以 **top-1** 为准（真要答，答的也是第一篇）。

本轮另加一个观察口径：**把负样本按"库里到底有没有这个词"再分一次**
    N1 领域外（珠峰/沸点/红楼梦）→ 词形证据应为 0
    N2 领域内未存（GPA/主板型号）  → 部分词有、被问的那个词没有
    N3 同形（Clash 续费/7-Zip 注册码）→ 工具名有、被问的属性没有

用法（零模型成本，读 refuse_bench_raw.json）：
    python refuse_gate.py
    python refuse_gate.py --detail
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RAW = os.path.join(HERE, 'refuse_bench_raw.json')
NEG_FILE = os.path.join(HERE, 'bench_data', 'refuse_negatives.json')

import refuse_bench as RB  # noqa: E402

# ASCII 整词里没有鉴别力的：纯数字、单位、常见动词
_ASCII_STOP = {
    'the', 'and', 'for', 'how', 'what', 'why', 'can', 'you', 'are', 'was',
    'with', 'this', 'that', 'its', 'get', 'set', 'new', 'old', 'use',
    'http', 'https', 'com', 'www', 'exe', 'dll', 'sys', 'txt', 'md', 'py',
}


def ascii_terms(q):
    """整词级 ASCII 术语（≥3 字符），小写。这是本轮的核心修正点。"""
    out = []
    for t in re.findall(r'[A-Za-z][A-Za-z0-9_\-\.]{2,}', q):
        tl = t.lower().strip('.-_')
        if len(tl) < 3 or tl in _ASCII_STOP:
            continue
        out.append(tl)
    return out


def cjk_terms(q, stop):
    """中文侧仍用字符二元组（免分词），但用库内文档频次自动剔停用。"""
    return [b for b in RB._bigrams(q) if b not in stop]


def build_stop(docs):
    df = {}
    for d in docs:
        for b in set(RB._bigrams(d)):
            df[b] = df.get(b, 0) + 1
    n = len(docs)
    return {b for b, c in df.items() if c > 0.30 * n}, df, n


def evidence(q, top1_low, top1_bg, stop):
    """返回 (ascii_hits, ascii_total, cjk_hits, cjk_total)"""
    a_terms = ascii_terms(q)
    a_hit = [t for t in a_terms if t in top1_low]
    c_terms = cjk_terms(q, stop)
    c_hit = [t for t in c_terms if t in top1_bg]
    return a_hit, a_terms, c_hit, c_terms


def main():
    detail = '--detail' in sys.argv
    if not os.path.exists(RAW):
        print('缺少 %s，请先跑 refuse_bench.py run' % RAW)
        return
    raw = json.load(io.open(RAW, encoding='utf-8'))
    docs = [r for r in RB.corpus_rows() if r.strip()]
    stop, df, n_doc = build_stop(docs)
    print('=' * 92)
    print('拒答判据 v2（ASCII 整词 + CJK 二元组，证据取 top-1）')
    print('  语料 %d 篇 · 自动停用二元组 %d 个' % (n_doc, len(stop)))
    print('=' * 92)

    spec = json.load(io.open(NEG_FILE, encoding='utf-8'))
    import hard_holdout as HH

    m = {x['id']: x for x in raw['rows']}

    def row(cid, q):
        x = m.get(cid)
        if not x:
            return None
        blob = x.get('blob') or ['']
        top1 = (blob[0] or '').lower()
        a_hit, a_all, c_hit, c_all = evidence(q, top1, set(RB._bigrams(top1)), stop)
        return {
            'id': cid, 'q': q, 'sim': x.get('sim_max', 0.0),
            'a_hit': a_hit, 'a_all': a_all, 'c_hit': c_hit, 'c_all': c_all,
            'n_hit': len(a_hit) + len(c_hit), 'n_all': len(a_all) + len(c_all),
            'cov': (len(a_hit) + len(c_hit)) / max(1, len(a_all) + len(c_all)),
        }

    neg = [r for r in (row(n['id'], n['q']) for n in spec['negatives']) if r]
    pos = [r for r in (row(p['id'], p['q']) for p in HH.HOLDOUT) if r]

    print('\n【负样本】证据强度升序（越低越该拒答）')
    print('-' * 92)
    for r in sorted(neg, key=lambda z: (z['n_hit'], z['cov'])):
        print('  hit=%d/%-2d cov=%.2f sim=%.3f %-4s %s' % (
            r['n_hit'], r['n_all'], r['cov'], r['sim'], r['id'], r['q'][:40]))
        if detail:
            print('        ascii 命中 %s / 全 %s' % (r['a_hit'], r['a_all']))
            print('        cjk   命中 %s / 共 %d' % (r['c_hit'][:6], len(r['c_all'])))

    print('\n【正样本对照】证据强度升序（越低越可能被误拒）')
    print('-' * 92)
    for r in sorted(pos, key=lambda z: (z['n_hit'], z['cov']))[:10]:
        print('  hit=%d/%-2d cov=%.2f sim=%.3f %-4s %s' % (
            r['n_hit'], r['n_all'], r['cov'], r['sim'], r['id'], r['q'][:40]))

    # ---------------- 判据扫描 ----------------
    def evaluate(name, refuse_fn):
        rn = [r for r in neg if refuse_fn(r)]
        rp = [r for r in pos if refuse_fn(r)]
        return name, len(rn), len(neg), len(rp), len(pos)

    cands = []
    # P1 纯"零证据"：一个显著词都没命中
    cands.append(evaluate('P1 n_hit==0', lambda r: r['n_hit'] == 0))
    # P2 零证据 + 语义也弱
    for st in (0.70, 0.72, 0.74, 0.76, 0.78):
        cands.append(evaluate('P2 n_hit==0 且 sim<%.2f' % st,
                              lambda r, st=st: r['n_hit'] == 0 and r['sim'] < st))
    # P3 命中率极低
    for ct in (0.05, 0.10, 0.15, 0.20):
        cands.append(evaluate('P3 cov<%.2f' % ct, lambda r, ct=ct: r['cov'] < ct))
    # P4 命中率极低 + 语义弱
    for ct in (0.10, 0.15, 0.20, 0.25):
        for st in (0.72, 0.74, 0.76):
            cands.append(evaluate('P4 cov<%.2f 且 sim<%.2f' % (ct, st),
                                  lambda r, ct=ct, st=st: r['cov'] < ct and r['sim'] < st))
    # P5 ASCII 侧全灭（工具名/文件名一个都没命中）—— 专治 N3
    cands.append(evaluate('P5 ascii 全灭（有词但零命中）',
                          lambda r: bool(r['a_all']) and not r['a_hit']))

    print('\n【判据扫描】目标：拒答率高 + 误拒率 0')
    print('-' * 92)
    print('  %-34s %-16s %-16s' % ('判据', '拒答负样本', '误拒正样本'))
    for name, rn, nn, rp, np_ in cands:
        star = '  ★零误拒' if rp == 0 and rn > 0 else ''
        print('  %-34s %-16s %-16s%s' % (
            name, '%d/%d (%.0f%%)' % (rn, nn, rn / nn * 100),
            '%d/%d (%.0f%%)' % (rp, np_, rp / np_ * 100), star))

    ok = [c for c in cands if c[3] == 0 and c[1] > 0]
    print('-' * 92)
    if ok:
        best = max(ok, key=lambda c: c[1])
        print('★零误拒最优：%s → 拦 %d/%d（%.0f%%）' % (
            best[0], best[1], best[2], best[1] / best[2] * 100))
    else:
        print('★不存在任何零误拒工作点。')

    # 天花板：负样本里"原则上可拒"的占比
    print('\n【天花板拆解】按负样本类别看"词形证据"能否救')
    print('-' * 92)
    cls = {}
    for n, r in zip(spec['negatives'], neg):
        cls.setdefault(n.get('class', '?'), []).append(r)
    for k in sorted(cls):
        rs = cls[k]
        zero = sum(1 for r in rs if r['n_hit'] == 0)
        print('  %-26s %d 条，其中零证据 %d 条 → 该类可拦上限 %.0f%%' % (
            k, len(rs), zero, zero / len(rs) * 100))
    print('=' * 92)
    print('★读法：若某类"零证据"占比高，说明词形证据对它有效；')
    print('  若某类"零证据"占比低（证据反而很足），说明该类的难点不在"词"，在"属性"——')
    print('  库里有这个实体，但没有它被问的那个属性。词形法原理上无解，必须靠语义蕴含判定。')


if __name__ == '__main__':
    main()
