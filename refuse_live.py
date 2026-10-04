"""refuse_live.py —— 把离线标定的拒答判据落成**在线可调用**的一层判定。

背景（标定在 refuse_gate.py / refuse_bench.py，此处只消费结论，不重新标定）
--------------------------------------------------------------------------
零误拒最优工作点 = P4 `cov < 0.25 且 sim < 0.74`，在 22 条负样本上拦 9 条（41%），
22 条正样本零误拒。天花板 59%：负样本里 9 条 gate=open（查询里的实体在库内命中），
词形法永远拒不了。其中 N3「同形不同属性」（Clash 怎么续费 / 7-Zip 注册码）
原理上无解 —— 库里有这个工具，但没有它被问的那个属性，必须上语义蕴含判定。

设计原则（三条，都是踩过的坑）
------------------------------
1. **默认不改输出**。拒答是「跑起来不报错、但结果全错」的典型：判据一旦误判，
   读者会以为"记忆里真没有这条"，从而**不再去找**——比给错答案更糟。
   标定集的零误拒 ≠ 真实查询分布的零误拒。故本模块默认只做 **weak 标注**
   （结果照常返回，另加一行证据强度提示），真正**拒答**必须显式开启。
2. **拒答必须给依据**。只说"没有"无法证伪；必须给出 cov/sim 数值与命中的词，
   读者才能判断"这是真的没有，还是检索没做好"。
3. **必须能关**。`MEM_REFUSE=off` 一律降级为 no-op（返回 level='ok'）。

调用契约（供 mem.py / mcp_server.py 等消费）
--------------------------------------------
    from refuse_live import decide
    d = decide(query, results, sim_max=None, mode='warn'|'strict'|'off')
    d = {'level': 'ok'|'weak'|'none', 'refuse': bool, 'cov': float, 'sim': float,
         'hit': [...], 'total': int, 'note': str|None}

失败策略：任何异常一律返回 `level='ok'`（**永不因为判据本身出错而阻断检索**）。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 标定得到的两个阈值。改这两个数必须重跑 refuse_gate.py 复核零误拒是否仍成立。
COV_THR = float(os.environ.get('MEM_REFUSE_COV', '0.25'))
SIM_THR = float(os.environ.get('MEM_REFUSE_SIM', '0.74'))

# 停用二元组缓存：语料（facts + tool_assets）不变就复用，避免每次查询都全库扫一遍。
_STOP_CACHE = os.path.join(HERE, '_refuse_stop.json')

# 查询词数少于这个数时不做判定：短查询（如单个工具名）天然 cov 低，
# 判它"证据弱"属于**结构性别名**，不是真弱。（实测：「Everything」这类单词查询）
_MIN_TERMS = 2


def _corpus_sig(rows):
    """语料指纹：任一 fact/tool 文本变了就重建停用表。"""
    import hashlib
    h = hashlib.md5()
    for r in rows:
        h.update(str(r).encode('utf-8', 'ignore'))
        h.update(b'\x00')
    return h.hexdigest()


def load_stop(force=False):
    """取停用二元组集合。返回 (stop_set, n_doc)；失败返回 (set(), 0)。"""
    try:
        import refuse_bench as RB
        import refuse_gate as RG
    except Exception as e:
        sys.stderr.write('[refuse] 标定模块不可用（%s），判定降级为 no-op\n' % e)
        return set(), 0

    try:
        rows = RB.corpus_rows()
    except Exception as e:
        sys.stderr.write('[refuse] 读语料失败（%s），判定降级为 no-op\n' % e)
        return set(), 0

    sig = _corpus_sig(rows)
    if not force and os.path.exists(_STOP_CACHE):
        try:
            d = json.load(io.open(_STOP_CACHE, encoding='utf-8'))
            if d.get('sig') == sig and d.get('stop') is not None:
                return set(d['stop']), int(d.get('n', 0))
        except Exception:
            pass

    docs = [r for r in rows if str(r).strip()]
    stop, _df, n = RG.build_stop(docs)
    try:
        with io.open(_STOP_CACHE, 'w', encoding='utf-8') as f:
            json.dump({'sig': sig, 'n': n, 'stop': sorted(stop)}, f, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write('[refuse] 停用表缓存写入失败（不影响判定）：%s\n' % e)
    return stop, n


def _sim_of(results, sim_max=None):
    """取语义相似度上界。hybrid 结果带 'semantic'；LIKE 兜底路径没有 → 退化用 'score'。

    ★注意 LIKE 兜底路径的 score 恒为 1.0（子串命中即满分），量纲与语义分不同。
    这种情况下 sim 会虚高 → 判据更倾向"不拒答"，属于**安全的失败方向**。
    """
    if sim_max is not None:
        return float(sim_max or 0.0)
    best = 0.0
    for x in results or []:
        if not isinstance(x, dict):
            continue
        v = x.get('semantic')
        if v is None:
            v = x.get('score')
        try:
            v = float(v or 0.0)
        except Exception:
            v = 0.0
        if v > best:
            best = v
    return best


def _top1_text(results):
    for x in results or []:
        if isinstance(x, dict):
            t = x.get('content') or x.get('text') or ''
            if t:
                return str(t)
    return ''


def decide(query, results, sim_max=None, mode=None, stop=None):
    """对一次检索结果做证据强度判定。**永不抛异常。**

    mode: 'off' / 'warn'（默认）/ 'strict'
      off    —— 直接返回 ok，不做任何计算
      warn   —— 证据弱时返回 level='weak'（调用方只加提示，仍展示结果）
      strict —— 证据弱时返回 level='weak' 且 refuse=True（调用方应改为拒答）
    默认取环境变量 MEM_REFUSE（缺省 'warn'；'0'/'off' 视作 off）。
    """
    if mode is None:
        mode = (os.environ.get('MEM_REFUSE') or 'warn').strip().lower()
        if mode in ('0', 'off', 'false', 'no'):
            mode = 'off'
        elif mode in ('1', 'on', 'true', 'yes', 'strict'):
            mode = 'strict'
    _ok = {'level': 'ok', 'refuse': False, 'cov': 0.0, 'sim': 0.0,
           'hit': [], 'total': 0, 'note': None}
    if mode == 'off':
        return _ok

    try:
        if not results:
            d = dict(_ok)
            d.update({'level': 'none', 'refuse': True, 'sim': _sim_of(results, sim_max),
                      'note': '检索无结果（engine 退化或中枢确实没有相关记录）'})
            return d

        import refuse_bench as RB
        import refuse_gate as RG

        if stop is None:
            stop, _n = load_stop()

        top1 = _top1_text(results)
        top1_low = top1.lower()
        a_hit, a_all, c_hit, c_all = RG.evidence(
            query, top1_low, set(RB._bigrams(top1_low)), stop)
        n_hit = len(a_hit) + len(c_hit)
        n_all = len(a_all) + len(c_all)
        cov = n_hit / max(1, n_all)
        sim = _sim_of(results, sim_max)

        hit = list(a_hit) + list(c_hit)
        d = {'level': 'ok', 'refuse': False, 'cov': round(cov, 4), 'sim': round(sim, 4),
             'hit': hit, 'total': n_all, 'note': None}

        if n_all < _MIN_TERMS:
            # 词太少，cov 天然无意义 —— 不判弱（避免结构性别名）
            d['note'] = '查询词过少（%d 个），未做证据判定' % n_all
            return d

        if cov < COV_THR and sim < SIM_THR:
            d['level'] = 'weak'
            d['refuse'] = (mode == 'strict')
            d['note'] = ('证据弱：命中率 %.0f%%（<%d%%）且语义相似度 %.2f（<%.2f）'
                         '—— 库内可能没有这条，返回结果或与提问无关'
                         % (cov * 100, int(COV_THR * 100), sim, SIM_THR))
            if n_hit:
                d['note'] += '；仅命中 %s' % '、'.join(hit[:6])
            else:
                d['note'] += '；一个查询词都没在首条结果里出现'
        return d
    except Exception as e:
        sys.stderr.write('[refuse] 判定异常（已忽略，不影响检索）：%s\n' % e)
        return _ok


# ------------------------------------------------------------------ 自检
def _selftest():
    """受控演练：造正/负样本，看它**报不报**（验收口径不是"跑通了"）。

    负样本要求 level='weak'；正样本要求 level='ok'。
    注意这是**合成样本**，只验代码路径通不通，不代表标定精度。
    """
    pos = [{'content': 'memory_hub 的投影槽位上限是 3980 字符，超了会被整体截断',
            'semantic': 0.86}]
    neg = [{'content': 'eNSP 拓扑窗 1280x800，含菜单/工具栏/设备栏/状态栏',
            'semantic': 0.41}]
    q_pos = '投影槽位上限是多少字符'
    q_neg = '珠穆朗玛峰的海拔是多少米'

    cases = [('正样本(应 ok)', q_pos, pos, 'ok'),
             ('负样本(应 weak)', q_neg, neg, 'weak')]
    bad = 0
    for name, q, res, want in cases:
        d = decide(q, res, mode='warn')
        got = d['level']
        mark = 'OK ' if got == want else 'FAIL'
        if got != want:
            bad += 1
        print('  [%s] %-16s level=%-5s cov=%.2f sim=%.2f  %s'
              % (mark, name, got, d['cov'], d['sim'], (d['note'] or '')[:70]))
    # 关闭开关必须真的 no-op
    d_off = decide(q_neg, neg, mode='off')
    if d_off['level'] != 'ok' or d_off['refuse']:
        bad += 1
        print('  [FAIL] mode=off 未降级为 no-op')
    else:
        print('  [OK ] mode=off 降级为 no-op')
    # 空结果
    d_e = decide(q_neg, [], mode='strict')
    if d_e['level'] != 'none' or not d_e['refuse']:
        bad += 1
        print('  [FAIL] 空结果未判 none/refuse')
    else:
        print('  [OK ] 空结果判 none/refuse')
    print('★自检%s（%d 项失败）' % ('通过' if not bad else '未通过', bad))
    return 1 if bad else 0


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    if '--stop' in sys.argv:
        s, n = load_stop(force='--force' in sys.argv)
        print('语料 %d 篇 · 停用二元组 %d 个 · 缓存 %s' % (n, len(s), _STOP_CACHE))
        sys.exit(0)
    print(__doc__)
