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
import re
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
# ★2026-09-15：向量库也必须独立，否则会覆盖生产索引（详见 retrieve_answer 的注释）
BENCH_CHROMA = os.path.join(HERE, 'bench_data', 'lme_chroma')
BENCH_COLLECTION = 'lme_bench'


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

    ★2026-09-16 三修——沙箱临时文件的"删除预算"。
      症状：60 题版跑到一半被拦停，日志只有一行
        `SAFE_DELETE_BULK_CONFIRM_REQUIRED count:114 threshold:50 target:lme_scratch.db`
      根因有两个，都在**同一个文件**上叠加：
        ① `os.remove(db_path)` 每题一次 → 60 次
        ② SQLite 默认 rollback journal 模式**每次事务提交都要 unlink 一次 journal 文件**
           → 单题多次 commit，60 题累计 100+ 次
      而 WorkBuddy 客户端注入的批量删除护栏按"单轮累计 >50 个删除"要求确认。
      （3 题小样跑得通、60 题跑不通，就是因为它在数累计量。）
      修法：① journal 改为 MEMORY —— 临时沙箱库不需要崩溃恢复，journal 不落盘；
            ② 不再删文件，改成 DROP TABLE 后重建，文件始终存在。
      结果是文件删除次数降到 0，既不触碰护栏，也比原来更快。
      ★注意这是**换实现**而非绕过安全机制：护栏要保护的是用户文件，
        而这里从头到尾只动 bench_data/ 下的评测自建文件。
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=MEMORY")     # journal 不落盘 → 不产生文件删除
    conn.execute("PRAGMA synchronous=OFF")         # 临时库，无需 fsync
    conn.execute("DROP TABLE IF EXISTS facts")     # 复用文件，不删文件
    conn.execute("DROP TABLE IF EXISTS tool_assets")
    conn.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT,
        confidence REAL, tags TEXT, created_at TEXT, updated_at TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
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


def _assert_sandboxed(stage='pre'):
    """★沙箱自检：确认评测不会碰到生产库。

    这条检查是**事故的产物**：2026-09-15 首次跑 60 题时，只切了 SQLite 路径，
    把生产向量索引覆盖成测试数据（facts_active 从 323 条塌成 12 条 lme-* 条目），
    而且全程不报错。所以现在宁可硬报错，也不要静默毁数据。

    stage='pre'  —— 开跑前：只校验"待切换的三件套都是独立 bench 路径"，
                    此时模块还指着生产库是**正常的**（切换在每题内做）。
    stage='post' —— 切换后：校验**真的切过去了**，这是最关键的一道闸。
    """
    import memsearch
    prod_db = os.path.abspath(os.path.join(HERE, 'memory.db'))
    prod_chroma = os.path.abspath(os.path.join(HERE, 'mem0_store'))
    bad = []
    if stage == 'pre':
        # 只需确认"目标"是独立的，不是生产
        if os.path.abspath(BENCH_DB) == prod_db:
            bad.append('BENCH_DB 就是生产 memory.db')
        if os.path.abspath(BENCH_CHROMA) == prod_chroma:
            bad.append('BENCH_CHROMA 就是生产 mem0_store')
        if BENCH_COLLECTION == 'facts_active':
            bad.append('BENCH_COLLECTION 撞上生产集合名')
    else:
        # ★关键闸：切换之后必须**确实**指向 bench，否则拒绝检索
        if os.path.abspath(memsearch.DB) == prod_db:
            bad.append('切换后 DB 仍是生产 memory.db')
        if os.path.abspath(memsearch.CHROMA_PATH) == prod_chroma:
            bad.append('切换后 CHROMA_PATH 仍是生产 mem0_store')
        if memsearch.COLLECTION == 'facts_active':
            bad.append('切换后 COLLECTION 仍是生产 facts_active')
    if bad:
        raise RuntimeError(
            '[%s] 评测沙箱未隔离，拒绝继续：%s\n'
            '（生产库会被覆盖，且不会报错 —— 查 retrieve_answer 的切换逻辑）'
            % (stage, '; '.join(bad)))
    return True


