# -*- coding: utf-8 -*-
"""predicates.py — 十三修：typed statement（谓词抽取 + 属性存在性校验）

设计动机：
  refuse_bench N3_same_form（同形干扰）是当前最大短板——
  「Clash 的订阅连接地址」能召回 10 条含 Clash 的记忆，但没有一条真正记录了地址。
  根因：检索只看内容相似度，不区分「关于 Clash 的记忆」和「记录了 Clash 订阅地址的记忆」。

方案（零 API 依赖）：
  1. 写入端：从 content 自动抽取 (entity, attr) 对，存入 facts.predicate 列。
  2. 检索端：识别查询里的属性问句（X的Y / X有Y吗），对 top 候选做
     「该实体的记忆是否真正提到 Y」校验，未提及的标 not_answered 并降权。
"""
import re
import json
import sqlite3

# ---------------------------------------------------------------
# 写入端：谓词抽取
# ---------------------------------------------------------------

# 中文常见属性词（首句截断前的「的X」模式）
_ATTR_PATTERN = re.compile(
    # ★2026-10-08 fix: allow whitespace around 的 — 'Clash 的端口' never matched
    # without it (retrieval-side _QUERY_ATTR already had \s*; write-side didn't).
    r'(?P<entity>[\w\u4e00-\u9fff\-\.]{2,15})\s*的\s*(?P<attr>[\w\u4e00-\u9fff\-\.]{1,15})'
)
# 「X支持Y」「X有Y」模式
_SUPPORT_PATTERN = re.compile(
    # ★2026-10-08 fix: same whitespace tolerance as _ATTR_PATTERN.
    r'(?P<entity>[\w\u4e00-\u9fff\-\.]{2,15})\s*(?:支持|有|具备|提供|包含)\s*(?P<attr>[\w\u4e00-\u9fff\-\.]{2,15})'
)

# 动作/完成句式：内容含完成关键词时，取首段作为 entity
_ACTION_KEYWORDS = re.compile(
    r'(?:发布完成|全渠道发布|上线完成|跑完|修复完成|写完|做完|制作完成|评测完成|扫描完成|全覆盖完成|全量.*完成|批.*完成|审稿完成|到达|解除)'
)

def extract_predicates(content):
    """从中文 content 抽取 (entity, attr) 对，返回 list[dict]。

    纯规则（零 LLM），宁漏勿滥：只抽高置信度的「X的Y是/在/为」和「X支持Y」模式。
    返回 [{"e": entity, "a": attr}, ...]
    """
    if not content:
        return []
    t = re.sub(r'^【[^】]*】', '', content.strip()).strip()
    preds = []
    seen = set()
    for m in _ATTR_PATTERN.finditer(t):
        e, a = m.group('entity'), m.group('attr')
        # 过滤：实体/属性太短或含常见非实体词
        if len(e) < 2 or len(a) < 1 or e in ('我','你','他','她','这','那','其','本'):
            continue
        key = (e, a)
        if key not in seen:
            seen.add(key)
            preds.append({'e': e, 'a': a})
    if _ACTION_KEYWORDS.search(t):
        # extract first meaningful phrase as entity (before punctuation/parens)
        _first = re.split(r'[（(。；，,\n]', t)[0].strip()
        # remove 【...】 prefix
        _first = re.sub(r'^【[^】]*】', '', _first).strip()
        if len(_first) >= 2:
            key = (_first[:30], '__status__')
            if key not in seen:
                seen.add(key)
                preds.append({'e': _first[:30], 'a': '__status__'})
    for m in _SUPPORT_PATTERN.finditer(t):
        e, a = m.group('entity'), m.group('attr')
        if len(e) < 2 or len(a) < 2 or e in ('我','你','他','她','这','那','其','本'):
            continue
        key = (e, a)
        if key not in seen:
            seen.add(key)
            preds.append({'e': e, 'a': a})
    return preds[:8]  # 每条记忆最多 8 个谓词，防止噪声


