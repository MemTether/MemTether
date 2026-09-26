#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
governance.py —— 记忆中枢「治理层」：冲突检测 + 时间衰减 + 退役建议

为什么需要它（2026-09-15 实测暴露）：
  中枢的存储层和资产层已经可用，但**治理层几乎为零**。实测：
    - `superseded_by` 字段   已填 0 / 254   （结构在，从没用过）
    - `valid_to`   字段      已填 0 / 254   （失效时间从未记录）
    - `valid_from` 字段      已填 6 / 254
  后果是具体的、可复现的：
    - 「DeepSeek官方API已充值10元可用(2026-09-11)」与
      「DEEPSEEK_OFFICIAL_KEY 已 401 失效(2026-09-15)」**两条都是 active**，
      检索先返回哪个看运气。用户问「deepseek key 还能用吗」会拿到过期答案。
  治理层的三件事，对应业界的成熟做法：
    ① 冲突检测   —— 找出"同一主题、结论相反"的 active 条目（Mem0 的 contradiction detection）
    ② 时间衰减   —— 老条目在被检索时降权（agentmemory V4 的 temporal signal / confidence decay）
    ③ 退役建议   —— 对确认过期的条目给出 supersede/retire 建议，**但不自动执行**
                    （★自动执行是危险的：判错一次就永久丢事实。默认只读，--apply 才写）

设计原则（沿用 Graphiti 的 t_invalid 思路）：
  **不删除、只失效**。过期条目保留在库里（`status='superseded'` + `valid_to` 有值），
  因为"以前信什么、什么时候改的"本身就是有价值的历史。检索默认只取 active，
  但要追历史时能查到。

用法：
  python governance.py conflicts          # 只读：列出疑似冲突
  python governance.py decay              # 只读：打印时间衰减后的重排效果
  python governance.py stale [--days 30]  # 只读：列出可能过期的条目
  python governance.py retire --uid X --by Y   # 执行退役（需显式指定）
  python governance.py audit              # 治理层完整体检
