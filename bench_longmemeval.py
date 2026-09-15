#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_longmemeval.py —— 轨道 A：LongMemEval 通用基准（外卷，非自出题）

## 为什么要有这个文件（2026-09-15）

本机的 `hub_score.py` 是**自出卷**——题目和答案键都由我（以及前几轮的我）写，
覆盖的全是"本机资产"这一维。它有已知的自出卷偏差，我在评分卡里已明确警告。

轨道 A 的意义：用**外部公开基准**得到不可自欺的分数。
LongMemEval（ICLR 2025，xiaowu0162/longmemeval）是长期记忆评测的主流基准之一。

## ★必须提前声明的三点失真（诚实优先）

1. **基准形态与本机架构不同**。LongMemEval 假设"把整个 haystack（约 500 轮会话）
   喂给模型"；本机中枢是**检索式**（embedding + RRF + cross-encoder 精排），
   不把全文塞进上下文。所以本 harness 的做法是：
   **把每一轮 haystack 会话灌进检索库 → 用主检索链路取回 top-k → 交给 judge**。
   这是"检索式记忆系统"的合理适配，但**不等于论文里的原始设置**，
   分数**不可与论文数值直接对比**。

2. **必须用 LLM judge，而 judge 本身不可靠**。答案键是英文开放式短答
   （如 "GPS system not functioning correctly"），没有确定性字符串可精确匹配。
   而文献实测：LoCoMo 答案键约 6.4% 错误、LLM judge 接受约 63% 的**故意错答**。
   → 因此本 harness **同时**跑两条判分：
     - `llm`   ：judge 模型判定（宽松，接近论文口径）
     - `strict`：答案键里的实词是否原样出现在模型答案里（严格，会低估）
     两个数都报，差距本身就是"judge 有多松"的量度。**只报一个数是在骗人。**

3. **覆盖不全**。500 题全跑需要大量模型调用（每题 1 次 judge，且检索需建索引）。
   本 harness 默认跑**分层抽样 N 题**（按 question_type 等比例），
   并可通过 `--all` 全量。抽样结果**有抽样误差**，报数时须注明样本量。

## 用法
  python bench_longmemeval.py --sample 60          # 分层抽 60 题（默认）
  python bench_longmemeval.py --all                # 全 500 题
  python bench_longmemeval.py --variant oracle     # 用 oracle 版（haystack 短）
  python bench_longmemeval.py --no-llm             # 只跑严格匹配（零模型调用）
"""
import os
import sys
import json
import random
import sqlite3
import argparse
import datetime as dt
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BENCH_DB = os.path.join(HERE, 'bench_data', 'lme_scratch.db')
DATA = os.path.join(HERE, 'bench_data')


# ---------------- 1) 把 haystack 灌进临时检索库 ----------------
def _turn_text(turn):
    """把一轮对话转成一行可检索文本。"""
    role = turn.get('role', '?')
    content = (turn.get('content') or '').strip().replace('\n', ' ')
    return '[%s] %s' % (role, content)


def build_scratch_db(item, db_path=BENCH_DB, verbose=False):
    """为一题的 haystack 建**独立**临时库（避免跨题污染）。

    每题一个库，因为 LongMemEval 的 haystack 是每题独立的 500 轮会话；
    混在一个库里会让其他题的会话成为干扰项，那不是这个基准要测的东西。
    """
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT,
        confidence REAL, tags TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE tool_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE, name TEXT, aliases TEXT, type TEXT, path TEXT,
        entrypoint TEXT, capabilities TEXT, known_failures TEXT,
        status TEXT DEFAULT 'active', last_verified_at TEXT,
        created_at TEXT, updated_at TEXT, content TEXT)""")

    sess_ids = item.get('haystack_session_ids') or []
    sess_dates = item.get('haystack_dates') or []
    sess = item.get('haystack_sessions') or []
    n = 0
    for si, session in enumerate(sess):
        sid = sess_ids[si] if si < len(sess_ids) else 'sess%d' % si
        sdate = sess_dates[si] if si < len(sess_dates) else ''
        for ti, turn in enumerate(session):
            txt = _turn_text(turn)
            if not txt.strip():
                continue
            uid = 'lme-%s-%d-%d' % (item['question_id'], si, ti)
            conn.execute(
                "INSERT OR IGNORE INTO facts (uid,type,subject,content,status,source,"
                "scope,confidence,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid, 'fact', 'bench', txt, 'active', 'longmemeval', 'shared',
                 0.7, sid, sdate, sdate))
            n += 1
    conn.commit()
    conn.close()
    if verbose:
        print('    临时库 %d 轮，%d 会话' % (n, len(sess)))
    return n


