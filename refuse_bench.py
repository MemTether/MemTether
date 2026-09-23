#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refuse_bench.py —— 检索拒答能力：负样本集机械校验 + 拒答阈值标定

【这个脚本存在的理由】
  项目目标之一是「反向不瞎编」。但"拒答"落地必须有阈值，而
  **没有负样本集的阈值 = 假阳性护栏**：随手定 0.5，既不知道会拒掉多少
  本来答得出的问题，也不知道能拦住多少本不该答的问题。
  所以顺序被钉死为：先造负样本集 → 看分布 → 最后才谈阈值。
  本文件只做前两步，**不修改任何生产代码**（memsearch.py / gateway.py 一行不动）。

【为什么不能用 RRF 融合分当阈值】
  RRF 只用排名（1/(K+rank)），是**排名型**分数，天然不可跨查询比较：
  任何查询的第 1 名都拿到同一个 1/61，无论它相关还是不相关。
  可比的只有**原始余弦相似度** —— 结果里的 semantic 字段（memsearch 已透传）。

【★纯语义阈值会误杀一类正确查询】
  实测记录在 memsearch.py 里：查询含 ASCII 文件名时（如 mcp_server.py），
  向量路整体召回不到目标（embedding 被相近 token 带偏），semantic 全 0，
  真正命中的是关键词/字面路。此时纯语义阈值会判"该拒答"，但答案其实在库里。
  所以判据必须是两个条件同时成立：
      sim_max < thr   AND   查询里没有能在库内命中的 ASCII 实体
  本脚本把这个 gate 的效果一并量出来（gate 列）。

用法：
  python refuse_bench.py verify    # 秒级，零模型加载：机械校验负样本集的"库内不存在"
  python refuse_bench.py run       # 加载模型跑全套（负样本 + 正样本对照），出分布与扫描表
  python refuse_bench.py run bge-m3-int8@cached   # 复用上次召回转录重算阈值（不加载模型，秒级）