"""
import os
import re
import sys
import json
import math
import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'memory.db')

# 默认操作者标识（中性值 local；单机/自建环境可用 MEM_DEFAULT_SOURCE 覆盖）
DEFAULT_SOURCE = os.environ.get('MEM_DEFAULT_SOURCE', 'local')


# ---------------- ① 冲突检测 ----------------
# 思路：同一"主题键"下出现多条 active，且它们的**极性相反**（一条肯定、一条否定）。
#
# ★v2 重写（2026-09-15 21:10 实测打脸后）：v1 的判定有两处硬伤，都在实测中暴露：
#
#  【硬伤 1 · 假阳性泛滥】v1 用「稀有 token」(全库 ≤3 次) 做主题对齐。结果
#    `schtasks.exe` / `cwd` / `raw` / `dump` / `message` / `launch` 这类**工具性名词**
#    被当成主题词——两条毫不相干的记忆（"Electron 换图标"与"中转站余额查询"）
#    因为都提到 `schtasks.exe` 就被判成冲突。24 对候选里绝大多数是这种噪音。
#    → 修正：主题词必须是**专名/实体**，不能是通用技术词。加 `_STOP` 停用词表，
#      并且要求共享词里**至少一个是专名**（含大写、下划线、或中文实体词）。
#
#  【硬伤 2 · 漏掉真矛盾】DeepSeek key 那对真矛盾，共享词是 `deepseek`
#    （全库出现 ≥20 次）→ 被"稀有词"过滤直接筛掉了。**真矛盾因为被反复提及而逃检**，
#    这跟直觉正好相反：越重要的事越常被记录，反而越容易被"稀有度"排除。
#    → 修正：不再只用稀有词。改为"共享词数 ≥ min_shared，且其中至少一个**强实体**"。
#      强实体 = 带大写/下划线/数字的 ASCII 标识符（DEEPSEEK_OFFICIAL_KEY、sk-EM7、
#      icon.png），或长度 ≥3 的中文专名。这类词本身就是"在说同一件事"的证据。
#
#  【硬伤 3 · 粒度错】v1 把「整条记忆」当原子单元。但一条长记忆里 ①~⑦ 各说各的
#    （实测 fact-20260915155740 里 ⑦ 说 "DEEPSEEK_OFFICIAL_KEY 已 401 失效"，
#    而 ①~⑥ 全是无关内容）。整条比对极性会被无关段落稀释，判成 'mix' 直接丢弃。
#    → 修正：**句子级**比对。按 ①②③/分号/换行切句，逐句判极性，
#      报冲突时精确指出是**哪一句**跟哪一句打架。

_POS = ('可用', '成功', '已通', '连通', '正常', '已修', '生效', '可以', '支持', '活着', '复活',
        '已充值', '能查', '通过', '已建', '已落地')
_NEG = ('失效', '不可用', '不通', '失败', '401', '403', '已死', '封停', '退役', '撤销', '挂死',
        '禁止', '不可依赖', '已撤销', '欠费', '清零', '作废', '过期')

# 通用技术词/工具名：出现在完全无关的记忆里，不能用来对齐主题
_STOP = {
    'cwd', 'raw', 'dump', 'message', 'launch', 'score', 'top1', 'rrf', 'menu', 'settings',
    'programs', 'server', 'account', 'balance', 'mtime', 'deny', 'icacls', 'fail-closed',
    'gmail.com', 'venv-memory', 'region_restricted', 'schtasks.exe',
    'cmd.exe', 'python.exe', 'index.html', 'readme', 'todo', 'mcp.json', 'json', 'http',
    'https', 'localhost', 'localhost:8080', 'utf-8', 'stdout', 'stdin', 'api',
}

# ★脱敏（2026-09-16）：此处原先硬编码了两个**真实密钥前缀**（形如「s k -」加四个字符）。
#   它们进 _STOP 的原因合理 —— 记忆里出现过的密钥片段会被 _strong_entities
#   当成"强实体"（含数字 → 命中第 136 行判据），进而把两条无关记忆错配成"同一件事"。
#   但把真实前缀写进开源源码 = 泄漏：哪怕只有前 8 位，也把爆破空间砍掉几个数量级。
#   改为**通用规则**：任何以「s k -」开头的 token 一律视为停用词。
#   行为等价（原先被排除的那两个前缀，现在同样被排除），且不留任何真实字符。
_STOP_PREFIX = ('sk-',)


def _is_stop(w):
    """通用技术词 / 密钥片段 → 不能用来对齐主题。"""
    w = (w or '').lower()
    return w in _STOP or w.startswith(_STOP_PREFIX)


def _polarity(text):
    """返回 (正向命中数, 负向命中数)。极性相反 = 冲突候选。"""
    p = sum(1 for w in _POS if w in text)
    n = sum(1 for w in _NEG if w in text)
    return p, n


def _sentences(text, max_len=200):
    """按 ①②③/分号/换行 切句。返回 [(句号标记, 句子文本)]。

    ★为什么要切句：一条长记忆里各段说各的事，整条判极性会被稀释成 'mix'，
      导致真矛盾被丢弃。切句后能精确定位"是这一句跟那一句打架"。
    """
    txt = (text or '').replace('\r', '')
    # 在 ①-⑳ 标记前插分隔符；再按换行/分号切
    txt = re.sub(r'([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])', r'\n\1', txt)
    parts = []
    for seg in re.split(r'[\n；;]+', txt):
        seg = seg.strip()
        if not seg:
            continue
        # 段太长再按句号切（保留分隔）
        if len(seg) > max_len:
            for s in re.split(r'(?<=[。！？])', seg):
                s = s.strip()
                if s:
                    parts.append(s)
        else:
            parts.append(seg)
    return parts


def _strong_entities(text):
    """提取**强实体词**：能证明"两条记忆在说同一件事"的词。

    判据（任一即成）：
      - 带下划线或数字的 ASCII 标识符：DEEPSEEK_OFFICIAL_KEY / gpt-4o / icon.png
      - 全大写的 ASCII 词（≥3 字符）：ARK / ACL
      - 长度 ≥4 的 ASCII 长词（非停用词）：deepseek / openclaw / packy
        ★v2.1 补：实测 `deepseek` 这种 8 字符全小写词被 v2 漏掉，导致
          「DeepSeek 已充值可用」vs「DEEPSEEK_OFFICIAL_KEY 已 401 失效」
          这对**最该抓的真矛盾**逃检。长度 ≥4 的非停用 ASCII 词本身就是专名证据。
      - 长度 ≥3 的中文专名候选（取前缀 3/4 字）
    通用技术词（_STOP）一律排除。
    """
    ents = set()
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', text or ''):
        wl = w.lower()
        if _is_stop(wl):
            continue
        # 带下划线/数字 → 强实体
        if '_' in w or any(ch.isdigit() for ch in w):
            ents.add(wl)
        # 全大写（≥3 字符）→ 强实体
        elif w.isupper() and len(w) >= 3:
            ents.add(wl)
        # 长度 ≥4 的普通 ASCII 词 → 也算（专名证据；_STOP 已排除通用词）
        elif len(w) >= 4:
            ents.add(wl)
    for seg in re.findall(r'[\u4e00-\u9fa5]{3,}', text or ''):
        for ch in '的是在和与及等对为':
            seg = seg.replace(ch, '') if seg.startswith(ch) else seg
        if len(seg) >= 3:
            ents.add(seg[:3])
            if len(seg) >= 4:
                ents.add(seg[:4])
    return ents


def _polarity_by_entity(text):
    """★v3 核心修正：把极性**绑定到实体**，而不是整句。

    实测打脸（2026-09-15 21:05）：v2.1 报出 246 对，绝大多数是「不同主语被错配」——
      句A: "deepseek 401 已失效，只剩 astra"     → 整句判 neg
      句B: "GPTX_ASTRA_KEY 可用，已做 deepseek→astra 回退" → 整句判 pos
    两条其实**说的是同一件事**（deepseek 挂了、改用 astra），却被判成"互相打架"。

    根因：极性是**实体级属性**，不是句子级。`astra 可用` 的正向属于 astra，
    `deepseek 失效` 的负向属于 deepseek。必须在同一实体上比较才成立。

    做法：按"子句"（逗号/顿号切）拆，每子句找它包含的实体 + 该子句极性，
    建立 {实体: 极性} 映射。只有**同一实体**上出现相反极性才算真矛盾。
    """
    out = {}   # entity -> {'pol': +1/-1, 'text': 子句}
    for seg in _sentences(text):
        for sub in re.split(r'[，,、]', seg):
            sub = sub.strip()
            if not sub:
                continue
            p, n = _polarity(sub)
            if not (p or n) or (p and n):
                continue
            ents = {e for e in _strong_entities(sub) if not _is_stop(e)}
            if not ents:
                continue
            pol = 1 if p else -1
            for e in ents:
                prev = out.get(e)
                # 同实体上同极性 → 保留更长的子句文本（信息更多）
                if prev is None or (prev['pol'] == pol and len(sub) > len(prev['text'])):
                    out[e] = {'pol': pol, 'text': sub}
    return out



def _topic_tokens(text, min_len=3):
    """（v1 遗留，仅 decay/stale 之外的旧调用点保留）提取稀有 token。"""
    toks = set()
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', text or ''):
        wl = w.lower()
        if not _is_stop(wl):
            toks.add(wl)
    for seg in re.findall(r'[\u4e00-\u9fa5]{3,}', text or ''):
        for n in (3, 4, 5):
            if n <= len(seg):
                toks.add(seg[:n])
    return toks


def _row_conflicts(rows, cutoff, min_shared=1, max_per_uid=3):
    """核心比对（**v3 实体级极性**）。

    对每条记忆算出 `{实体: 极性}`；两条记忆若在**同一实体**上极性相反，
    才是真矛盾候选。这样 `astra 可用` 与 `deepseek 失效` 不会被错配。

    额外护栏（防再次假阳性泛滥）：
      - 只在"两条都含该实体"时比对（实体级，天然满足）
      - 排除中文泛词实体（长度 3 的中文前缀常是"用户原话""共享强实体"这类噪音）
      - 每条记忆最多报 max_per_uid 对（避免一条长记忆刷屏）
    """
    items = []
    for r in rows:
        ts = (r['updated_at'] or '')[:16]
        try:
            if dt.datetime.strptime(ts, '%Y-%m-%d %H:%M') < cutoff:
                continue
        except Exception:
            continue
        ep = _polarity_by_entity(r['content'])
        if not ep:
            continue
        items.append({'uid': r['uid'], 'ts': ts, 'ep': ep,
                      'text': r['content'], 'src': r['source']})

    out = []
    seen = set()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            A, B = items[i], items[j]
            # 找"同一实体、极性相反"的交集
            clashes = []
            for e in set(A['ep']) & set(B['ep']):
                if A['ep'][e]['pol'] != B['ep'][e]['pol']:
                    # ★护栏：纯中文前缀实体（非标识符）需要≥2个实体支持才算，
                    #   否则"用户原话""共享强实体"这类泛词会制造噪音
                    idlike = ('_' in e or any(c.isdigit() for c in e)
                              or (e.isascii() and len(e) >= 4))
                    clashes.append((e, idlike))
            if not clashes:
                continue
            n_id = sum(1 for _, k in clashes if k)
            if n_id < 1 and len(clashes) < 2:
                continue
            key = tuple(sorted([A['uid'], B['uid']]))
            if key in seen:
                continue
            seen.add(key)
            ents = sorted(e for e, _ in clashes)
            first = ents[0]
            out.append({
                'a': {'uid': A['uid'], 'ts': A['ts'],
                      'text': A['ep'][first]['text'],
                      'full': A['text']},
                'b': {'uid': B['uid'], 'ts': B['ts'],
                      'text': B['ep'][first]['text'],
                      'full': B['text']},
                'shared': ents,
                'why': '同一实体相反极性',
                'weight': n_id * 10 + len(clashes),
            })
    out.sort(key=lambda x: -x['weight'])
    return out


def find_self_contradictions(rows=None):
    """★新增：找**同一条记忆内部**自相矛盾（同一实体上出现相反极性）。

    实测动机：fact-20260915155740 那条"四层架构已全部落地"里，⑦ 说
    "DEEPSEEK_OFFICIAL_KEY 已 401 失效"，但同条别处又提到 deepseek 通道可用。
    这种"一条记录自己打自己"比跨条冲突更隐蔽——读者只看首句结论就以为全条可信。
    """
    conn = None
    if rows is None:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT uid, type, content, updated_at, source FROM facts "
                            "WHERE status='active'").fetchall()
    out = []
    for r in rows:
        # 逐子句收集，允许同实体多次出现（要看到相反极性）
        bag = {}   # entity -> {pol: subclause}
        for seg in _sentences(r['content']):
            for sub in re.split(r'[，,、]', seg):
                sub = sub.strip()
                if not sub:
                    continue
                p, n = _polarity(sub)
                if not (p or n) or (p and n):
                    continue
                ents = {e for e in _strong_entities(sub) if not _is_stop(e)}
                if not ents:
                    continue
                pol = 1 if p else -1
                for e in ents:
                    bag.setdefault(e, {}).setdefault(pol, sub)
        clashes = [(e, d) for e, d in bag.items() if len(d) == 2]
        # 护栏：至少 1 个"标识符型"实体，否则靠 ≥2 个中文泛词
        n_id = sum(1 for e, _ in clashes
                   if '_' in e or any(c.isdigit() for c in e) or (e.isascii() and len(e) >= 4))
        if not clashes or (n_id < 1 and len(clashes) < 2):
            continue
        e0, d0 = clashes[0]
        out.append({'uid': r['uid'], 'ts': (r['updated_at'] or '')[:16],
                    'shared': sorted(e for e, _ in clashes),
                    'entity': e0,
                    'pos': {'text': d0.get(1, '')}, 'neg': {'text': d0.get(-1, '')}})
    if conn:
        conn.close()
    return out


def find_conflicts(days_window=30, min_shared=1):
    """找疑似冲突（**启发式·供人工复核，不作自动判定**）。

    ★诚实声明（2026-09-15 21:05 三次迭代后实测得出，必须写在最前面）：
      纯规则法**无法可靠区分**「真矛盾」与「互补描述 / 踩坑记录」。实证：
        - v1（稀有 token）→ 假阳性泛滥（schtasks.exe 之类工具词当主题）
        - v2（句子级+强实体）→ 漏掉真矛盾（deepseek 那对因词汇量太常见被过滤）
        - v2.1（放宽实体）→ 246 对，过度报
        - v3（实体级极性）→ 120 对，仍然把「先试 A 不行就退 B」这类**互补决策**
          误判成矛盾
      结论：这是自然语言矛盾检测的**固有难点**，继续调参只会在两个失效模式之间摆动。
      → 因此定位调整为：**人工复核候选生成器**。它给出"值得看一眼"的条目，
        不给出"这就是矛盾"的结论。真正确性的判定走 `detect_explicit_conflicts()`。

    返回 [{a, b, shared, why, weight}]，按可信度降序。
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid, type, content, updated_at, source FROM facts "
        "WHERE status='active' ORDER BY updated_at DESC").fetchall()
    conn.close()
    cutoff = dt.datetime.now() - dt.timedelta(days=days_window)
    return _row_conflicts(rows, cutoff, min_shared=min_shared)