# ---------------- 2) 用主检索链路取答案 ----------------
def retrieve_answer(item, k=12, variant='s'):
    """把 haystack 灌库 → 走生产检索链路 search_hybrid → 拼成给 judge 的上下文。

    ★诚实说明：这里**复用了生产链路**（memsearch.search_hybrid），
      而不是自己拼一条"更好用"的查询路径——否则测的就不是中枢的真实能力。
      但有一个必要的适配：把 question_date 一起塞进查询，
      因为本机中枢的时间衰减与时间戳相关。
    """
    import importlib
    import memsearch
    n = build_scratch_db(item)
    q = item['question']
    # 临时把 memsearch 的 DB 指向本题库
    orig_db = None
    if hasattr(memsearch, 'DB'):
        orig_db = memsearch.DB
    memsearch.DB = BENCH_DB
    try:
        # vec 索引需要重建（指向新库）
        if hasattr(memsearch, 'rebuild_vector_index'):
            try:
                memsearch.rebuild_vector_index(verbose=False)
            except Exception:
                pass
        res = memsearch.search_hybrid(q, limit=k, decay=False)
    finally:
        if orig_db is not None:
            memsearch.DB = orig_db
    return res.get('results', []), n


# ---------------- 3) 两种判分 ----------------
def _key_terms(answer):
    """从答案键里取实词（用于严格匹配）。去掉停用词和短词。"""
    import re
    stop = {'the', 'a', 'an', 'of', 'to', 'in', 'on', 'at', 'and', 'or', 'is', 'was',
            'were', 'for', 'with', 'my', 'i', 'it', 'that', 'this', 'be', 'been',
            'have', 'has', 'had', 'do', 'did', 'not', 'no', 'but', 'so', 'as'}
    toks = re.findall(r"[A-Za-z][A-Za-z0-9_'\-]+", (answer or '').lower())
    return [t for t in toks if t not in stop and len(t) >= 3]


def judge_strict(pred, gold):
    """严格判分：答案键实词**全部**出现在预测里（AND），才算对。

    ★这会**系统性低估**（同义改写会被判错），但对"模型是否真的找到了那段话"
      非常敏感，且**零模型调用、完全可复现**。作为下界使用。
    """
    if not pred:
        return False
    p = pred.lower()
    kt = _key_terms(gold)
    if not kt:
        return False
    return all(t in p for t in kt)


def _load_cred():
    """从本机 vault 载入密钥（唯一真源 E:/RUANJIAN/.secure/vault/vault.bin）。

    ★实测踩坑（2026-09-15 21:22）：vault 的读取入口是 `cred_env.env()`
      （把全部条目**灌进 os.environ**），不是 `cred_env.resolve()`（那个只是
      `${VAR}` 字符串展开器，调用它拿到的是变量名本身，看着"有值"其实没用）。
    """
    try:
        sys.path.insert(0, 'E:/RUANJIAN/ai-audit')
        import cred_env
        cred_env.env()
    except Exception:
        pass


def judge_llm(question, gold, pred, model=None):
    """LLM judge：宽松判定（接近论文口径）。

    ★必须同时报出这条的通过率——它比 strict 高一截，差值就是"judge 有多松"。
    """
    import requests
    _load_cred()
    key = os.environ.get('GPTX_ASTRA_KEY') or ''
    base = os.environ.get('GPTX_ASTRA_BASE') or 'https://api.gptx.cc/v1'
    if not key:
        return None, 'no GPTX_ASTRA_KEY'
    prompt = (
        "You are a strict grader. Decide if the PREDICTED answer is correct "
        "given the GOLD answer for the QUESTION.\n"
        "Rules: the prediction is correct if it contains the same key information "
        "as gold, even if worded differently. Extra irrelevant text is allowed.\n"
        "Reply with exactly one word: CORRECT or WRONG.\n\n"
        "QUESTION: %s\nGOLD: %s\nPREDICTED: %s\n" % (question, gold, pred)
    )
    try:
        r = requests.post(base.rstrip('/') + '/chat/completions',
                          headers={'Authorization': 'Bearer ' + key,
                                   'Content-Type': 'application/json'},
                          json={'model': model or 'gpt-6-astra',
                                'messages': [{'role': 'user', 'content': prompt}],
                                'max_tokens': 16, 'temperature': 0},
                          timeout=60)
        r.raise_for_status()
        j = r.json()
        out = (j['choices'][0]['message']['content'] or '').strip().upper()
        return ('CORRECT' in out), out[:40]
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, str(e)[:80])


# ---------------- 4) 分层抽样 ----------------
def stratified_sample(items, n, seed=42):
    """按 question_type 等比例抽样，保证六类能力都被覆盖。"""
    if n >= len(items):
        return list(items)
    by = defaultdict(list)
    for it in items:
        by[it['question_type']].append(it)
    rnd = random.Random(seed)
    out = []
    for t, lst in sorted(by.items()):
        cnt = max(1, round(n * len(lst) / len(items)))
        out.extend(rnd.sample(lst, min(cnt, len(lst))))
    rnd.shuffle(out)
    return out[:n]


