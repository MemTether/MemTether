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
    r'(?P<entity>(?:(?!的)[\w\u4e00-\u9fff\-\. ]){2,22})\s*的\s*(?P<attr>[\w\u4e00-\u9fff\-\.]{1,15}?)(?:是|在|为|了|$|[^\w\u4e00-\u9fff])'
)
# 「X支持Y」「X有Y」模式
_SUPPORT_PATTERN = re.compile(
    # ★2026-10-08 fix: same whitespace tolerance as _ATTR_PATTERN.
    r'(?P<entity>[\w\u4e00-\u9fff\-\.][\w\u4e00-\u9fff\-\. ]{1,20}?[\w\u4e00-\u9fff\-\.])\s*(?:支持|有|具备|提供|包含)\s*(?P<attr>[\w\u4e00-\u9fff\-\.]{2,15})'
)

# ★P0-3 (2026-10-18): 抽取质量重建——修「Claude Code」类多词英文实体断链 + 散文垃圾实体
# 根因（全量审计 2026-10-13 §10 已知未修 + 本轮 2034 条 active 全量实测）：
#   ① _ATTR_PATTERN 的 entity 段任意匹配 → 抽出「这正是 2026-09-16 事故」等长散文
#     （实测 3481 个 distinct entity 里 1967 个是 len>30 或含功能词的垃圾，56%）。
#   ② 混合串「search packy 时 Clash」被当成一个实体（真实体是尾部 ASCII 词 Clash）。
# 修法（纯规则、零依赖，2034 条全量 0.4s）：
#   S1 _FUNCT_WORDS 功能词表：实体含任一功能词 → 拒（散文/句子残留）。
#      严格集（不含 中/上/下/内/外 等可入名词的结构字）+ 时/后/前。
#   S2 _is_solid_entity：实体要么是 ASCII 词串（字母开头，词间单空格，如 'Claude Code'），
#      要么是纯 CJK 2-12 字且无功能词（如 '记忆中枢'）。
#   S3 _tail_ascii_entity：混合串取**尾部 ASCII 词段**（'search packy 时 Clash'→'Clash'）。
#   S4 _SUPPORT_PATTERN 拆成 ASCII 版 + CJK 版：旧版对 'GPT-4o 支持图片' 会把
#      entity 吃成 'GPT'（ASCII 边界截断），拆开各自精确。
#   S5 attr 尾部动词剥离（没生效/未生效/下载/安装 等——这些是谓语不是属性）。
# 效果实测（2034 条 active）：
#   old: 3742 pairs / 3481 ents / 1967 prose-junk
#   new: 1950 pairs / 1607 ents / 0 prose-junk / 1 trigger-junk
#   正例全保：Claude Code / Clash(混合串尾) / bge-m3 / GPT-4o / 记忆中枢 / mcp_server.py
_FUNCT_WORDS = set(
    '的了是在我有和就都不很到说要去你会着没看好自己怎什帮查下随情事们呢吧啊嘛呀哦哈呗'
    '样做弄搞整找搜看听问答讲提啥为还又再被把给从对跟离往朝向时后前'
)
_ASCII_TERM_RE = re.compile(r'[A-Za-z][A-Za-z0-9._\-]*(?: [A-Za-z0-9._\-]+)*')
_SUPPORT_ASCII = re.compile(
    r'(?P<entity>[A-Za-z][A-Za-z0-9._\-]*(?: [A-Za-z0-9._\-]+)*)'
    r'\s*(?:支持|具备|提供|包含)\s*'
    r'(?P<attr>[A-Za-z0-9\u4e00-\u9fff._\-]{2,15})')
_SUPPORT_CJK = re.compile(
    r'(?P<entity>[\u4e00-\u9fff]{2,12})\s*(?:支持|具备)\s*'
    r'(?P<attr>[A-Za-z0-9\u4e00-\u9fff._\-]{2,15})')
_ATTR_TRAIL_VERB = re.compile(
    r'(?:没生效|未生效|生效了|已生效|没找到|找不到|下载|安装)$')