# ---- 精确检测：只认「同一实体 + 显式状态断言」的相反结论 ----
# 这是**确定性**的：句式明确，误报率低。用于自动退役建议。
#
# ★实测（2026-09-15 21:15）暴露的关键坑：**同物异名**。
#   真矛盾的双方写的是同一把 key，但字面完全不同：
#     ⊕ "DeepSeek官方API已充值10元可用"      ← 中文名
#     ⊖ "DEEPSEEK_OFFICIAL_KEY 已失效(401)" ← 大写标识符
#   字面匹配（含大小写归一）**永远匹配不上**，真矛盾直接逃检。
#   → 解法：**别名归并表**（canonical name）。把已知的同物异名收在一张表里，
#     正则匹配到任一别名都归到同一个 canonical key。

_ALIAS = {
    # ★实测教训（2026-09-15 21:48）：别名表**宁窄勿宽**。
    #   我最初把 `deepseek` 也归到 `deepseek_official`，结果
    #   『packy 的 deepseek-officially 分组 key 可用』（讲的是中转站分组 key）
    #   被错配成『DeepSeek 官方 key 401 失效』的对立方 —— **两把不同的 key**。
    #   → 只有**确证同物**的写法才进表；有歧义的（裸 `deepseek`）宁可漏也不误伤。
    'deepseek_official_key': 'deepseek_official',
    'deepseek_official': 'deepseek_official',
    'deepseek官方api': 'deepseek_official',
    'deepseek官方': 'deepseek_official',
    'packy_grok_sale_key': 'packy_grok_sale',
    'packy_grok_official_key': 'packy_grok_official',
    'gptx_astra_key': 'gptx_astra',
    'gptx_astra': 'gptx_astra',
    'gpt-6-astra': 'gptx_astra',
    'siliconflow': 'siliconflow',
    'ollama': 'ollama',
}