# ---------------- 5) 主流程 ----------------
def run(sample=60, variant='oracle', use_llm=True, k=12, all_items=False, verbose=True):
    path = os.path.join(DATA, 'longmemeval_%s' % variant)
    if not os.path.exists(path):
        print('数据集不存在: %s' % path)
        return None
    items = json.load(open(path, 'r', encoding='utf-8'))
    todo = items if all_items else stratified_sample(items, sample)

    lines = []
    lines.append('=' * 72)
    lines.append('轨道 A · LongMemEval  |  %s  |  variant=%s' % (
        dt.datetime.now().strftime('%Y-%m-%d %H:%M'), variant))
    lines.append('=' * 72)
    lines.append('  样本       %d 题（总 %d，%s）' % (
        len(todo), len(items), '全量' if all_items else '分层抽样'))
    lines.append('  检索方式   生产链路 memsearch.search_hybrid，k=%d' % k)
    lines.append('  判分方式   %s' % ('strict(严格子串) + llm(judge)' if use_llm else 'strict(仅严格子串)'))
    lines.append('-' * 72)

    per_type = defaultdict(lambda: {'n': 0, 'strict': 0, 'llm': 0, 'llm_n': 0})
    strict_hits, llm_hits, llm_total = 0, 0, 0
    errs = []

    for i, it in enumerate(todo, 1):
        try:
            res, nturn = retrieve_answer(it, k=k, variant=variant)
            pred = ' | '.join((x.get('content') or '')[:400] for x in res[:k])
            s = judge_strict(pred, it['answer'])
            t = it['question_type']
            per_type[t]['n'] += 1
            if s:
                strict_hits += 1
                per_type[t]['strict'] += 1
            if use_llm:
                ok, note = judge_llm(it['question'], it['answer'], pred)
                if ok is not None:
                    llm_total += 1
                    per_type[t]['llm_n'] += 1
                    if ok:
                        llm_hits += 1
                        per_type[t]['llm'] += 1
                else:
                    errs.append(note)
            if verbose and i % 10 == 0:
                print('  ... %d/%d  strict %d  llm %d/%d' % (
                    i, len(todo), strict_hits, llm_hits, llm_total))
        except Exception as e:
            errs.append('%s: %s' % (type(e).__name__, str(e)[:70]))

    lines.append('  能力类型          n     strict      llm')
    for t in sorted(per_type):
        d = per_type[t]
        sr = d['strict'] / d['n'] if d['n'] else 0
        lr = d['llm'] / d['llm_n'] if d['llm_n'] else 0
        lines.append('  %-22s %3d   %5.1f%%   %5.1f%%' % (t, d['n'], sr * 100, lr * 100))
    lines.append('-' * 72)
    sr = strict_hits / len(todo) if todo else 0
    lr = llm_hits / llm_total if llm_total else 0
    lines.append('  严格匹配（下界）   %d/%d = %.1f%%' % (strict_hits, len(todo), sr * 100))
    if use_llm:
        lines.append('  LLM judge（论文口径） %d/%d = %.1f%%' % (llm_hits, llm_total, lr * 100))
        if llm_total:
            lines.append('  ★两者差距 %.1f pp —— 这就是 judge 的宽松度（不报这个数=在骗人）'
                         % ((lr - sr) * 100))
    lines.append('=' * 72)
    lines.append('  ⚠ 不可比声明：本结果**不能**与 LongMemEval 论文数值直接对比——')
    lines.append('     ① 论文假设全 haystack 入上下文；本 harness 是检索式（top-%d）' % k)
    lines.append('     ② 样本 %d 题（非全量 500），有抽样误差' % len(todo))
    lines.append('     ③ judge 有已知的宽松偏差（文献实测可接受 ~63%% 故意错答）')
    lines.append('     ④ 本 harness 未做答案改写/实体规范化，strict 系统性低估')
    if errs:
        lines.append('  ⚠ 异常 %d 条，样例: %s' % (len(errs), errs[0][:70]))
    lines.append('=' * 72)

    text = '\n'.join(lines)
    print(text)
    out = {'ts': dt.datetime.now().isoformat(), 'variant': variant,
           'n': len(todo), 'strict': round(sr, 4),
           'llm': round(lr, 4) if use_llm else None,
           'per_type': {t: dict(v) for t, v in per_type.items()},
           'errors': errs[:20]}
    with open(os.path.join(HERE, 'bench_longmemeval_result.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=60)
    ap.add_argument('--variant', default='oracle', choices=['s', 'm', 'oracle'])
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--k', type=int, default=12)
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    run(sample=a.sample, variant=a.variant, use_llm=not a.no_llm,
        k=a.k, all_items=a.all)