"""
import os
import re
import sys
import json
import time
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from interpreter import resolve_python, require_modules      # noqa: E402


def _fix_stdio():
    """★2026-09-23 补：stdout/stderr 按 UTF-8 重配（errors=replace），与 gateway.py 同源。

    根因：Windows 控制台默认 GBK，而本脚本大量中文 print（含 ✓/✗）。
    实测 `refuse_bench.py verify` 跑到最后一行
    print("✓ 全部通过") 直接 UnicodeEncodeError 崩掉 ——
    而崩溃前的校验其实全部通过，回看输出像"没结果"而不是"崩了"。
    这是本项目"跑完但结果错/不可用"家族的第 6 例。
    必须放在 resolve_python(announce=True) 之前 —— 那行也会打印中文。
    """
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        try:
            if _s is not None and hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_stdio()

# ★2026-09-18 改：不再写死 `.venv-memory` —— 发布库（clone 出来的）里没有这个目录，
#   写死等于「README 说能复跑、实际一跑就 WinError 2」。统一走 interpreter 解析。
PY, PY_SRC = resolve_python(HERE, announce=True)
NEG_FILE = os.path.join(HERE, 'bench_data', 'refuse_negatives.json')
RESULT = os.path.join(HERE, 'refuse_bench_result.json')
RAW = os.path.join(HERE, 'refuse_bench_raw.json')   # 原始召回转录，供重算阈值而不用重载模型


# ---------------------------------------------------------------- 语料读取
def corpus_rows():
    """把 facts + tool_assets 的全部文本拼成一份语料（校验用，不加载向量栈）。"""
    import sqlite3
    con = sqlite3.connect(os.path.join(HERE, 'memory.db'))
    cur = con.cursor()
    rows = []
    cur.execute('select content from facts')
    rows += [r[0] or '' for r in cur.fetchall()]
    cur.execute('select name, capabilities, path from tool_assets')
    rows += [' | '.join(str(x or '') for x in r) for r in cur.fetchall()]
    con.close()
    return rows


def _subject_terms(q):
    """从问题里抽出主语候选（去停用词后取前 2/3 字 + ASCII 实体）。

    判据要的是"这条记忆是不是在回答这个问题"，主语共现是最弱可用的代理。
    """
    t = (q or '').lower()
    out = set()
    for seg in re.findall(r'[\u4e00-\u9fa5]+', t):
        if len(seg) <= 4 and seg:
            out.add(seg)
        else:
            for n in (2, 3, 4):
                out.add(seg[:n])
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', t):
        out.add(w.lower())
    return out


# 2026-09-22 补：元讨论标记 —— 一条记忆如果在**谈论**某个词的出现/误报/判据，
# 它就不是在**回答**该词对应的问题。这是 memsearch 自指中毒的同族问题，
# 已在 refuse_bench 侧实证两次（N15 MySQL：先是渗透报告提及，后是我写的复盘提及）。
_META_TELL = ('误报', '共现', 'must_be_absent', '判据', '自指', '复盘', '本条目',
              '谈论', '提及', '提到', '零命中', '闸门')


def _cooccur_in_same_row(rows, tok, subj_terms):
    """同一行（同一条记忆）里同时出现 tok 和主语候选 => 可能是在回答。

    ★2026-09-22：排除元讨论行。若该行本身带 _META_TELL 标记，
    说明它在讨论"这个词被判命中"而不是"这个问题的答案是什么"，
    不能作为共现证据。
    """
    hits = []
    tl = tok.lower()
    for r in rows:
        low = (r or '').lower()
        if tl not in low:
            continue
        if any(t in low for t in _META_TELL):
            continue
        for st in subj_terms:
            if st and st in low and len(st) >= 2:
                hits.append('%s|%s' % (tok, st))
                break
    return hits


def _occurs(blob_low, tok):
    """出现次数。ASCII token 用词边界，避免 configPath 里的 gPa 这种假阳性；
    中文按裸子串（中文没有词边界概念）。"""
    t = tok.lower()
    if re.fullmatch(r'[a-z0-9_.\-]+', t):
        return len(re.findall(r'(?<![a-z0-9_])' + re.escape(t) + r'(?![a-z0-9_])', blob_low))
    return blob_low.count(t)


# ---------------------------------------------------------------- verify
def verify(verbose=True):
    spec = json.load(open(NEG_FILE, encoding='utf-8'))
    rows = corpus_rows()
    blob = ' \n '.join(rows)
    blob_low = blob.lower()

    bad = []
    warn_only = []
    for n in spec['negatives']:
        problems = []
        for tok in n.get('must_be_absent', []):
            c = _occurs(blob_low, tok)
            if c:
                # 2026-09-22 修：must_be_absent 的语义是"库里没有这个**答案**"，
                # 不是"库里没出现过这个词"。原实现用全库裸词计数，
                # 于是渗透报告/工具清单里**提到** MySQL 就算命中 —— 实测误报 3 条
                # (N10 显卡 / N15 MySQL / N21 许可证)，全是"被谈起"而非"被回答"。
                # 修法：命中时不立即判失败，而是再看一眼上下文是否构成"答案"，
                # 即该词是否与问题主语共现于同一条记忆内。不共现 = 只是被谈起。
                # ★主语里若本身就含被检词，共现判据会恒真（自己和自己共现），
                #   必须把与被检词相同的主语项排除掉，只留"另一个"主语。
                #   例：问「MySQL 版本是多少」，主语含 MySQL —— 此时共现不能作为
                #   "在回答"的证据，因为只要提到 MySQL 就算共现。
                _all_subj = _subject_terms(n.get('q', ''))
                subj = {x for x in _all_subj if x != tok.lower()}
                ctx_hits = _cooccur_in_same_row(rows, tok, subj) if subj else []
                if ctx_hits:
                    problems.append('must_be_absent 命中 %r x%d（且与问题主语共现于同一条：%s）'
                                    % (tok, c, ctx_hits[:2]))
                else:
                    warn_only.append('%s: 词 %r 库内出现 x%d，但未与问题主语共现 —— 被谈起而非被回答，不判失败'
                                     % (n.get('id'), tok, c))
        for pat in n.get('must_be_unanswered', []):
            m = re.search(pat, blob, re.I)
            if m:
                problems.append('must_be_unanswered 命中 %r -> %r' % (pat, m.group(0)))
        if problems:
            bad.append((n['id'], problems))

    if verbose:
        print('=' * 78)
        print('负样本集机械校验（%d 条）' % len(spec['negatives']))
        print('=' * 78)
        by = {}
        for n in spec['negatives']:
            by[n['class']] = by.get(n['class'], 0) + 1
        for k, v in sorted(by.items()):
            print('  %-28s %d 条' % (k, v))
        print('-' * 78)
        if not bad:
            print('  ✓ 全部 %d 条通过：库内确实不存在对应答案' % len(spec['negatives']))
        else:
            for cid, ps in bad:
                print('  ✗ %s' % cid)
                for p in ps:
                    print('      - %s' % p)
        print('=' * 78)
    return bad


# ---------------------------------------------------------------- run
def _query_all(queries, model_key, limit=10):
    """独立进程里跑全部查询（同进程模型只加载一次）。limit=10 = 生产默认值。"""
    # ★2026-09-18：只有这里真加载模型。`verify` / `cover` / `run ...@cached`
    #   都是零模型路径，不该被依赖拦住（缺 chromadb 的机器也该能跑它们排障）。
    #   这里必须拦：缺依赖不报错、只静默降级成纯关键词 → 阈值标定全废。
    require_modules(PY, 'chromadb', 'numpy')
    env = dict(os.environ)
    env['MEM_EMBED_MODEL'] = model_key
    env['MEM_EMBED_BACKEND'] = 'local'
    env['PYTHONPATH'] = HERE
    fd, path = tempfile.mkstemp(suffix='.json', prefix='rb_q_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(queries, f, ensure_ascii=False)
    env['RB_Q'] = path
    script = r'''
import os, sys, json
sys.path.insert(0, %r)
import memsearch
qs = json.load(open(os.environ['RB_Q'], encoding='utf-8'))
out = []
for q in qs:
    try:
        r = memsearch.search_hybrid(q['q'], limit=%d, decay=False)
        res = r.get('results', [])
        blob = [x['content'] for x in res]
        sims = [x.get('semantic', 0) or 0 for x in res]
    except Exception as e:
        blob = ['[ERR] %%s' %% e]; sims = []
    out.append({'id': q['id'], 'blob': blob,
                'sim_max': max(sims) if sims else 0.0,
                'sim_top1': sims[0] if sims else 0.0, 'n': len(blob)})
print(json.dumps({'info': memsearch.LAST_EMBED_INFO, 'rows': out}, ensure_ascii=False))
''' % (HERE, limit)
    try:
        r = subprocess.run([PY, '-c', script], cwd=HERE, env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if r.returncode != 0:
        return None, (r.stderr or '')[-400:]
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), None
    except Exception as e:
        return None, 'parse: %s / %s' % (e, (r.stdout or '')[-200:])


def _ascii_ents(q):
    """查询里长度 >=3 的 ASCII 实体（文件名/工具名）。这些是"关键词路能救回来"的候选。"""
    return [t for t in re.findall(r'[A-Za-z0-9_\-\.]{3,}', q)]


def sweep(neg, pos, blob_low, thresholds=None):
    """阈值扫描。neg/pos 是 [(id, q, sim_max, gate_open)]，gate_open=该题有 ASCII 实体且库内命中。"""
    thresholds = thresholds or [round(0.30 + 0.02 * i, 2) for i in range(26)]  # 0.30~0.80
    lines = []
    for thr in thresholds:
        # 拒答判定：sim_max < thr 且 没有可命中的 ASCII 实体
        refused_neg = [x for x in neg if x[2] < thr and not x[3]]
        leaked_neg = [x for x in neg if not (x[2] < thr and not x[3])]
        refused_pos = [x for x in pos if x[2] < thr and not x[3]]
        n_neg, n_pos = len(neg), len(pos)
        lines.append({
            'thr': thr,
            'neg_refused': len(refused_neg), 'neg_leaked': len(leaked_neg),
            'refuse_rate': round(len(refused_neg) / max(n_neg, 1), 3),
            'pos_false_refuse': len(refused_pos),
            'false_refuse_rate': round(len(refused_pos) / max(n_pos, 1), 3),
        })
    return lines


def run(model_key=None):
    model_key = model_key or os.environ.get('MEM_EMBED_MODEL') or 'bge-m3-int8'
    spec = json.load(open(NEG_FILE, encoding='utf-8'))
    negatives = spec['negatives']

    # 正样本对照：直接复用 hard_holdout 的 22 题（补强后才创建、措辞不重复）
    sys.path.insert(0, HERE)
    import hard_bench as HB
    import hard_holdout as HH
    positives = [dict(id=c['id'], q=c['q'], expect=c['expect']) for c in HH.HOLDOUT]

    qs = [{'id': n['id'], 'q': n['q']} for n in negatives] + \
         [{'id': p['id'], 'q': p['q']} for p in positives]
    # ★查询结果落缓存：模型加载一次要 30s+，演示/统计层出 bug 不该重算模型
    if model_key.endswith('@cached') or os.environ.get('RB_USE_CACHE') == '1':
        model_key = model_key.replace('@cached', '')
        if not os.path.exists(RAW):
            print('缓存 %s 不存在，请先正常跑一次 run' % RAW)
            return
        data = json.load(open(RAW, encoding='utf-8'))
        el = 0.0
        print('复用缓存 %s（未加载模型）' % RAW)
    else:
        print('跑 %d 条负样本 + %d 条正样本（对照），模型 %s …' % (len(negatives), len(positives), model_key))
        t0 = time.time()
        data, err = _query_all(qs, model_key)
        if data is None:
            print('查询失败:', err)
            return
        el = time.time() - t0
        json.dump(data, open(RAW, 'w', encoding='utf-8'), ensure_ascii=False)
    m = {x['id']: x for x in data['rows']}

    rows = corpus_rows()
    blob_low = ' \n '.join(rows).lower()

    def gate_open(q):
        """查询里有 ASCII 实体、且该实体在库内出现 → 关键词路可能命中 → 不拒答。"""
        for t in _ascii_ents(q):
            if _occurs(blob_low, t):
                return True
        return False

    neg = []
    for n in negatives:
        x = m.get(n['id'], {})
        neg.append((n['id'], n['q'], x.get('sim_max', 0.0), gate_open(n['q']),
                    n['class'], (x.get('blob') or [''])[0][:70]))
    pos = []
    for p in positives:
        x = m.get(p['id'], {})
        blob = ' \n '.join(x.get('blob') or [])
        hit = any(re.search(pat, blob, re.I) for pat in p['expect'])
        pos.append((p['id'], p['q'], x.get('sim_max', 0.0), gate_open(p['q']), 'PASS' if hit else 'FAIL'))

    # ---------------- 输出 ----------------
    print('=' * 88)
    print('后端 %s   总耗时 %.1fs（含模型加载一次）' % (data['info'], el))
    print('=' * 88)

    print('\n【一】负样本 sim_max 分布（越低越容易拒答；★高的是危险项）')
    print('-' * 88)
    for cid, q, sm, g, cls, snip in sorted(neg, key=lambda x: -x[2]):
        flag = '★危险' if sm >= 0.60 else ('  偏高' if sm >= 0.50 else '  低  ')
        print('  %s %-4s sim=%.4f %-26s %s' % (flag, cid, sm, cls.split('_')[0], q[:34]))
        if sm >= 0.60:
            print('          └ 召回首条: %s' % snip.replace('\n', ' '))
    print('\n【二】正样本对照（22 题，sim_max 越低说明"本来答得出的题也可能被误拒"）')
    print('-' * 88)
    npass = sum(1 for x in pos if x[4] == 'PASS')
    print('  命中率（原判分口径）: %d/%d = %.1f%%' % (npass, len(pos), npass / len(pos) * 100))
    lows = sorted([x for x in pos], key=lambda x: x[2])[:6]
    print('  最低的 6 条 sim_max（误拒风险最高的正样本）:')
    for cid, q, sm, g, st in lows:
        print('    %-4s sim=%.4f gate=%-5s %-4s %s' % (cid, sm, 'open' if g else 'closed', st, q[:34]))

    print('\n【三】阈值扫描（判据 = sim_max < thr 且 无库内 ASCII 实体）')
    print('-' * 88)
    n_gate = sum(1 for x in neg if x[3])
    print('  注：%d/%d 条负样本 gate=open（查询含库内存在的 ASCII 实体）→ 按判据永不拒答，'
          '这是设计使然，不是缺陷。' % (n_gate, len(neg)))
    print('  %-6s %-16s %-14s %-16s' % ('thr', '拒答负样本', '漏答负样本', '误拒正样本'))
    sw = sweep([x[:4] for x in neg], [x[:4] for x in pos], blob_low)
    for s in sw:
        star = ''
        if s['false_refuse_rate'] == 0 and s['refuse_rate'] >= 0.5:
            star = '  ← 零误拒'
        print('  %-6.2f %-16s %-14s %-16s%s' % (
            s['thr'],
            '%d/%d (%.0f%%)' % (s['neg_refused'], len(neg), s['refuse_rate'] * 100),
            '%d/%d' % (s['neg_leaked'], len(neg)),
            '%d/%d (%.0f%%)' % (s['pos_false_refuse'], len(pos), s['false_refuse_rate'] * 100),
            star))

    # ---- 工作点判读：零误拒前提下拒答率最高的那档 ----
    print('-' * 88)
    ok = [s for s in sw if s['false_refuse_rate'] == 0]
    if ok:
        best = max(ok, key=lambda s: s['refuse_rate'])
        print('★零误拒工作点：thr=%.2f → 拒答 %d/%d 负样本（%.0f%%），正样本误拒 0/%d'
              % (best['thr'], best['neg_refused'], len(neg), best['refuse_rate'] * 100, len(pos)))
        print('  代价：仍有 %d 条负样本会漏答。其中 sim_max 最高的三条：'
              % best['neg_leaked'])
        for cid, q, sm, g, cls, snip in sorted([x for x in neg if not (x[2] < best['thr'] and not x[3])],
                                               key=lambda x: -x[2])[:3]:
            print('    %-4s sim=%.4f gate=%-5s %s' % (cid, sm, 'open' if g else 'closed', q[:40]))
    else:
        lo = min(sw, key=lambda s: s['false_refuse_rate'])
        print('★不存在"零误拒"工作点：最低误拒率也有 %.0f%%（thr=%.2f）。'
              % (lo['false_refuse_rate'] * 100, lo['thr']))
        print('  → 结论：**纯语义阈值不足以做拒答判据**，必须叠加其他信号（见文档建议）。')
    print('=' * 88)
    print('★读法：拒答率要尽量高（拦住不该答的），误拒率必须尽量低（别把答得出的也拒了）。')
    print('  两者不可兼得，扫描表就是让人**看着代价选工作点**，而不是拍脑袋定 0.5。')
    print('  本脚本只出数、不改代码 —— 是否落进生产由人决定。')

    json.dump({
        'model': model_key, 'info': data['info'], 'elapsed_s': round(el, 1),
        'negatives': [{'id': c, 'q': q, 'sim_max': s, 'gate': g, 'class': k, 'top1': t}
                      for c, q, s, g, k, t in neg],
        'positives': [{'id': c, 'q': q, 'sim_max': s, 'gate': g, 'status': st} for c, q, s, g, st in pos],
        'sweep': sw,
    }, open(RESULT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('结果已写入 %s' % RESULT)


def cover_run():
    """【替代判据实测】实体覆盖法 —— 纯语义阈值鉴别力不足时的第二条路。

    判据不是"分数低"，而是"问题里的关键信息，召回文本里到底有没有"。
    关键信息用**字符二元组**近似（中文免分词），停用二元组由**库内文档频次自动筛出**
    （出现在 >30% 文档里的二元组没有鉴别力），不手写停用词表。
    零模型成本：直接读 refuse_bench_raw.json。
    """
    if not os.path.exists(RAW):
        print('缺少 %s，请先跑一次 run' % RAW)
        return
    raw = json.load(open(RAW, encoding='utf-8'))
    rows = corpus_rows()
    docs = [r for r in rows if r.strip()]

    # 文档频次 → 自动停用二元组
    df = {}
    for d in docs:
        seen = set(_bigrams(d))
        for b in seen:
            df[b] = df.get(b, 0) + 1
    n_doc = len(docs)
    STOP = {b for b, c in df.items() if c > 0.30 * n_doc}

    def toks(q):
        return [b for b in _bigrams(q) if b not in STOP]

    m = {x['id']: x for x in raw['rows']}
    spec = json.load(open(NEG_FILE, encoding='utf-8'))
    sys.path.insert(0, HERE)
    import hard_holdout as HH
    qmap = {n['id']: n['q'] for n in spec['negatives']}
    qmap.update({c['id']: c['q'] for c in HH.HOLDOUT})

    print('=' * 88)
    print('实体覆盖法（自动停用二元组 %d 个 / 全库 %d 个）' % (len(STOP), len(df)))
    print('=' * 88)

    def cov(cid):
        x = m.get(cid)
        q = qmap.get(cid, '')
        if not x or not q:
            return None
        tk = toks(q)
        if not tk:
            return None
        blob = ' '.join(x.get('blob') or [])
        bl = set(_bigrams(blob))
        hit = sum(1 for t in tk if t in bl)
        return cid, q, x.get('sim_max', 0.0), hit, len(tk), hit / len(tk)

    neg = [c for c in (cov(n['id']) for n in spec['negatives']) if c]
    pos = [c for c in (cov(p['id']) for p in HH.HOLDOUT) if c]

    print('\n【负样本】按覆盖率降序（越低越该拒答）')
    print('-' * 88)
    for cid, q, sm, h, n, r in sorted(neg, key=lambda z: -z[5]):
        print('  cover=%.2f (%d/%d) sim=%.4f %-4s %s' % (r, h, n, sm, cid, q[:38]))

    print('\n【正样本对照】按覆盖率升序（越低越可能被误拒）')
    print('-' * 88)
    for cid, q, sm, h, n, r in sorted(pos, key=lambda z: z[5])[:8]:
        print('  cover=%.2f (%d/%d) sim=%.4f %-4s %s' % (r, h, n, sm, cid, q[:38]))

    print('\n【覆盖率阈值扫描】判据 = cover < thr')
    print('-' * 88)
    print('  %-6s %-18s %-16s' % ('thr', '拒答负样本', '误拒正样本'))
    for i in range(11):
        thr = round(i * 0.1, 2)
        rn = [x for x in neg if x[5] < thr]
        rp = [x for x in pos if x[5] < thr]
        star = '  ← 零误拒' if (not rp and len(rn) >= len(neg) * 0.5) else ''
        print('  %-6.2f %-18s %-16s%s' % (
            thr, '%d/%d (%.0f%%)' % (len(rn), len(neg), len(rn) / len(neg) * 100),
            '%d/%d (%.0f%%)' % (len(rp), len(pos), len(rp) / len(pos) * 100), star))
    ok = [round(i * 0.1, 2) for i in range(11) if not [x for x in pos if x[5] < round(i * 0.1, 2)]]
    if ok:
        best = max(ok)
        rn = [x for x in neg if x[5] < best]
        print('-' * 88)
        print('★零误拒工作点：cover_thr=%.2f → 拒答 %d/%d 负样本（%.0f%%），误拒 0/%d'
              % (best, len(rn), len(neg), len(rn) / len(neg) * 100, len(pos)))

    # ---- 联合判据：cover < ct AND sim < st（两个条件同时成立才拒答）----
    print('\n【联合判据】cover < ct  且  sim < st（同时成立才拒）')
    print('-' * 88)
    print('  %-8s %-8s %-18s %-16s' % ('ct', 'st', '拒答负样本', '误拒正样本'))
    best_combo = None
    for ci in range(3, 7):          # ct = 0.30 ~ 0.60
        for si in range(4, 11):     # st = 0.64 ~ 0.76
            ct, st = round(ci * 0.1, 2), round(0.60 + si * 0.02, 2)
            rn = [x for x in neg if x[5] < ct and x[2] < st]
            rp = [x for x in pos if x[5] < ct and x[2] < st]
            if rp:
                continue
            if best_combo is None or len(rn) > best_combo[2]:
                best_combo = (ct, st, len(rn), len(rp))
    if best_combo:
        ct, st, nrn, nrp = best_combo
        print('  %-8.2f %-8.2f %-18s %-16s' % (
            ct, st, '%d/%d (%.0f%%)' % (nrn, len(neg), nrn / len(neg) * 100),
            '%d/%d (0%%)' % (nrp, len(pos))))
        print('-' * 88)
        print('★联合判据最优零误拒工作点：cover<%.2f 且 sim<%.2f → 拒答 %d/%d 负样本（%.0f%%）'
              % (ct, st, nrn, len(neg), nrn / len(neg) * 100))
        print('  对比：纯语义 5/22 (23%%)、纯覆盖 5/22 (23%%) → 联合仅多拦 %d 条，量级未变。'
              % max(0, nrn - 5))
    else:
        print('  不存在零误拒的联合工作点。')
    print('=' * 88)
    print('★与语义阈值对照：语义法零误拒工作点只拒 5/22 (23%)；覆盖率法见上。')


def _bigrams(s):
    """字符二元组，只保留含中文/字母数字的。"""
    s = re.sub(r'\s+', '', s)
    out = []
    for i in range(len(s) - 1):
        b = s[i:i + 2]
        if re.search(r'[\u4e00-\u9fffA-Za-z0-9]', b):
            out.append(b.lower())
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'verify'
    if cmd == 'verify':
        sys.exit(1 if verify() else 0)
    elif cmd == 'run':
        run(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == 'cover':
        cover_run()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