# ★引用型句子：不是"断言"，而是"转述/复盘别人怎么说"。不能作为冲突证据。
#   实测（2026-09-15 21:18）：09-15 那条复盘笔记里写了
#     『查"deepseek key 失效"返回09-11的"DeepSeek官方API已充值10元可用"，
#       而09-15事实是401失效，两条并存』
#   —— 正负极性都在**同一条**里（因为它在**讨论**那对矛盾）。若不过滤，
#   它会自己跟自己判成冲突，而真正的矛盾双方反而被这条元描述顶掉。
#
#   这是"自指污染"在治理层的同源表现：**描述问题的文字，长得跟问题本身一样**。
_QUOTE_MARKERS = ('返回', '查到', '搜到', '两条并', '此前那条', '旧的那条',
                  '会把', '会拿到')


def _is_quote_like(sent):
    """该句是否为"引用/复盘/假设"而非"当前断言"。"""
    return any(mk in sent for mk in _QUOTE_MARKERS)


def _canonical(ent):
    """把实体名归一到 canonical key（同物异名合并）。

    ★实测踩坑（2026-09-15 21:22）：正则捕获的是 `DeepSeek官方API`（含空格/大写），
      而 _ALIAS 里键写的是 `'deepseek官方api'`。大小写/空格不一致 → 别名表命中不了，
      于是「DeepSeek官方API」(pos) 与「DEEPSEEK_OFFICIAL_KEY」(neg) 被当成两个不同实体，
      **真矛盾永远配不上**。归一必须先做彻底：去空格、去标点、统一小写。
    """
    e = (ent or '').strip().lower()
    e = e.strip('`*《》「」【】[]()（）')
    e = re.sub(r'[\s\u3000]+', '', e)          # 去所有空白（含全角空格）
    e = e.replace('（', '(').replace('）', ')')
    if e in _ALIAS:
        return _ALIAS[e]
    # 去掉常见后缀变体（_key / -key / key / api）
    for suf in ('_key', '-key', 'key', 'api', '_api'):
        if e.endswith(suf) and len(e) > len(suf) + 2:
            base = e[:-len(suf)].rstrip('_-')
            if base in _ALIAS:
                return _ALIAS[base]
            # 递归再做一次（例如 deepseek_official_api → deepseek_official）
            if base in _ALIAS:
                return _ALIAS[base]
            return base
    return e