def store_predicate(conn, uid, content):
    """写入时自动抽取并存储谓词到 facts.predicate 列（JSON）。"""
    preds = extract_predicates(content)
    if preds:
        conn.execute('UPDATE facts SET predicate=? WHERE uid=?',
                     (json.dumps(preds, ensure_ascii=False), uid))
    return preds


def store_entities(conn, uid, content, source='', ts='', valid_from='', valid_to=''):
    """写入时把谓词拆成 (entity, attr, value) 存入 fact_entities 表。

    与 store_predicate 不同：store_predicate 存 JSON 到 facts.predicate 列，
    store_entities 存**独立行**到 fact_entities 表——使 SQL 确定性查询成为可能。
    """
    preds = extract_predicates(content)
    if not preds:
        return []
    ts_val = ts or __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    vf = valid_from or ts_val
    for p in preds:
        conn.execute(
            "INSERT INTO fact_entities (entity, attr, value, fact_uid, source, ts, valid_from) VALUES (?,?,?,?,?,?,?)",
            (p['e'], p['a'], content[:200], uid, source, ts_val, vf))
    return preds


def query_entities(conn, entity, attr=None, include_retired=False):
    """确定性查询：按实体（+可选属性）取回所有活跃 fact uid。不走 embedding，不走概率。"""
    sql = "SELECT DISTINCT fe.fact_uid, f.content, f.status FROM fact_entities fe "           "JOIN facts f ON f.uid = fe.fact_uid WHERE fe.entity = ?"
    params = [entity]
    if attr:
        sql += " AND fe.attr LIKE ?"
        params.append(f'%{attr}%')
    if not include_retired:
        sql += " AND f.status = 'active'"
    sql += " ORDER BY f.updated_at DESC"
    return [dict(zip(('uid','content','status'), r)) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------
# 检索端：属性存在性校验
# ---------------------------------------------------------------

# 查询里的属性问句：「X的Y」模式
_QUERY_ATTR = re.compile(
    r'(?P<entity>[\w\u4e00-\u9fff\-\.]{2,15})\s*的\s*(?P<attr>[\w\u4e00-\u9fff\-\.]{1,15})'
)

_QUESTION_STOP = re.compile(r'[是|在|多|哪|怎|什|么|几|谁|为|什|呢|吧|吗|?|？|。|；|，]')
def _query_attrs(query):
    """从查询里抽取 (entity, attr) 对，attr 在问句词处截断。"""
    if not query:
        return []
    t = query.strip()
    pairs = []
    seen = set()
    for m in _QUERY_ATTR.finditer(t):
        e, a = m.group('entity'), m.group('attr')
        # 截断属性：在问句/疑问词处截断
        _m2 = _QUESTION_STOP.search(a)
        if _m2:
            a = a[:_m2.start()]
        if len(e) >= 2 and len(a) >= 1:
            key = (e, a)
            if key not in seen:
                seen.add(key)
                pairs.append({'e': e, 'a': a})
    return pairs


def check_attr_coverage(query, results):
    # P2-2 (2026-10-05): NOT wired into memsearch/gateway yet (audit finding).
    # Stored predicates exist at write time; retrieval-side validation is future work.
    """检索后校验：查询含「X的Y」时，检查候选是否真正记录了 Y。

    逻辑：
    - 候选同时含 entity+attr → 有资格回答（any_answered=True）
    - 候选含 entity 但不含 attr → not_answered=True 并降权
    - 候选不含 entity → 中性（不改分）
    - all_unanswered=True 仅当**所有候选**都没有同时含 entity+attr
    """
    attrs = _query_attrs(query)
    if not attrs:
        return results, False
    any_answered = False
    for x in results:
        c = (x.get('content') or '').lower()
        _has_entity = False
        _has_attr = False
        for pair in attrs:
            e, a = pair['e'].lower(), pair['a'].lower()
            if e in c:
                _has_entity = True
                if a in c:
                    _has_attr = True
        if _has_entity and _has_attr:
            any_answered = True
        elif _has_entity and not _has_attr:
            x['not_answered'] = True
            x['not_answered_reason'] = 'mentions entity but lacks queried attr'
            x['score'] = round(x['score'] * 0.15, 5)
        # 不含 entity → 中性，不改
    return results, not any_answered
