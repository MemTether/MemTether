# -*- coding: utf-8 -*-
"""qvalue_ab.py —— Q-Value 开/关 A/B 对照（验收台）

【为什么单独一个脚本，而不是改 hard_bench.py】
  `hard_bench._query_all()` 在子进程里跑 `search_hybrid(..., decay=False)`，
  子进程 env = `dict(os.environ)` 直接继承父进程环境。
  所以只要在调用前设 `MEM_QVALUE=0` / `1`，就能在**不改验收台、不重建索引**
  的前提下做开/关对照 —— 保证对照两侧跑的是同一份代码、同一个索引。

【判据】
  开 Q-Value 后 hard_bench 分数**不得下降**。

【预期（可被证伪的强断言）】
  全库 `q_value` 默认 0.5 → 检索因子 `(0.3 + 0.7*0.5) = 0.65`，
  对同一批候选是**同一个常数因子**，在 RRF → min-max 精排链路里会被完全抵消
  → 逐题 Top10 内容序列应**完全一致**、PASS/FAIL 逐题一致。
  若真一致，"不下降"就是最强形式成立；若不一致 → 注入点有副作用，必须查。

【前置条件】
  · 需要一个可检索的库：默认取 `$MEM_DB`，否则本目录 `memory.db`。
    没有真实库时先跑 `python scripts/make_demo_db.py` 再 `export MEM_DB=demo/memory_demo.db`。
  · 题集真源是 `hard_bench.CASES`（本仓库自带）。**题集不随库变化** ——
    若库里没有题目所指的资产，分数本来就低，那是数据问题不是 Q-Value 问题。

【用法】
  python qvalue_ab.py            # off / on 两轮并对比
  python qvalue_ab.py --save     # 额外落盘 json 供留档
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import hard_bench as hb   # noqa: E402
except ImportError as e:
    print('★ 找不到 hard_bench.py（题集真源）：%s' % e)
    print('  本脚本不内置题集 —— 题集是评测口径的一部分，必须来自仓库里的 hard_bench.py。')
    print('  请确认你在仓库根目录下运行本脚本（当前 HERE=%s）。' % HERE)
    sys.exit(2)


def _run_flag(flag, model_key):
    """设好 MEM_QVALUE 后跑全量查询。返回 (data, err, secs)。"""
    os.environ['MEM_QVALUE'] = flag
    t0 = time.time()
    data, err = hb._query_all(model_key)
    return data, err, round(time.time() - t0, 1)


def main():
    save = '--save' in sys.argv
    model_key = os.environ.get('MEM_EMBED_MODEL') or 'bge-m3'
    print('=' * 84)
    print('Q-Value 开/关 A/B 对照   模型=%s   题数=%d' % (model_key, len(hb.CASES)))
    print('=' * 84)

    got = {}
    for flag in ('0', '1'):
        data, err, secs = _run_flag(flag, model_key)
        if data is None:
            print('MEM_QVALUE=%s 查询失败: %s' % (flag, err))
            return 1
        res = hb.score(data['rows'])
        npass = sum(1 for _, s, _ in res if s == 'PASS')
        got[flag] = {'data': data, 'res': res, 'npass': npass, 'secs': secs}
        print('  MEM_QVALUE=%s   得分 %2d/%2d = %5.1f%%   （用时 %ss）'
              % (flag, npass, len(res), npass / len(res) * 100, secs))

    off, on = got['0'], got['1']
    print()
    print('-' * 84)
    print('判据 1：分数不得下降')
    delta = on['npass'] - off['npass']
    verdict = 'OK' if delta >= 0 else '★下降！必须回退'
    print('  关闭 %d/%d  →  开启 %d/%d   Δ=%+d   [%s]'
          % (off['npass'], len(off['res']), on['npass'], len(on['res']), delta, verdict))

    print('判据 2：逐题 PASS/FAIL 是否翻转')
    flips = [(off['res'][i][0], off['res'][i][1], on['res'][i][1])
             for i in range(len(off['res'])) if off['res'][i][1] != on['res'][i][1]]
    if flips:
        for cid, a, b in flips:
            print('  ★%s  %s → %s' % (cid, a, b))
    else:
        print('  无翻转（%d 题全部一致）' % len(off['res']))

    print('判据 3：逐题 Top10 内容序列是否变化（最强等价性检验）')
    m_on = {x['id']: x for x in on['data']['rows']}
    diffs = []
    for x in off['data']['rows']:
        y = m_on.get(x['id']) or {}
        if x['blob'] != y.get('blob'):
            diffs.append(x['id'])
    if diffs:
        print('  %d 题排序/内容有变化：%s' % (len(diffs), ', '.join(diffs[:20])))
    else:
        print('  全部 %d 题 Top10 内容序列逐字节一致 → Q-Value 在 q 全默认时零副作用'
              % len(off['data']['rows']))

    print()
    if delta >= 0 and not flips:
        print('结论：Q-Value 接入**未使验收台下降**。')
    else:
        print('结论：存在差异，需人工复核上方清单。')

    if save:
        tag = time.strftime('%Y%m%d-%H%M%S')
        p = os.path.join(HERE, 'qvalue_ab_%s.json' % tag)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'model': model_key, 'off_npass': off['npass'], 'on_npass': on['npass'],
                       'total': len(off['res']),
                       'off': [list(x) for x in off['res']],
                       'on': [list(x) for x in on['res']],
                       'blob_diffs': diffs}, f, ensure_ascii=False, indent=2)
        print('已落盘 %s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