_STATUS_ASSERT = [
    # (正则, 极性) —— 匹配"实体 + 显式状态词"的断言模式。
    #
    # ★实测踩坑（2026-09-15 21:25）：`[A-Za-z][\w\-]*` 里的 `\w` 在 Python 中
    #   **匹配中文**！所以 `DEEPSEEK[\w\-]*` 会贪婪吞掉后面整串中文
    #   （`DeepSeek官方API已充值10元可用` 被当成一个"实体名"），
    #   与英文别名 `DEEPSEEK_OFFICIAL_KEY` 永远对不上，真矛盾逃检。
    #   → 铁律：ASCII 标识符的正则**只能用 `[A-Za-z0-9_.\-]`，绝不能用 `\w`**。
    (r'(DEEPSEEK_OFFICIAL_KEY|DEEPSEEK_[A-Za-z0-9_]*|DeepSeek\s*官方\s*API|DeepSeek\s*官方)'
     r'[^。；\n]{0,12}?(?:已|现在|目前)?\s*[^。；\n]{0,6}?(401|403|失效|不可用|封停|作废|已死)',
     -1),
    (r'(DEEPSEEK_OFFICIAL_KEY|DEEPSEEK_[A-Za-z0-9_]*|DeepSeek\s*官方\s*API|DeepSeek\s*官方)'
     r'[^。；\n]{0,14}?(?:已充值|充值|可用|连通|复活|恢复)', 1),
    (r'(PACKY_GROK_SALE_KEY|PACKY_GROK_OFFICIAL_KEY|PACKY_[A-Za-z0-9_]+)'
     r'[^。；\n]{0,16}?(?:401|403|失效|封停|停用|已死)', -1),
    (r'(PACKY_GROK_SALE_KEY|PACKY_GROK_OFFICIAL_KEY|PACKY_[A-Za-z0-9_]+)'
     r'[^。；\n]{0,16}?(?:可用|复活|恢复|都能用|正常)', 1),
    # packy_grok：中文里常写成裸名 `packy_grok`，单独一条
    (r'(packy_grok)\b[^。；\n]{0,16}?(?:401|403|失效|封停|停用|已死)', -1),
    (r'(packy_grok)\b[^。；\n]{0,16}?(?:可用|复活|恢复|正常)', 1),
    (r'(SILICONFLOW|SiliconFlow)'
     r'[^。；\n]{0,18}?(?:401|403|410|失效|废弃|不可查|余额不足)', -1),
    (r'(SILICONFLOW|SiliconFlow)'
     r'[^。；\n]{0,18}?(?:已充值|可用|200)', 1),
    (r'(GPTX_ASTRA_KEY|GPTX_[A-Za-z0-9_]+|api\.gptx\.cc|gptx_astra|gpt-6-astra)'
     r'[^。；\n]{0,16}?(?:失效|不可用|403|挂)', -1),
    (r'(GPTX_ASTRA_KEY|GPTX_[A-Za-z0-9_]+|api\.gptx\.cc|gptx_astra|gpt-6-astra)'
     r'[^。；\n]{0,16}?(?:可用|直连|通|正常)', 1),
    # OPENAI_API_KEY：通用 API key 状态断言（2026-09-26 5适配器 demo 实测补充）
    (r'(OPENAI_API_KEY|OpenAI\s*API\s*Key|OPENAI_[A-Za-z0-9_]*)'
     r'[^。；\n]{0,18}?(?:401|403|失效|不可用|作废|封停|已死)', -1),
    (r'(OPENAI_API_KEY|OpenAI\s*API\s*Key|OPENAI_[A-Za-z0-9_]*)'
     r'[^。；\n]{0,18}?(?:可用|余额充足|连通|正常|恢复|复活)', 1),
]


def detect_explicit_conflicts(days_window=90):
    """★精确检测：同一实体上，有**显式状态断言**且极性相反。

    与 find_conflicts 的区别：这里的句式是确定的（"X 已 401 失效" / "X 已充值可用"），
    误报率低得多。可放心用于自动退役建议（仍需 --apply 才写库）。
    """
    rows = None
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT uid, content, updated_at FROM facts "
                        "WHERE status='active' ORDER BY updated_at DESC").fetchall()
    conn.close()

    cutoff = dt.datetime.now() - dt.timedelta(days=days_window)
    # canonical entity -> {+1: [(uid,ts,sent)], -1: [...]}
    claims = {}
    for r in rows:
        ts = (r['updated_at'] or '')[:16]
        try:
            if dt.datetime.strptime(ts, '%Y-%m-%d %H:%M') < cutoff:
                continue
        except Exception:
            continue
        for seg in _sentences(r['content']):
            if _is_quote_like(seg):     # ★跳过引用/复盘/假设句，防自指污染
                continue
            for pat, pol in _STATUS_ASSERT:
                for m in re.finditer(pat, seg, re.IGNORECASE):
                    ent = _canonical(m.group(1))
                    if not ent or _is_stop(ent):
                        continue
                    claims.setdefault(ent, {1: [], -1: []})
                    claims[ent][pol].append(
                        {'uid': r['uid'], 'ts': ts, 'sent': seg.strip(),
                         'matched': m.group(0)[:60]})

    out = []
    for ent, d in claims.items():
        if not (d[1] and d[-1]):
            continue
        # ★关键判据：真矛盾的定义是**跨条**（两条独立记忆对同一实体结论相反）。
        #   同一条里正负都有 → 那是"同条自相矛盾"或在"讨论矛盾"，
        #   归 find_self_contradictions() / 人工看，不在这里报。
        pos_uids = {x['uid'] for x in d[1]}
        neg_uids = {x['uid'] for x in d[-1]}
        cross = (pos_uids - neg_uids) and (neg_uids - pos_uids)
        if not cross:
            continue
        pos = max([x for x in d[1] if x['uid'] not in neg_uids], key=lambda x: x['ts'])
        neg = max([x for x in d[-1] if x['uid'] not in pos_uids], key=lambda x: x['ts'])
        newer = 'neg' if neg['ts'] > pos['ts'] else 'pos'
        out.append({'entity': ent, 'pos': pos, 'neg': neg,
                    'newer': newer,
                    'suggest_retire': pos['uid'] if newer == 'neg' else neg['uid'],
                    'keep': neg['uid'] if newer == 'neg' else pos['uid'],
                    'suggest_retire_side': 'pos' if newer == 'neg' else 'neg'})
    out.sort(key=lambda x: x['entity'])
    return out