# 动作/完成句式：内容含完成关键词时，取首段作为 entity
_ACTION_KEYWORDS = re.compile(
    r'(?:发布完成|全渠道发布|上线完成|跑完|修复完成|写完|做完|制作完成|评测完成|扫描完成|全覆盖完成|全量.*完成|批.*完成|审稿完成|到达|解除)'
)


def _is_solid_entity(e):
    """S2：实体要么 ASCII 词串（可含单空格），要么纯 CJK 2-12 字无功能词。"""
    e = e.strip()
    if not e or len(e) < 2 or len(e) > 30:
        return False
    if _ASCII_TERM_RE.fullmatch(e):
        return True
    if re.search(r'[A-Za-z]', e):
        return not any(ch in _FUNCT_WORDS for ch in e)
    return 2 <= len(e) <= 12 and not any(ch in _FUNCT_WORDS for ch in e)


def _tail_ascii_entity(e):
    """S3：混合串取尾部 ASCII 词段；无则 None。'search packy 时 Clash' → 'Clash'"""
    parts = re.split(r'[\u4e00-\u9fff]+', e)
    parts = [p.strip(' ，。、；;') for p in parts if p.strip(' ，。、；;')]
    if parts and _ASCII_TERM_RE.fullmatch(parts[-1]):
        return parts[-1]
    return None


def _norm_entity(e):
    """a75-r4: strip personal-determiner prefixes so '我的上司' and '上司'
    map to the same entity node (MemDaily/中文口语场景核心归一化)."""
    for pref in ('我的', '我们的', '我的家', '家里', '我家', '我'):
        if e.startswith(pref) and len(e) > len(pref) + 1:
            return e[len(pref):]
    return e


def extract_predicates(content):
    """从 content 抽取 (entity, attr) 对，返回 list[dict]。

    P0-3 重写（2026-10-18）：solid-entity 过滤 + 尾部 ASCII 恢复 + 支持句拆 ASCII/CJK。
    纯规则（零 LLM），宁漏勿滥。返回 [{"e": entity, "a": attr}, ...]
    """
    if not content:
        return []
    t = re.sub(r'^【[^】]*】', '', content.strip()).strip()
    preds, seen = [], set()

    def _add(e, a):
        e, a = e.strip(' ，。、和与；;'), a.strip(' ，。、和与；;')
        if len(e) < 2 or len(a) < 1:
            return
        a = _ATTR_TRAIL_VERB.sub('', a) or a
        if len(a) < 1:
            return
        key = (e, a)
        if key not in seen:
            seen.add(key)
            preds.append({'e': e, 'a': a})

    # 1) 「X的Y」模式 + S1/S2/S3 过滤
    for m in _ATTR_PATTERN.finditer(t):
        raw_e, a = m.group('entity'), m.group('attr')
        e = raw_e.strip(' ，。、和与；;')
        if len(e) < 2 or e in ('我', '你', '他', '她', '这', '那', '其', '本'):
            continue
        if _is_solid_entity(e):
            _add(e, a)
            continue
        ta = _tail_ascii_entity(e)
        if ta and len(ta) >= 2:
            _add(ta, a)
    # 2) 「X支持Y」ASCII 实体版
    for m in _SUPPORT_ASCII.finditer(t):
        _add(m.group('entity').strip(), m.group('attr'))
    # 3) 「X支持Y」CJK 实体版（无功能词）
    for m in _SUPPORT_CJK.finditer(t):
        e = m.group('entity')
        if not any(ch in _FUNCT_WORDS for ch in e):
            _add(e, m.group('attr'))
    # 4) 动作/完成句式（保留原逻辑：首段作为 __status__ 实体）
    if _ACTION_KEYWORDS.search(t):
        _first = re.split(r'[（(。；，,\n]', t)[0].strip()
        _first = re.sub(r'^【[^】]*】', '', _first).strip()
        # P0-3: __status__ 实体也必须是 solid term —— 旧版放行任意首段，
        # 实测产出 53 个散文实体（'★我犯的错误记录' / '能做的全部做完' …）
        if len(_first) >= 2 and _is_solid_entity(_first[:30]):
            key = (_first[:30], '__status__')
            if key not in seen:
                seen.add(key)
                preds.append({'e': _first[:30], 'a': '__status__'})
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
            e = _norm_entity(e)
            if len(e) < 2:
                continue
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