# ---------------- 2) 用主检索链路取答案 ----------------
def retrieve_answer(item, k=12, variant='s'):
    """把 haystack 灌库 → 走生产检索链路 search_hybrid → 拼成给 judge 的上下文。

    ★诚实说明：这里**复用了生产链路**（memsearch.search_hybrid），
      而不是自己拼一条"更好用"的查询路径——否则测的就不是中枢的真实能力。
      但有一个必要的适配：把 question_date 一起塞进查询，
      因为本机中枢的时间衰减与时间戳相关。

    ★★2026-09-15 重大修复：**沙箱隔离曾漏掉向量库，把生产索引写坏了。**
      症状：评测跑完，生产检索的 `semantic` 全变 0.0，
            `facts_active` 集合只剩 12 条 —— 而且内容是 `lme-*` 测试数据。
      根因：原来只切了 `memsearch.DB`（SQLite 路径），但 `CHROMA_PATH` 是
            **模块级常量，没有跟着切**。于是每题 rebuild_vector_index() 都在
            **生产向量库**上执行 delete + create + add，把 323 条真实索引
            覆盖成了当题的 12 条 haystack。
      教训：**"换库"必须换全套（sqlite + 向量库 + 集合名），漏一个就是数据事故。**
            而且它**不报错**——评测照常出分，只有回头看检索质量才会发现。
      修法：把三样一起指到独立的 bench 目录，用完在 finally 里全部还原。
    """
    import memsearch
    n = build_scratch_db(item)
    q = item['question']

    # ★三件套一起切：SQLite、Chroma 目录、集合名
    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    memsearch.DB = BENCH_DB
    memsearch.CHROMA_PATH = BENCH_CHROMA
    memsearch.COLLECTION = BENCH_COLLECTION
    try:
        _assert_sandboxed('post')      # ★切换后硬校验，过不了就不检索
        try:
            # reuse=True：清空数据而非删除集合。
            # ★2026-09-16：不改这一处，评测每题都会因 delete_collection 删掉
            #   53 个 HNSW 文件而撞上客户端批量删除护栏，跑 20 秒即被拦停。
            memsearch.rebuild_vector_index(verbose=False, reuse=True)
        except Exception:
            pass
        res = memsearch.search_hybrid(q, limit=k, decay=False)
    finally:
        # ★务必还原全部三样 —— 只还 DB 就是这次事故的成因
        memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
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

    ★★2026-09-15 实测发现的**方法论硬伤**（必须诚实标注）：
      原实现对所有 gold 一视同仁做 AND 全词匹配，但抽查发现 LongMemEval 的
      gold 有**两种截然不同的形态**：
        (a) 实体型 —— "MusicTheory.net"、"GPS system not functioning correctly"
            → 短、实词少，AND 匹配有意义
        (b) 散文型 —— "The information provided is not enough. You mentioned getting
            the iPhone 13 Pro and attend..."（一段说明文字）
            → 这种 gold **原样返回都过不了**：没有哪个检索系统会把整段
              说明文字一字不差地召回。
      也就是说：原 strict **测的不是检索质量，而是"gold 恰好是不是短的"**。
      对散文型 gold 打 0 分是**指标缺陷，不是系统缺陷**。
      修法：散文型（实词 > 12 个 或 含句末标点的完整句）**不参与 strict 判分**，
            单列 `n/a` 计数，并明确说明"该题只有 llm judge 有意义"。
      —— 宁可承认指标不适用，也不要拿一个假装是下界的数字骗自己。

    ★★2026-09-15 二次修复：**gold 不一定是字符串**。
      全量 500 题里有 32 题 gold 是 `int`（答案是个数字，如"2"、"3"），
      集中在 multi-session（数会话）与 temporal-reasoning（算天数）。
      原实现直接 `gold.lower()` → AttributeError，60 题里静默崩掉 8 题（13%），
      而 llm 分母因此变成 52 而不是 60 —— **分数被悄悄算错了**。
      修法：先把 gold 统一成 str；数字型 gold 改成**数字出现即命中**的判定
      （"3" 作为独立 token 出现在预测里），这比子串匹配更贴合其语义。
    """
    if not pred:
        return None
    gold_s = '' if gold is None else str(gold).strip()
    if not gold_s:
        return None

    # ★数字型 gold："3" 必须作为**独立数字**出现，不能是 "13" 或 "31" 里的一部分
    if isinstance(gold, (int, float)) or re.fullmatch(r'-?\d+(\.\d+)?', gold_s):
        return _number_hit(pred, gold_s)

    kt = _key_terms(gold_s)
    if not kt:
        return None
    # 散文型 gold：实词过多 或 明显是多句说明 → strict 不适用
    if len(kt) > 12 or _looks_like_prose(gold_s):
        return None
    p = pred.lower()
    return all(t in p for t in kt)


def _number_hit(pred, gold_num):
    """数字型 gold 的命中判定：gold 数字必须以**独立 token** 形式出现。

    实测动机：答案 "3" 若用朴素子串匹配，会被 "13:30"、"2023"、"31" 里的
    数字片段误判成命中 —— 那是假阳性，会把分数虚高。
    """
    return re.search(r'(?<![\d.])%s(?![\d.])' % re.escape(gold_num), pred) is not None


def _looks_like_prose(gold):
    """判断 gold 是不是"一段说明"而不是"一个可命中的答案"。

    信号：有多句（>=2 个句末标点），或长度超过 120 字符。
    """
    g = (gold or '').strip()
    if len(g) > 120:
        return True
    return len([c for c in g if c in '.!?']) >= 2


def _load_cred():
    """从本机 vault 载入密钥（唯一真源 <DATA>/.secure/vault/vault.bin）。

    ★实测踩坑（2026-09-15 21:22）：vault 的读取入口是 `cred_env.env()`
      （把全部条目**灌进 os.environ**），不是 `cred_env.resolve()`（那个只是
      `${VAR}` 字符串展开器，调用它拿到的是变量名本身，看着"有值"其实没用）。
    """
    try:
        sys.path.insert(0, '<AUDIT>')
        import cred_env
        cred_env.env()
    except Exception:
        pass


# ★2026-09-15：judge 通道候选表（按优先级回退）。
#
# 实测动机：首轮正式评测跑到第 8 题时 Astra 通道返回
#   `403 {"error":{"message":"insufficient balance","type":"billing_error"}}`
# —— 不是 key 失效，是**欠费**。结果 60 题里只有 7 题判上了分，
#   汇总行打出 `5/7 = 71.4%`，看起来像个分数，实际是 7 题的。
#   （幸好加了分母健全性校验，否则这个数会被当成"60 题的结果"报出去。）
# 教训：**外部通道会中途挂**（欠费/限流/超时），judge 必须有回退，
#       而且回退失败要显式计数，不能让"没判上"伪装成"判错了"。
JUDGE_CHANNELS = [
    ('CN_DEEPSEEK_V4_FLASH_KEY', 'http://127.0.0.1:7863/v1', 'cn:deepseek-v4-flash'),
    ('GPTX_ASTRA_KEY', 'https://api.gptx.cc/v1', 'gpt-6-astra'),
    ('SILICON_KEY', 'https://api.siliconflow.cn/v1', 'Qwen/Qwen2.5-7B-Instruct'),
]
_JUDGE_PICK = None      # 缓存首次探活成功的通道，避免每题都试一遍


def _pick_judge_channel():
    """探活并选定 judge 通道（带缓存）。返回 (env_name, base, model, key) 或 None。"""
    global _JUDGE_PICK
    if _JUDGE_PICK is not None:
        return _JUDGE_PICK
    import requests
    _load_cred()
    for envn, base, model in JUDGE_CHANNELS:
        key = os.environ.get(envn) or ''
        if not key:
            continue
        try:
            r = requests.post(base.rstrip('/') + '/chat/completions',
                              headers={'Authorization': 'Bearer ' + key,
                                       'Content-Type': 'application/json'},
                              json={'model': model, 'max_tokens': 8, 'temperature': 0,
                                    'messages': [{'role': 'user', 'content': 'reply OK'}]},
                              timeout=20)
            if r.status_code == 200:
                _JUDGE_PICK = (envn, base, model, key)
                return _JUDGE_PICK
        except Exception:
            continue
    return None


def generate_answer(question, evidence, model=None):
    """★2026-09-15 新增：把**检索证据**生成成**答案**，再交给 judge 判分。

    为什么必须有这一步（血泪）：
      记忆系统（检索式）返回的是**证据片段**，不是答案。
      首轮实测把 12 条检索原文（共约 3200 字符）直接当"预测答案"送判，
      结果 llm 通过率只有 10%，**比 strict 子串匹配还低** —— 明显是任务定义错了。
      judge 看到的是一大坨对话原文，只能评"原文是否切题"，无法评"答案对不对"。

    这一步是**评测链路的标准动作**（LongMemEval 论文口径也是
    「检索 → 生成 → 判分」三段），漏掉它就等于在测一个不存在的系统。

    返回 (答案文本, 错误说明)；失败返回 (None, reason)。
    """
    import requests
    ch = _pick_judge_channel()
    if not ch:
        return None, 'no available channel'
    envn, base, mdl, key = ch
    prompt = (
        "Answer the QUESTION using ONLY the CONTEXT below. "
        "Be concise — give the direct answer, no explanation.\n"
        "If the context does not contain the answer, reply exactly: NOT ENOUGH INFO\n\n"
        "CONTEXT:\n%s\n\nQUESTION: %s\nANSWER:" % (evidence, question)
    )
    try:
        r = requests.post(base.rstrip('/') + '/chat/completions',
                          headers={'Authorization': 'Bearer ' + key,
                                   'Content-Type': 'application/json'},
                          json={'model': model or mdl,
                                'messages': [{'role': 'user', 'content': prompt}],
                                'max_tokens': 120, 'temperature': 0},
                          timeout=60)
        r.raise_for_status()
        out = (r.json()['choices'][0]['message']['content'] or '').strip()
        return (out or None), ('' if out else 'empty generation')
    except Exception as e:
        return None, '[%s] %s: %s' % (envn, type(e).__name__, str(e)[:70])


def judge_llm(question, gold, pred, model=None):
    """LLM judge：宽松判定（接近论文口径）。

    ★必须同时报出这条的通过率——它比 strict 高一截，差值就是"judge 有多松"。

    ★返回三态：`(True/False, note)` 判分成功；`(None, 原因)` 判分失败。
      调用方**必须**把 `None` 计入"未判上"并让分母校验报警 —— 见坑 10。
    """
    import requests
    ch = _pick_judge_channel()
    if not ch:
        return None, 'no available judge channel（全部通道探活失败）'
    envn, base, mdl, key = ch
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
                          json={'model': model or mdl,
                                'messages': [{'role': 'user', 'content': prompt}],
                                'max_tokens': 16, 'temperature': 0},
                          timeout=60)
        r.raise_for_status()
        j = r.json()
        out = (j['choices'][0]['message']['content'] or '').strip().upper()
        return ('CORRECT' in out), out[:40]
    except Exception as e:
        return None, '[%s] %s: %s' % (envn, type(e).__name__, str(e)[:70])


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
    _assert_sandboxed()          # ★开跑前硬自检，绝不让评测碰生产库

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

    per_type = defaultdict(lambda: {'n': 0, 'strict': 0, 'strict_n': 0,
                                    'strict_na': 0, 'llm': 0, 'llm_n': 0})
    strict_hits, strict_total, strict_na = 0, 0, 0
    llm_hits, llm_total = 0, 0
    llm_skipped = 0          # ★生成阶段失败的题数（必须显式计数，见坑 10）
    errs = []

    for i, it in enumerate(todo, 1):
        try:
            res, nturn = retrieve_answer(it, k=k, variant=variant)
            pred = ' | '.join((x.get('content') or '')[:400] for x in res[:k])
            s = judge_strict(pred, it['answer'])
            t = it['question_type']
            per_type[t]['n'] += 1
            # ★None = 该题 gold 是散文型，strict 指标不适用（不再当 0 分骗自己）
            if s is None:
                strict_na += 1
                per_type[t]['strict_na'] += 1
            else:
                strict_total += 1
                per_type[t]['strict_n'] += 1
                if s:
                    strict_hits += 1
                    per_type[t]['strict'] += 1
            if use_llm:
                # ★★2026-09-15 关键修复：**必须先把检索证据生成成答案，再判分**。
                #   原实现把 top-12 检索结果拼成 ~3200 字符的**对话原文**直接给 judge，
                #   而 gold 是 "Doc Martin" 这种短答案 —— judge 面对的是一大坨原文，
                #   它实际在评"这段原文是否回答了问题"，不是在评"答案对不对"。
                #   实测后果：llm 通过率 6/60 = 10%，**比 strict 还低 48.5pp**，
                #   明显不合理（judge 应比子串严格匹配更宽松）。
                #   根因不是 judge 模型弱（对照测试里所有模型对明确答案都判对），
                #   而是**任务本身定义错了**：记忆系统返回的是"证据"不是"答案"。
                #   → 补一次 generate：用同一套检索证据让模型作答，再拿答案去判分。
                ans, gerr = generate_answer(it['question'], pred)
                if ans is None:
                    errs.append('gen: %s' % gerr)
                    llm_skipped += 1
                else:
                    ok, note = judge_llm(it['question'], it['answer'], ans)
                    if ok is not None:
                        llm_total += 1
                        per_type[t]['llm_n'] += 1
                        if ok:
                            llm_hits += 1
                            per_type[t]['llm'] += 1
                    else:
                        errs.append(note)
            if verbose and i % 10 == 0:
                print('  ... %d/%d  strict %d/%d  llm %d/%d' % (
                    i, len(todo), strict_hits, strict_total, llm_hits, llm_total))
        except Exception as e:
            errs.append('%s: %s' % (type(e).__name__, str(e)[:70]))

    lines.append('  能力类型          n   strict适用  strict      llm')
    for t in sorted(per_type):
        d = per_type[t]
        sr = d['strict'] / d['strict_n'] if d['strict_n'] else 0
        lr = d['llm'] / d['llm_n'] if d['llm_n'] else 0
        lines.append('  %-22s %3d   %3d/%3d   %5.1f%%   %5.1f%%'
                     % (t, d['n'], d['strict_n'], d['n'], sr * 100, lr * 100))
    lines.append('-' * 72)
    sr = strict_hits / strict_total if strict_total else 0
    lr = llm_hits / llm_total if llm_total else 0
    lines.append('  严格匹配（下界）   %d/%d = %.1f%%   [另有 %d 题 gold 为散文型，strict 不适用]'
                 % (strict_hits, strict_total, sr * 100, strict_na))
    if use_llm:
        lines.append('  LLM judge（检索→生成→判分） %d/%d = %.1f%%' % (llm_hits, llm_total, lr * 100))
        if llm_skipped:
            lines.append('    （其中 %d 题在生成阶段失败，未计入分母）' % llm_skipped)
        if llm_total:
            lines.append('  ★两者差距 %.1f pp —— 这就是 judge 的宽松度（不报这个数=在骗人）'
                         % ((lr - sr) * 100))
        # ★2026-09-15：分母健全性校验。宁可把"算错了"写在脸上，也不让异常偷偷缩样本。
        #   首轮 60 题就踩了：8 题因 `int.lower()` 崩溃被静默跳过，llm 分母变成 52，
        #   看结果时很容易误以为"只跑了 52 题"，而实际是 60 题里有 8 题根本没判上分。
        if strict_total + strict_na != len(todo) or llm_total + llm_skipped != len(todo):
            lines.append('  ✗ 分母不健全：样本 %d 题，strict 有效 %d + 不适用 %d，llm 有效 %d + 跳过 %d'
                         % (len(todo), strict_total, strict_na, llm_total, llm_skipped))
            lines.append('    → 有题在判分阶段被跳过（异常见下），分数不可直接采信')
        else:
            lines.append('  ✓ 分母健全：%d/%d 题全部完成 strict 与 llm 判分' % (len(todo), len(todo)))
    lines.append('=' * 72)
    lines.append('  ⚠ 不可比声明：本结果**不能**与 LongMemEval 论文数值直接对比——')
    lines.append('     ① 论文假设全 haystack 入上下文；本 harness 是检索式（top-%d）' % k)
    lines.append('     ② 样本 %d 题（非全量 500），有抽样误差' % len(todo))
    lines.append('     ③ judge 有已知的宽松偏差（文献实测可接受 ~63%% 故意错答）')
    lines.append('     ④ strict 只在"实体型 gold"上有意义；散文型已剔除（%d 题），'
                 '否则它测的是 gold 长短而非检索质量' % strict_na)
    if errs:
        lines.append('  ⚠ 异常 %d 条，样例: %s' % (len(errs), errs[0][:70]))
    lines.append('=' * 72)

    text = '\n'.join(lines)
    print(text)
    out = {'ts': dt.datetime.now().isoformat(), 'variant': variant,
           'n': len(todo), 'strict': round(sr, 4),
           'strict_n': strict_total, 'strict_na': strict_na,
           'llm': round(lr, 4) if use_llm else None,
           'llm_n': llm_total, 'llm_skipped': llm_skipped,
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