# ---------------- ② 时间衰减 ----------------
# 对齐 agentmemory V4 的 temporal signal + confidence decay，但做成**幂等的乘法因子**，
# 而不是改数据库里的 confidence（那会污染原始数据、且不可逆）。
#
# 设计：
#   factor = 0.5 ** (age_days / HALFLIFE)
#   HALFLIFE 默认 30 天 —— 即 30 天前的条目权重减半。
#   ★下限 0.35：不让老条目彻底消失。「老」不等于「错」——
#     本机很多资产（软件路径/端口）几个月都不会变，砍太狠是自伤。
#   ★`decision`/`incident` 类衰减更慢（半衰期 120 天）：它们记录"当时为什么这么定"，
#     是历史证据，不会因为时间过去就变假。
HALFLIFE_DEFAULT = 30
HALFLIFE_BY_TYPE = {'decision': 120, 'incident': 120, 'experience': 45, 'fact': 30}
DECAY_FLOOR = 0.35


def _norm_pair(a, b):
    """冲突对归一为无序，避免 (a,b)/(b,a) 各存一条。"""
    return (a, b) if a <= b else (b, a)


def record_conflict_review(uid_a, uid_b, verdict, note='', by_agent=DEFAULT_SOURCE):
    """记录一次冲突的人工复核结论。

    ★为什么需要这张表：detect_explicit_conflicts 定位是"误报率低的确定性检测"，
      但实测仍会误判。本库真实例（2026-09-16）：
        「四层架构已全部落地（含 gptx_astra 通道配置）」
        「2026-09-15 22:15 Astra GPTX_ASTRA_KEY 403 insufficient balance」
      被判为 gptx_astra 的极性冲突，实为**互补信息**——一条讲配置存在、
      一条讲当前故障，并不矛盾。
      若无复核留痕，这类误报会**永久扣治理度分且无人能纠正**。
      → 规则只负责生成候选，人工复核结论单独留痕，评分卡据此排除已否定的对。
    """
    a, b = _norm_pair(uid_a, uid_b)
    conn = sqlite3.connect(DB)
    try:
        # 自包含建表：本模块可能被独立调用（不经 gateway.init_db）
        conn.execute("CREATE TABLE IF NOT EXISTS conflict_reviews ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, uid_a TEXT, uid_b TEXT, "
                     "verdict TEXT, note TEXT, by_agent TEXT, ts TEXT)")
        conn.execute("INSERT INTO conflict_reviews (uid_a,uid_b,verdict,note,by_agent,ts) "
                     "VALUES (?,?,?,?,?,?)",
                     (a, b, verdict, (note or '')[:300], by_agent,
                      dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        return {'ok': True, 'uid_a': a, 'uid_b': b, 'verdict': verdict}
    finally:
        conn.close()


def reviewed_no_conflict():
    """已被人工复核否定为"非冲突"的 uid 对（frozenset 集合，供评分卡排除）。"""
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute("SELECT uid_a,uid_b FROM conflict_reviews "
                            "WHERE verdict='no_conflict'").fetchall()
        return {frozenset((a, b)) for a, b in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def decay_factor(updated_at, ftype='fact', halflife=None, now=None):
    """返回 (factor, age_days)。factor ∈ [DECAY_FLOOR, 1.0]。"""
    now = now or dt.datetime.now()
    try:
        t = dt.datetime.strptime((updated_at or '')[:16], '%Y-%m-%d %H:%M')
    except Exception:
        return 1.0, 0.0
    age_days = max((now - t).total_seconds() / 86400.0, 0.0)
    hl = halflife or HALFLIFE_BY_TYPE.get(ftype, HALFLIFE_DEFAULT)
    f = 0.5 ** (age_days / hl)
    return max(f, DECAY_FLOOR), age_days


def apply_decay(results, now=None, lookup_db=True):
    """对检索结果施加时间衰减。**不改内容，只调 score**，并记录原值便于审计。

    ★实测踩坑（2026-09-15 21:30）：`memsearch.search_hybrid()` 返回的字段是
      `[uid, content, type, source, score, semantic, reason]`——**根本没有 updated_at**！
      所以 v1 的 `decay_factor('')` 走异常分支静默返回 (1.0, 0.0)，
      表面看"衰减跑通了"，实际**每条都是 ×1.00**，等于没做。
      这是最危险的一类 bug：**不报错、结果看起来正常、但功能完全没生效**。
      → 修正：结果里没有 updated_at 时，**按 uid 回库补查**（而不是静默放弃）。
        若 lookup_db=False 或查不到，则标 `decay_missing=True` 明示未生效，
        绝不假装成功。
    """
    now = now or dt.datetime.now()
    need = [x for x in results if not (x.get('updated_at') or x.get('_updated_at'))]
    ts_map = {}
    if need and lookup_db:
        uids = [x.get('uid') for x in need if x.get('uid')]
        if uids:
            conn = sqlite3.connect(DB)
            try:
                q = ','.join('?' * len(uids))
                for uid, ts in conn.execute(
                        "SELECT uid, updated_at FROM facts WHERE uid IN (%s)" % q, uids):
                    ts_map[uid] = ts
            finally:
                conn.close()

    for x in results:
        ts = (x.get('updated_at') or x.get('_updated_at')
              or ts_map.get(x.get('uid')) or '')
        f, age = decay_factor(ts, x.get('type') or 'fact', now=now)
        x['updated_at'] = ts
        x['decay_factor'] = round(f, 4)
        x['age_days'] = round(age, 1)
        if not ts:
            x['decay_missing'] = True      # ★明示未生效，不假装
        if 'score_before_decay' not in x:
            x['score_before_decay'] = x.get('score', 0.0)
        x['score'] = round(x.get('score', 0.0) * f, 5)
    results.sort(key=lambda x: -x.get('score', 0.0))
    return results


# ---------------- ③ 过期候选（不自动执行） ----------------
def find_stale(days=30, min_confidence=0.0):
    """列出"很久没更新、且内容含否定/过期信号"的 active 条目，供人工判断。

    ★故意**不自动退役**：判错一次就永久丢事实。
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid, type, content, updated_at, confidence, source FROM facts "
        "WHERE status='active' ORDER BY updated_at").fetchall()
    conn.close()
    now = dt.datetime.now()
    out = []
    for r in rows:
        f, age = decay_factor(r['updated_at'], r['type'] or 'fact', now=now)
        if age < days:
            continue
        p, n = _polarity(r['content'])
        # 老 + 含否定信号 = 最可能是"已经变了但没人更新"
        risk = 'high' if (n and not p) else ('mid' if n else 'low')
        out.append({'uid': r['uid'], 'type': r['type'], 'age_days': round(age, 1),
                    'risk': risk, 'ts': (r['updated_at'] or '')[:16],
                    'text': (r['content'] or '')[:110]})
    out.sort(key=lambda x: ({'high': 0, 'mid': 1, 'low': 2}[x['risk']], -x['age_days']))
    return out


def _residual_facts(content, conflict_entity=''):
    """★退役前的**残留事实检查**：这条记忆里除了冲突的那部分，还有没有独立有效的信息？

    实测动机（2026-09-15 21:35，差点造成真实损失）：
      自动建议退役 `fact-20260913085142-51f4e11f357e`，其内容是
        『DeepSeek官方deepseek-flash已在dsh连通；记录价格为每百万输入1元、输出2元、缓存命中0.02元。』
      —— 冲突的只是前半句（key 连通性），**后半句的价格是独立、仍然有效的事实**，
      而"替代者"里根本没提价格。若按自动建议整条退役，**会连带丢掉价格数据**。

    这是"退役"这个操作的**固有粗粒度**：它按"条"失效，但一条记忆常混装多个事实。
    → 因此：退役必须**人工确认**，且工具要把"残留事实"显式摆出来提醒。

    返回：看起来**不依赖冲突实体**的独立子句列表。
    """
    if not content:
        return []
    resid = []
    for seg in _sentences(content):
        for sub in re.split(r'[；;，,]', seg):
            sub = sub.strip()
            if len(sub) < 8:
                continue
            if conflict_entity and conflict_entity.lower() in sub.lower():
                continue
            if _is_quote_like(sub):
                continue
            resid.append(sub)
    return resid


def retire(uid, by_uid=None, reason='', apply=False, force=False):
    """把一条记忆标记为退役（不删除）。by_uid 给出替代者则写 superseded_by。

    遵循 Graphiti 的 t_invalid：**不删旧边、只置失效时间**。

    ★安全护栏（2026-09-15 21:35 加的，起因见 _residual_facts）：
      若这条记忆里存在"与冲突无关的独立事实"（残留事实），
      则 `apply=True` 会被**拒绝**，除非同时 `force=True`。
      理由：退役是整条级操作，会连带丢掉混装在同一条里的其他正确事实。
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM facts WHERE uid=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return {'ok': False, 'err': 'uid 不存在: %s' % uid}
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 残留事实检查（用 reason 里提到的实体做过滤，尽量准确）
    ents = _strong_entities(reason) if reason else set()
    ce = sorted(ents)[0] if ents else ''
    resid = _residual_facts(row['content'], conflict_entity=ce)

    if apply and resid and not force:
        conn.close()
        return {'ok': False, 'err': '检测到残留事实，拒绝自动退役（需 --force 显式覆盖）',
                'uid': uid, 'residual': resid[:5],
                'hint': '这条记忆里还有与冲突无关的独立事实，整条退役会连带丢失。'
                        '正确做法：先把它拆成两条（一条退役、一条保留），或确认残留事实'
                        '确实已无价值后再 --force。'}

    if not apply:
        conn.close()
        return {'ok': True, 'dry_run': True, 'uid': uid,
                'would_set': {'status': 'superseded', 'valid_to': now, 'superseded_by': by_uid},
                'residual_facts': resid[:5],
                'residual_warning': bool(resid)}
    conn.execute(
        "UPDATE facts SET status='superseded', valid_to=?, invalidated_at=?, superseded_by=?, "
        "updated_at=? WHERE uid=?",
        (now, now, by_uid, now, uid))
    conn.commit()
    conn.close()
    return {'ok': True, 'dry_run': False, 'uid': uid, 'status': 'superseded',
            'valid_to': now, 'superseded_by': by_uid, 'reason': reason,
            'forced_with_residual': bool(resid)}



# ---------------- 体检 ----------------
def audit():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    lines = []
    lines.append('=' * 72)
    lines.append('治理层体检  governance.audit  |  %s' % dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
    lines.append('=' * 72)

    n_active = c.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    n_sup = c.execute("SELECT COUNT(*) FROM facts WHERE status='superseded'").fetchone()[0]
    by_filled = c.execute(
        "SELECT COUNT(*) FROM facts WHERE superseded_by IS NOT NULL AND superseded_by != ''").fetchone()[0]
    vt_filled = c.execute(
        "SELECT COUNT(*) FROM facts WHERE valid_to IS NOT NULL AND valid_to != ''").fetchone()[0]
    conn.close()

    lines.append('  库存        active %d / superseded %d' % (n_active, n_sup))
    lines.append('  失效时间    valid_to 已填 %d 条' % vt_filled)
    lines.append('  替代关系    superseded_by 已填 %d 条' % by_filled)

    conf = find_conflicts()
    selfc = find_self_contradictions()
    exact = detect_explicit_conflicts()
    stale = find_stale(days=30)
    lines.append('  ── 精确检测（可自动化，误报率低）──')
    lines.append('  显式冲突    %d 个实体（跨条·状态断言相反）' % len(exact))
    lines.append('  ── 启发式检测（**仅供人工复核**，勿自动处理）──')
    lines.append('  跨条候选    %d 对（实测大量为互补决策/对比说明的误报）' % len(conf))
    lines.append('  条内候选    %d 条（实测逐条核实全为误报，附在下方供参考）' % len(selfc))
    lines.append('  过期候选    %d 条（30 天以上未更新）' % len(stale))
    hi = sum(1 for s in stale if s['risk'] == 'high')
    lines.append('             其中高风险 %d 条（含否定信号）' % hi)

    lines.append('-' * 72)
    if exact:
        lines.append('  ★精确冲突（建议处理，注意先看残留事实）：')
        for x in exact:
            lines.append('    实体 %s（较新的是 %s）' % (x['entity'], x['newer']))
            lines.append('      ⊕ %s' % x['pos']['sent'][:70].replace('\n', ' '))
            lines.append('      ⊖ %s' % x['neg']['sent'][:70].replace('\n', ' '))
    if conf:
        lines.append('  ☆跨条候选（前 3 对，人工判断用）：')
        for x in conf[:5]:
            lines.append('    [%s] 共享强实体: %s' % (x['why'], ', '.join(x['shared'][:4])))
            lines.append('      A %s | %s' % (x['a']['ts'], x['a']['text'][:74].replace('\n', ' ')))
            lines.append('      B %s | %s' % (x['b']['ts'], x['b']['text'][:74].replace('\n', ' ')))
    if selfc:
        lines.append('  ★条内自矛盾（前 5 条）：')
        for x in selfc[:5]:
            lines.append('    %s 共享 %s' % (x['uid'][:28], ', '.join(x['shared'][:3])))
            lines.append('      ⊕ %s' % x['pos']['text'][:74].replace('\n', ' '))
            lines.append('      ⊖ %s' % x['neg']['text'][:74].replace('\n', ' '))
    if hi:
        lines.append('  ★高风险过期候选（前 5 条）：')
        for s in [x for x in stale if x['risk'] == 'high'][:5]:
            lines.append('    %5.1f天 [%s] %s' % (s['age_days'], s['ts'], s['text'][:78].replace('\n', ' ')))
    lines.append('=' * 72)
    lines.append('  ⚠ 治理层默认只读。退役需显式 `retire --uid X [--by Y] --apply`。')
    lines.append('    理由：判错一次就永久丢事实；老 ≠ 错（本机很多资产几个月不变）。')
    lines.append('=' * 72)
    text = '\n'.join(lines)
    print(text)
    return {'active': n_active, 'superseded': n_sup, 'valid_to_filled': vt_filled,
            'superseded_by_filled': by_filled, 'conflicts': len(conf),
            'self_contradictions': len(selfc),
            'stale': len(stale), 'stale_high': hi}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='记忆中枢治理层：冲突检测 / 时间衰减 / 退役')
    sub = ap.add_subparsers(dest='cmd')
    cf = sub.add_parser('conflicts'); cf.add_argument('--days', type=int, default=30)
    st = sub.add_parser('stale'); st.add_argument('--days', type=int, default=30)
    rt = sub.add_parser('retire')
    rt.add_argument('--uid', required=True); rt.add_argument('--by', default=None)
    rt.add_argument('--reason', default=''); rt.add_argument('--apply', action='store_true')
    rt.add_argument('--force', action='store_true',
                    help='残留事实检查不通过时强制退役（危险，会连带丢事实）')
    sub.add_parser('audit')
    sub.add_parser('self')          # ★条内自矛盾（只读）
    dc = sub.add_parser('decay'); dc.add_argument('query')
    a = ap.parse_args()

    if a.cmd == 'conflicts':
        r = find_conflicts(days_window=a.days)
        print('疑似冲突 %d 对（句子级）：' % len(r))
        for x in r:
            print('  [%s] 共享强实体 %s' % (x['why'], ', '.join(x['shared'][:5])))
            print('    A %s %s' % (x['a']['ts'], x['a']['text'][:95].replace('\n', ' ')))
            print('    B %s %s' % (x['b']['ts'], x['b']['text'][:95].replace('\n', ' ')))
    elif a.cmd == 'self':
        r = find_self_contradictions()
        print('条内自矛盾 %d 条：' % len(r))
        for x in r:
            print('  [%s] 共享 %s' % (x['uid'][:30], ', '.join(x['shared'][:4])))
            print('    ⊕ %s' % x['pos']['text'][:95].replace('\n', ' '))
            print('    ⊖ %s' % x['neg']['text'][:95].replace('\n', ' '))
    elif a.cmd == 'stale':
        r = find_stale(days=a.days)
        print('30 天以上未更新：%d 条' % len(r))
        for s in r[:25]:
            print('  [%s] %5.1f天 %s' % (s['risk'], s['age_days'], s['text'][:88].replace('\n', ' ')))
    elif a.cmd == 'retire':
        print(json.dumps(retire(a.uid, a.by, a.reason, a.apply, getattr(a, 'force', False)),
                         ensure_ascii=False, indent=2))
    elif a.cmd == 'audit':
        audit()
    elif a.cmd == 'decay':
        sys.path.insert(0, HERE)
        import memsearch
        r = memsearch.search_hybrid(a.query, limit=6)
        res = r['results']
        print('=== 衰减前 ===')
        for x in res[:5]:
            print('  %.4f %s' % (x['score'], x['content'][:70].replace('\n', ' ')))
        apply_decay(res)
        print('=== 衰减后 ===')
        for x in res[:5]:
            print('  %.4f (×%.2f, %d天) %s' % (x['score'], x['decay_factor'], x['age_days'],
                                                x['content'][:60].replace('\n', ' ')))
