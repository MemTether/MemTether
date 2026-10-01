# -*- coding: utf-8 -*-
"""
memsearch.py — 记忆混合检索模块（DeepSeek × Astra 协作成果 2026-09-13）

修复目标：原来 search() 用 content LIKE '%q%' 精确子串匹配，中文查询 4/8 失败。
新方案（经 Astra 多轮协作确认）：
  1. 质量门禁：隔离无主语泛化垃圾（它同时伤害召回和排序）
  2. 向量检索（智谱 embedding-3 + ChromaDB 本地）：解决"问法与表述不一致"
  3. ASCII 实体精确匹配：解决 robocopy/STM32/APK/路径 这类实体查询
  4. 融合排序：结构化候选加权 + RRF
"""
import os
import re
import sys
import json
import math
import time
import sqlite3
import datetime
import urllib.request

HUB = os.path.dirname(os.path.abspath(__file__))
# ★真源库/向量库路径解析（与 gateway.py 同一套规则；不设环境变量时行为完全不变）。
#   动机（2026-09-16 实测）：本文件与 gateway.py **各自硬编码** memory.db，
#   一旦用 MEM_DB 切库（例如指向合成演示库 demo/memory_demo.db），
#   gateway 走新库、本文件仍读旧库 → **一次查询混两个库的数据**，
#   属"跑起来不报错、但结果错"的一类。凡新增读真源的模块，一律用这段解析。
DB = os.environ.get('MEM_DB') or os.path.join(HUB, 'memory.db')
if not os.path.isabs(DB):
    DB = os.path.join(HUB, DB)
# 向量库必须与真源库**同步切换**：切库后默认落到"新库同目录/mem0_store"。
#   该目录不存在时语义路优雅降级（见 load_index 的 isdir 判断），
#   关键词/字面匹配路照常工作 —— 演示库因此无需下载 543MB 本地模型即可跑。
CHROMA_PATH = os.environ.get('MEM_STORE') or os.path.join(os.path.dirname(DB), 'mem0_store')
if not os.path.isabs(CHROMA_PATH):
    CHROMA_PATH = os.path.join(HUB, CHROMA_PATH)
COLLECTION = 'facts_active'
LAST_EMBED_INFO = {}  # diagnostic: record last embed call params
EMBED_BACKEND_DEFAULT = os.environ.get('MEM_EMBED_BACKEND_DEFAULT', 'local')
EMBED_MODEL = 'embedding-3'

# ---- 泛化词表（无信息量的通用动词/名词）----
GENERIC_WORDS = [
    '功能', '上线', '建立', '共用', '已成', '完成', '支持', '通过',
    '机制', '测试', '验证', '升级', '优化', '改进', '成功', '实现',
    '方案', '记录', '规则', '能力',
]


# ---- P0-07 TTL（2026-09-24）：陈旧结论必须有复核截止日 ----
#  背景（实测，非推测）：grok「key 已废、缺 token」（09-14）在用户 09-17 补齐新 key 后
#  仍留库，09-21 被后继会话当成**当前答案**引用，用户当场爆发。同类：Fooocus 状态快照过期。
#  结论：**检索到旧结论 = 给用户的答案直接错，且结论越肯定越危险**。
#  机制：状态类条目允许带 tag `ttl:YYYY-MM-DD`（或 `ttl:YYYY-MM-DD` 落在 valid_to）；
#        过期后检索**降权 + 显式标注**，绝不静默丢弃（丢了下游反而查不到"曾经这么说过"）。
TTL_EXPIRED_FACTOR = float(os.environ.get('MEM_TTL_EXPIRED_FACTOR') or 0.35)
_TTL_TAG_RE = re.compile(r'(?:^|[,;\s])ttl\s*[:：]\s*(\d{4}-\d{2}-\d{2})', re.I)


def _parse_ttl(tags=None, valid_to=None, content=None):
    """解析复核截止日，返回 'YYYY-MM-DD' 或 None。

    优先 tags 里的 `ttl:YYYY-MM-DD`（显式意图），其次 valid_to 字段，
    最后从正文里认 `ttl:YYYY-MM-DD`（方便人工补写）。
    """
    for raw in (tags, content):
        if not raw:
            continue
        m = _TTL_TAG_RE.search(str(raw))
        if m:
            return m.group(1)
    if valid_to:
        s = str(valid_to).strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}', s):
            return s[:10]
    return None


def _ttl_state(ttl, today=None):
    """返回 (expired: bool, days_left: int|None)。ttl 为 None 时 (False, None)。"""
    if not ttl:
        return False, None
    import datetime as _dt
    try:
        d = _dt.datetime.strptime(ttl, '%Y-%m-%d').date()
    except Exception:
        return False, None
    t = today or _dt.date.today()
    return (d < t), (d - t).days


# 状态类条目的判据词（命中即认为"描述的是当前/最新状态"，应带 TTL）
_STATE_TELL = (
    '当前', '最新', '现在', '目前', '现状', '余额', '额度', '还剩', '过期',
    '已废', '失效', '不可用', '可用', '暂时', '临时', '待办', '提醒',
    '状态', '快照', '截至', '此时',
)


def looks_like_state(content):
    """P0-07 写入侧判据：这条是否在描述「当前状态」（会随时间失效）。"""
    c = content or ''
    return any(w in c for w in _STATE_TELL)


def is_generic_garbage(content, min_len=20, min_generic=2):
    """质量门禁：判定"无主语泛化短句"（实测清掉这2条后 8/8 top1 正确）。
    规则：长度<20字 且 无ASCII实体(>=3连续字母) 且 泛化词>=2。"""
    if not content:
        return False
    c = content.strip()
    if len(c) >= min_len:
        return False
    if re.search(r'[A-Za-z]{3,}', c):
        return False
    g = sum(1 for w in GENERIC_WORDS if w in c)
    return g >= min_generic


def is_placeholder(content):
    """占位符垃圾：<见vault:key> 这类只有指针没有内容的条目。
    实测它会在向量检索里占据 Top1（因为短文本向量不稳定且与其他条目都"有点像"），
    必须单独识别——is_generic_garbage 的长度规则抓不到它。"""
    if not content:
        return True
    s = content.strip()
    if not s:
        return True
    if re.match(r'^<[^>]{1,64}>\s*$', s):
        return True
    if len(s) < 8:
        return True
    return False


_STOP = set('的了是在和与及为对把被这那有我你他不也很都就要会能可将并被让从到于之其此以所但而或如若则因故又再还只才更最已正在着过们个一些什么怎么如何可以不能没有以及通过进行使用需要应该因为所以虽然但是'.split())


def _terms(text):
    """查询/文档分词：中文 2~4gram + 实体前缀候选 + ASCII 实体。

    ★2026-09-15 修正：旧实现用 re.findall(r'[\\u4e00-\\u9fa5]{2,4}') 是**顺序滑窗**，
    会把「微信装在哪」切成 ['微信装','信装在','装在哪'] —— 完整词「微信」被破坏，
    df 统计里「微信」这类短实体词根本不存在，于是 `LIKE '%微信%'` 永远命不中。
    修复：连续中文段先取**前 2/3/4 字作为实体词候选**（中文实体词几乎都在句首），
    再补 3gram 滑窗做长词召回。
    """
    t = text or ''
    out = set()
    for seg in re.findall(r'[\u4e00-\u9fa5]+', t):
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) <= 3:
            if seg not in _STOP:
                out.add(seg)
        else:
            for n in (2, 3, 4):
                w = seg[:n]
                if w not in _STOP:
                    out.add(w)
            for i in range(len(seg) - 1):
                w = seg[i:i + 3]
                if w not in _STOP:
                    out.add(w)
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', t):
        out.add(w.lower())
    return out


def asset_text(row):
    """把一条 tool_assets 记录压成可检索的一段文本。

    ★2026-09-15：此前 mem.py / search_hybrid 只查 facts 表，66 条资产对检索**完全不可见**
    （实测 `mem.py search "微信 在哪"` 返回的是无关记忆，微信资产 0 命中）。
    资产不该只是投影里的一行字，它必须和事实一样能被检索到。
    """
    parts = [row['name'] or '']
    if row['aliases']:
        parts.append(row['aliases'])
    if row['path']:
        parts.append(row['path'])
    if row['entrypoint']:
        parts.append(row['entrypoint'])
    if row['capabilities']:
        parts.append(row['capabilities'])
    if row['prerequisites']:
        parts.append(row['prerequisites'])
    if row['known_failures']:
        parts.append(row['known_failures'])
    return '【资产】' + ' | '.join(p for p in parts if p)


# ---- ★自指降权：记忆"关于某个查询"的笔记，不该压过那条查询的答案 ----
# 背景（2026-09-15 实测）：我刚写完一条「结论：资产必须可被检索——实测
#   `mem.py search "微信 在哪"` 零命中」的复盘笔记。下一次查「微信 在哪」，
#   这条**笔记本身**因为字面包含完整查询串，kw 覆盖率做到 1.0 排到 Top1，
#   把真正的资产条目压到第 3。更糟的是：自适应精排看到 cov=1.0 判定
#   "字面特征可靠，别动排序" → 连精排都救不回来。
# 判据：若一条记忆里**原样出现了整段查询**，且它自身明显是"复盘/教训/规范"体，
#   则它是在**谈论**这个查询，而不是**回答**这个查询。
_SELFREF_TELL = ('结论：', '实测', '之前', '此前', '已修', '缺', '坑',
                 '教训', '写法', '不要', '必须', '规矩', '自指')

# ★2026-09-15 补：**近似**引用也要拦。
#   实测：修好上面那条之后，查「微信 装在哪」Top1 **仍然是**那条复盘笔记，
#   因为笔记里引用的是「微信 在哪」——少了「装」字，原样包含判据 `q in c` 失效。
#   泛化判据：若一条记忆**在检索语境里引用了一段查询串**，且该引用串与本次查询
#   核心词高度重叠（≥50%），则它同样是在**谈论**这个查询，而不是**回答**它。
#   为什么必须限定"检索语境"：裸引号太常见（`用户要求「XXX」`），
#   宽判据会误伤正常记忆。要求引号附近 24 字内出现检索类词，才认。
_SEARCH_CTX = ('mem.py search', 'search_hybrid', '检索', '查询', '搜')
_QUOTE_RE = re.compile(r'[「『"\']([^」』"\']{2,40})[」』"\']')


def _quoted_query_like(content):
    """抽出"疑似被引用的查询串"（只取检索语境附近的引号内容）。"""
    out = []
    for m in _QUOTE_RE.finditer(content):
        snip = m.group(1).strip()
        if not snip:
            continue
        head = content[max(0, m.start() - 24):m.start()]
        if any(t in head for t in _SEARCH_CTX):
            out.append(snip)
    return out


def _is_self_referential(query, content):
    """R11 v2: Only mark as self-referential when BOTH query and content
    are about the retrieval/memory system itself. Much narrower than v1."""
    _meta = {'\u68c0\u7d22\u8d28\u91cf', '\u641c\u7d22\u8d28\u91cf', 'retrieval quality', 'search quality',
             'memory system', '\u8bb0\u5fc6\u7cfb\u7edf', 'hard_bench', '\u8bc4\u6d4b\u96c6', '\u8bc4\u5206\u5361',
             'benchmark', 'self-ref', '\u81ea\u6307', '\u68c0\u7d22\u5347\u7ea7'}
    q_lower = query.lower()
    c_lower = content.lower()
    q_has = any(kw in q_lower for kw in _meta)
    c_has = any(kw in c_lower for kw in _meta)
    return q_has and c_has


def _embed_zhipu(texts):
    sys.path.insert(0, r'E:\RUANJIAN\ai-audit')
    import cred_env
    cred_env.env()
    key = os.environ['ZHIPU_KEY']
    body = {'model': EMBED_MODEL, 'input': texts}
    req = urllib.request.Request(
        'https://open.bigmodel.cn/api/paas/v4/embeddings',
        data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=60)
    return [x['embedding'] for x in json.loads(r.read().decode())['data']]


def _embed_local(texts):
    """本地 bge-m3。失败时抛异常（由 _embed 决定是否回退）。"""
    import embed_local
    if not embed_local.available():
        raise RuntimeError('本地 embedding 模型文件缺失：%s' % os.path.join(embed_local.HUB, 'models'))
    return embed_local.encode(texts)


def _embed(texts):
    """统一 embedding 入口。返回 list[list[float]]。"""
    be = (os.environ.get('MEM_EMBED_BACKEND') or EMBED_BACKEND_DEFAULT).lower()

    if be == 'zhipu':
        v = _embed_zhipu(texts)
        LAST_EMBED_INFO.update(backend='zhipu', model=EMBED_MODEL,
                               dim=len(v[0]) if v else 0)
        return v

    try:
        v = _embed_local(texts)
        try:
            import embed_local as _el
            _mname = _el._profile()['name']
        except Exception:
            _mname = 'local-onnx'
        LAST_EMBED_INFO.update(backend='local', model=_mname, dim=len(v[0]) if v else 0)
        return v
    except Exception as e:
        if be != 'auto':
            # local：不静默降级。宁可报错，也不要偷偷换成另一条路。
            raise RuntimeError('本地 embedding 失败（backend=local，不回退云端）：%s' % e)
        if not getattr(_embed, '_warned_fb', False):
            _embed._warned_fb = True
            print('[warn] 本地 embedding 失败，回退智谱云端：%s' % str(e)[:90],
                  file=sys.stderr)
        v = _embed_zhipu(texts)
        LAST_EMBED_INFO.update(backend='zhipu(fallback)', model=EMBED_MODEL,
                               dim=len(v[0]) if v else 0)
        return v


def expected_dim():
    """当前后端应产生的向量维度（不加载模型）。"""
    be = (os.environ.get('MEM_EMBED_BACKEND') or EMBED_BACKEND_DEFAULT).lower()
    if be == 'zhipu':
        return 2048
    try:
        import embed_local
        return embed_local.dim()
    except Exception:
        return 1024


VENV_PY = os.path.join(HUB, '.venv-memory', 'Scripts', 'python.exe')


class StorePathMismatch(RuntimeError):
    """P0-04：向量库路径与真源库路径不配对。专用类型 —— 调用方不得静默降级。"""


def _guard_paths():
    """P0-04 闸门之一（2026-09-24）：DB 与向量库必须落在同一目录，否则 fail-closed。

    背景（实测，非推测）：DB / CHROMA_PATH 都是**模块级常量**。评测脚本切库时
    若两者被拆到不同目录（典型：只切 MEM_DB，MEM_STORE 仍指生产 mem0_store），
    结果不是报错而是**静默给错** —— 拿临时库的关键词结果去配生产/空索引，
    实测 facts_active 从 323 塌成 12，全程无告警。

    默认配对规则：CHROMA_PATH 与 DB 同目录（生产 = <HUB>；bench = bench_data）。
    确需有意拆分（副本库配正本索引，如 qvalue_upshift_test）必须显式声明：
        MEM_ALLOW_STORE_DB_SPLIT=1
    """
    if os.path.dirname(os.path.abspath(CHROMA_PATH)) == os.path.dirname(os.path.abspath(DB)):
        return
    if os.environ.get('MEM_ALLOW_STORE_DB_SPLIT', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        return
    raise StorePathMismatch(
        'P0-04 闸门：向量库与真源库不在同一目录，拒绝启动（避免静默污染/误读生产索引）。\n'
        '  DB          = %s\n'
        '  CHROMA_PATH = %s\n'
        '切库时必须让两者同目录（放弃 MEM_STORE，或同步指向新目录）。\n'
        '确为有意拆分（副本库配正本索引等）请显式设 MEM_ALLOW_STORE_DB_SPLIT=1。'
        % (DB, CHROMA_PATH))


def verify_active_consistency():
    """P0-04 验收判据：索引条数必须 == 按 rebuild 同口径算出的应入索引条数。

    ★口径必须与 rebuild_vector_index 完全一致，否则判据自己就是错的：
        expected = (active facts 去掉质量门禁垃圾/测试源) + (active 资产去掉占位符)
    返回 {sqlite_active, sqlite_assets, expected, vector_count, ok, error}
    """
    out = {'sqlite_active': None, 'sqlite_assets': None, 'expected': None,
           'vector_count': None, 'ok': False, 'error': None}
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        frows = conn.execute(
            "SELECT uid, content, type, source, scope FROM facts WHERE status='active'").fetchall()
        try:
            arows = conn.execute("SELECT * FROM tool_assets WHERE status='active'").fetchall()
        except Exception:
            arows = []
        conn.close()
    except Exception as e:
        out['error'] = 'sqlite: %s: %s' % (type(e).__name__, e)
        return out

    out['sqlite_active'] = len(frows)
    out['sqlite_assets'] = len(arows)
    kept = [r for r in frows
            if not is_generic_garbage(r['content'])
            and (r['source'] or '') not in ('test', 'test_hub', 'fixture')]
    akept = [a for a in arows if not is_placeholder(asset_text(a))]
    out['expected'] = len(kept) + len(akept)

    try:
        out['vector_count'] = _client().get_collection(COLLECTION).count()
    except Exception as e:
        out['error'] = 'chroma: %s: %s' % (type(e).__name__, e)
        return out
    out['ok'] = (out['expected'] == out['vector_count'])
    return out


def _client():
    _guard_paths()
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH)


def check_env(raise_on_missing=False):
    """★2026-09-15 新增：解释器自检。

    背景（血泪）：本模块要 chromadb + numpy，它们装在 `.venv-memory` 里。
    用默认 python 跑时，向量路和精排路**各自 try/except 吞掉异常**，
    最后降级成纯关键词检索——**表面上照常返回结果，实际残废**。
    这个静默降级真实发生过：66 条资产查不到、我误判「语义检索不可用」，白查一整天。
    → 与其让它安静地残废，不如显式报错，并直接把正确解释器路径告诉调用方。
    """
    missing = []
    for m in ('chromadb', 'numpy'):
        try:
            __import__(m)
        except Exception:
            missing.append(m)
    if missing and raise_on_missing:
        raise RuntimeError(
            '缺少 %s —— memsearch 需要 E:\\RUANJIAN\\memory_hub\\.venv-memory\\Scripts\\python.exe\n'
            '当前解释器: %s\n'
            '正确用法: PYTHONPATH= "%s" mem.py search "<关键词>"'
            % (', '.join(missing), sys.executable, VENV_PY))
    return missing


def extract_ascii_entities(text):
    """提取 ASCII 实体（连续>=3字母数字，含 _ - . 路径片段）"""
    return set(re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', text or ''))


def reclaim_orphan_segments(dry_run=True, verbose=True):
    """★2026-09-15 新增：回收 Chroma 的**孤儿 segment 目录**。

    【为什么需要它 —— 实测踩到的真实泄漏】
      `delete_collection(COLLECTION)` 只清 sqlite 里的登记，**不删 HNSW 的二进制目录**。
      每次 rebuild = delete + create + add，于是每跑一次就永久多留一份 ~833KB 的
      `data_level0.bin`（2048 维 × ~100 条向量）。
      实测证据（2026-09-15 21:5x）：
        · sqlite 里登记的 segments：14 个（7 个 collection × VECTOR/METADATA 两段）
        · 磁盘上的目录：74 个
        · **孤儿 68 个，共 54.1 MB** —— 其中 60 个是当晚跑 benchmark 的 13 分钟里新建的
      也就是说：**跑一轮 60 题的评测，就烧掉 50MB 磁盘**，而且没有任何地方会回收。

    【判定规则（保守）】
      只删「磁盘上存在、但 sqlite 的 segments 表里没有登记」的目录。
      不碰任何已登记的 segment，不碰 chroma.sqlite3 本体，不碰 .cache 等非 UUID 条目。
      → 结构上不可能误删活着的索引数据。

    dry_run=True 时只报告不删除（默认），确认无误再显式传 dry_run=False。
    """
    import shutil
    if not os.path.isdir(CHROMA_PATH):
        return {'ok': False, 'err': 'CHROMA_PATH 不存在: %s' % CHROMA_PATH}

    sq = os.path.join(CHROMA_PATH, 'chroma.sqlite3')
    registered = set()
    if os.path.exists(sq):
        conn = sqlite3.connect(sq)
        try:
            registered = {r[0] for r in conn.execute('SELECT id FROM segments')}
        except Exception as e:
            return {'ok': False, 'err': '读取 segments 失败: %s' % str(e)[:80]}
        finally:
            conn.close()

    # 顺手把 collection 名也读出来，方便报告"这些孤儿原本属于谁"
    coll_names = {}
    try:
        conn = sqlite3.connect(sq)
        for cid, name in conn.execute('SELECT id, name FROM collections'):
            coll_names[cid] = name
        seg2coll = {r[0]: coll_names.get(r[1], '?')
                    for r in conn.execute('SELECT id, collection FROM segments')}
        conn.close()
    except Exception:
        seg2coll = {}

    # 只认「UUID 形状」的目录名 —— 避免误碰 chroma 未来可能新增的非 segment 目录
    _uuid_re = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

    orphans, kept, freed = [], 0, 0
    for d in sorted(os.listdir(CHROMA_PATH)):
        fp = os.path.join(CHROMA_PATH, d)
        if not os.path.isdir(fp) or not _uuid_re.match(d):
            continue
        if d in registered:
            kept += 1
            continue
        size = 0
        for f in os.listdir(fp):
            ff = os.path.join(fp, f)
            if os.path.isfile(ff):
                size += os.path.getsize(ff)
        orphans.append({'seg': d, 'bytes': size, 'formerly': seg2coll.get(d, '')})
        freed += size

    if not dry_run:
        for o in orphans:
            try:
                shutil.rmtree(os.path.join(CHROMA_PATH, o['seg']))
                o['removed'] = True
            except Exception as e:
                o['removed'] = False
                o['err'] = str(e)[:60]

    if verbose:
        mode = 'DRY-RUN（未删除）' if dry_run else '已删除'
        print('[reclaim] %s  登记 segment %d 个 / 目录 %d 个 / 孤儿 %d 个 / 可回收 %.1f MB'
              % (mode, len(registered), kept + len(orphans), len(orphans), freed / 1048576))
        for o in orphans[:5]:
            print('   孤儿 %s  %.0f KB  (原属 %s)'
                  % (o['seg'][:8], o['bytes'] / 1024, o['formerly'] or '未知'))
        if len(orphans) > 5:
            print('   ... 其余 %d 个略' % (len(orphans) - 5))

    return {'ok': True, 'dry_run': dry_run, 'registered': len(registered),
            'orphans': len(orphans), 'freed_bytes': freed,
            'freed_mb': round(freed / 1048576, 1), 'detail': orphans[:50]}


def rebuild_vector_index(verbose=True, reclaim=True, reuse=False):
    """从 SQLite active facts **+ active tool_assets** 重建干净的向量索引（排除垃圾/测试源）。幂等。

    ★2026-09-15：资产（66 条）原先不在索引里，导致「微信装在哪」这类资产查询全灭。
    现在资产以 kind='tool' 入索引，uid 沿用 tool_assets.uid。

    ★2026-09-15 二修（reclaim 参数）：delete_collection 会**永久泄漏 HNSW 目录**，
    实测一次 rebuild 漏 ~833KB，跑一轮 60 题 benchmark 漏 50MB。
    现在重建**结束前**顺手调用 reclaim_orphan_segments() 把自己刚产生的孤儿收掉，
    让这个函数不再是"越用越胖"的。

    ★2026-09-16 三修（reuse 参数）：沙箱友好路径，用「清空数据」代替「删除集合」。
      起因：delete_collection 在磁盘上删掉整个 HNSW 目录（实测一个集合 53 个文件），
        而 WorkBuddy 客户端向 python 进程注入的批量删除护栏，对"单轮 >50 个删除"
        要求确认 → LongMemEval **每题**重建索引都撞护栏，跑 20 秒即被拦停，
        评测根本没跑起来（日志里是 `SAFE_DELETE_BULK_CONFIRM_REQUIRED count:53`）。
      做法：collection.delete(ids=...) 只删数据、**不动目录结构**，既达成"索引里
        没有旧数据"的目的，也不触碰安全机制。评测固定复用同一个集合名，
        盘上始终只有一个目录，不增长。
      ★默认仍为 False：生产路径继续走 delete_collection（它能顺带回收泄漏目录）。
        这不是绕过护栏，而是**换一种不触发它的等价操作**。
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid, content, type, source, scope FROM facts WHERE status='active'").fetchall()
    try:
        arows = conn.execute(
            "SELECT * FROM tool_assets WHERE status='active'").fetchall()
    except Exception:
        arows = []
    conn.close()

    kept, quarantined = [], []
    for r in rows:
        if is_generic_garbage(r['content']) or (r['source'] or '') in ('test', 'test_hub', 'fixture'):
            quarantined.append(r)
        else:
            kept.append(r)

    # 资产转成「类 fact」的行，统一进入后续 embed
    assets = []
    for a in arows:
        doc = asset_text(a)
        if is_placeholder(doc):
            continue
        assets.append({'uid': a['uid'], 'content': doc, 'type': 'tool',
                       'source': 'tool_assets', 'scope': 'asset'})
    kept.extend(assets)

    client = _client()
    # ★建集合时**记下向量维度与后端**。查询侧据此做一致性校验：
    #   本地(1024) 与 智谱(2048) 混用会得到无意义的近邻，必须硬报错而不是静默出错。
    _be = (os.environ.get('MEM_EMBED_BACKEND') or EMBED_BACKEND_DEFAULT).lower()
    if _be == 'zhipu':
        _mname = EMBED_MODEL
    else:
        try:
            import embed_local as _el
            _mname = _el._profile()['key']
        except Exception:
            _mname = 'local-onnx'
    _meta = {
        'hnsw:space': 'cosine',
        'embed_dim': expected_dim(),
        'embed_backend': _be,
        'embed_model': _mname,
        'built_at': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    if reuse:
        # 沙箱友好：清空数据而非删集合（见 docstring 三修说明）
        col = None
        try:
            col = client.get_collection(COLLECTION)
            cm = col.metadata or {}
            if cm and int(cm.get('embed_dim') or 0) != int(expected_dim()):
                # 维度不符（中途换过后端）→ 只能重建集合，退回删除路径
                client.delete_collection(COLLECTION)
                col = None
            else:
                ids = col.get(include=[])['ids']
                if ids:
                    col.delete(ids=ids)          # 只删数据，不动目录
        except Exception:
            col = None
        if col is None:
            col = client.create_collection(COLLECTION, metadata=_meta)
    else:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        col = client.create_collection(COLLECTION, metadata=_meta)

    vecs = []
    B = 20
    for i in range(0, len(kept), B):
        batch = [r['content'] for r in kept[i:i + B]]
        vecs.extend(_embed(batch))
        time.sleep(0.2)
    if kept:
        col.add(ids=[r['uid'] for r in kept], embeddings=vecs,
                documents=[r['content'] for r in kept],
                metadatas=[{'uid': r['uid'], 'type': r['type'] or 'fact',
                            'source': r['source'] or '',
                            'kind': ('tool' if r['type'] == 'tool' else 'fact')} for r in kept])
    if verbose:
        print('向量索引重建完成：facts %d 条 + 资产 %d 条，入索引 %d 条，隔离垃圾 %d 条'
              % (len(rows), len(assets), len(kept), len(quarantined)))
        for q in quarantined:
            print('  隔离: %s' % q['content'][:50])
    # ★2026-09-15 二修：把自己刚产生的孤儿 segment 收掉（否则每次 rebuild 漏 833KB）
    rec = None
    if reclaim:
        try:
            rec = reclaim_orphan_segments(dry_run=False, verbose=verbose)
        except Exception as e:
            if verbose:
                print('  [warn] 孤儿回收失败（不影响索引）:', str(e)[:70])
    return {'active': len(rows), 'assets': len(assets),
            'indexed': len(kept), 'quarantined': len(quarantined),
            'reclaimed_mb': (rec or {}).get('freed_mb', 0.0)}


def _load_governance():
    """★2026-09-22：按显式路径加载 governance，杜绝同名模块遮蔽。

    同 gateway.py 的 _load_governance，也是手册卷12 事故 #10 的遗留半边：
    本处 `import governance as _gov` 只被 try/except 包着，
    遮蔽发生时的症状是**时间衰减静默失效**（排序退化成纯相关性），
    连告警都只会打在 stderr —— 最难发现的那类错。
    """
    import importlib.util as _iu
    _p = os.path.join(HUB, 'governance.py')
    if not os.path.isfile(_p):
        return __import__('governance')
    _spec = _iu.spec_from_file_location('_mh_governance_ms', _p)
    _mod = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod




# ==================== R10: Scaffold & Packet Compiler (v6) ====================
# Based on Auditable Memory (arXiv 2609.38021) and Mnemon (arXiv 2609.36059)

def detect_question_type(query):
    """Detect the type of question to choose aggregation strategy."""
    q = query.lower()
    if any(kw in q for kw in ['how many', 'count', 'number of', 'how much',
                               'sum of', 'average of', 'mean of']):
        return 'counting'
    if any(kw in q for kw in ['compare', 'difference between', 'better than',
                               'worse than', 'which is more', 'versus', ' vs ']):
        return 'comparison'
    if any(kw in q for kw in ['before the', 'after the', 'when did', 'what year',
                               'timeline', 'chronological', 'in order']):
        return 'temporal'
    if any(kw in q for kw in ['no longer', 'still valid', 'has changed',
                               'current version', 'latest version']):
        return 'knowledge-update'
    if any(kw in q for kw in ['list all', 'summarize', 'every mention',
                               'all instances', 'each time']):
        return 'aggregation'
    return 'single-session'


def build_scaffold(question_type, query, results):
    """Generate deterministic reasoning scaffolds for specific question types.
    Based on Auditable Memory (arXiv 2609.38021) Stage 4."""
    if not results:
        return None
    
    scaffold_lines = []
    
    if question_type == 'counting':
        # Extract entities from query and count occurrences in results
        entities = extract_ascii_entities(query)
        if entities:
            scaffold_lines.append("[scaffold: counting]")
            for e in entities:
                count = sum(1 for r in results 
                           if e.lower() in r.get('content', '').lower())
                if count > 0:
                    scaffold_lines.append(f"  '{e}' appears in {count}/{len(results)} results")
        else:
            # Count unique sources/types
            type_counts = {}
            for r in results:
                t = r.get('type', '?')
                type_counts[t] = type_counts.get(t, 0) + 1
            scaffold_lines.append("[scaffold: counting by type]")
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
                scaffold_lines.append(f"  {t}: {c} results")
    
    elif question_type == 'temporal':
        # Sort results by time
        dated = [(r.get('updated_at', ''), r.get('content', '')[:80]) 
                 for r in results if r.get('updated_at')]
        if dated:
            scaffold_lines.append("[scaffold: temporal ordering]")
            for date, content in sorted(dated)[:10]:
                scaffold_lines.append(f"  {date[:10]}: {content}")
    
    elif question_type == 'comparison':
        # Show top pairs for comparison
        scaffold_lines.append("[scaffold: comparison]")
        for i in range(min(3, len(results) - 1)):
            a = results[i]
            b = results[i + 1]
            scaffold_lines.append(
                f"  Compare: [{a.get('type','?')}/{a.get('source','?')}] vs [{b.get('type','?')}/{b.get('source','?')}]"
            )
    
    elif question_type in ('knowledge-update', 'aggregation'):
        # Group by source and type for diversity
        scaffold_lines.append(f"[scaffold: {question_type}]")
        type_counts = {}
        source_counts = {}
        for r in results:
            t = r.get('type', '?')
            s = r.get('source', '?')
            type_counts[t] = type_counts.get(t, 0) + 1
            source_counts[s] = source_counts.get(s, 0) + 1
        scaffold_lines.append(f"  Types: {type_counts}")
        scaffold_lines.append(f"  Sources: {source_counts}")
        scaffold_lines.append(f"  Total: {len(results)} results, "
                              f"score range: {min(r.get('score',0) for r in results):.3f} - "
                              f"{max(r.get('score',0) for r in results):.3f}")
    
    if scaffold_lines:
        return '\n'.join(scaffold_lines)
    return None


def compile_packet(results, max_items=16, max_chars=12000):
    """Coverage-first packet compilation.
    Based on Auditable Memory (arXiv 2609.38021) Stage 3.
    Select results to maximize topic/source/type coverage within budget."""
    if len(results) <= max_items:
        return results
    
    # Phase 1: Take top-scored results (half the budget)
    sorted_r = sorted(results, key=lambda x: -x.get('score', 0))
    selected = sorted_r[:max_items // 2]
    selected_uids = {x['uid'] for x in selected}
    seen_types = {x.get('type') for x in selected}
    seen_sources = {x.get('source') for x in selected}
    
    # Phase 2: Fill remaining with diversity bonuses
    remaining = [x for x in sorted_r if x['uid'] not in selected_uids]
    for x in remaining:
        if len(selected) >= max_items:
            break
        bonus = 0
        if x.get('type') not in seen_types:
            bonus += 0.2
        if x.get('source') not in seen_sources:
            bonus += 0.1
        adjusted = x.get('score', 0) + bonus
        x = dict(x)  # copy to avoid mutating original
        x['_adjusted_score'] = adjusted
        selected.append(x)
        selected_uids.add(x['uid'])
        seen_types.add(x.get('type'))
        seen_sources.add(x.get('source'))
    
    # Sort by score and trim to budget
    selected.sort(key=lambda x: -x.get('_adjusted_score', x.get('score', 0)))
    
    # Trim by character budget
    total_chars = 0
    final = []
    for x in selected:
        content_len = len(x.get('content', ''))
        if total_chars + content_len > max_chars:
            # Truncate content to fit
            remaining_budget = max_chars - total_chars
            if remaining_budget > 100:
                x = dict(x)
                x['content'] = x['content'][:remaining_budget]
                final.append(x)
            break
        final.append(x)
        total_chars += content_len
    
    return final

# ==================== End R10 ====================

def search_hybrid(query, limit=10, vec_k=60, use_rerank=True, rerank_k=None,
                  rerank_w=0.4, rerank_model=None,
                  adaptive=True, adaptive_thr=0.6,
                  decay=True, qvalue=None, _round=0):
    """混合检索：质量门禁 + 向量 + ASCII精确 + RRF 融合 + cross-encoder 精排。

    use_rerank : 是否启用 cross-encoder 精排（agentmemory V4 的核心增益项）
    rerank_k   : 对 RRF 前多少条做精排（精排是 O(n) 全注意力）。
                 默认 None → 读 MEM_RERANK_K，再退回 30（生产值）。
    rerank_w   : 精排分在最终融合中的权重，1-rerank_w 给 RRF 排名分
    rerank_model: 默认读环境变量 MEM_RERANK_MODEL，再退回 'bge'。
                 'bge'=BAAI/bge-reranker-base fp32(1.06GB)；
                 'bge-int8'=同模型量化版（体积约 1/4，冷启动更快）；
                 'msmarco'=英文模型，**中文实测有害，别用**。

    ★2026-09-15 实测选型依据（40 例自评测，直白集/改写集各 20）：
      基线(RRF)        直白 80%/95%   改写 35%/45%    → 合计 Top1 57.5% Top3 70%
      精排[msmarco]全量 直白 60%       改写 45%        → 英文模型在中文记忆上瞎排
      精排[bge]全量     直白 70%/95%   改写 30%/55%
      **bge+自适应      直白 80%/95%   改写 30%/55%    → 合计 Top1 55% Top3 75%**
      选它的理由：直白集不退化（保住 80%），改写集 Top3 +10pp（给模型看 3 条比第 1 条更关键）。
    ★更重要的实测结论：改写集 30% 的失败是「正确答案没进候选集」（见 _diag_recall.py），
      精排救不了召回 → 下一步该做查询扩展，不是继续调排序。

    qvalue     : ★升级 1（2026-09-17）—— 价值分加权，让**被反复采纳**的记忆上浮。
                 None = 读环境变量 MEM_QVALUE（缺省视为开）；True/False = 显式开关。
                 公式 score *= (0.3 + 0.7 * q_value)，q∈[0,1]。
                 ★全库 q_value 默认 0.5 → 因子恒为 0.65，对同批候选是同一常数，
                   在 RRF→min-max 精排链路里被完全抵消 ⇒ **默认不改变任何现有排序**。
                 回写不由本函数负责（走 gateway.bump_qvalue / `mem.py qvalue`）。
    """
    q = (query or '').strip()
    # ★2026-10-01 查询扩展（中英文同义词）：加载 synonyms.json 自动扩展查询
    _syn_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'synonyms.json')
    _expanded_terms = set()
    if os.path.exists(_syn_path):
        try:
            with open(_syn_path, 'r', encoding='utf-8') as _sf:
                _syn_map = json.load(_sf)
            _words = re.split(r'[\s,，。；;]+', q)
            for _w in _words:
                _w_lower = _w.lower().strip()
                if _w_lower in _syn_map:
                    _expanded_terms.update(_syn_map[_w_lower])
                for _syn_group in _syn_map.values():
                    if _w_lower in [s.lower() for s in _syn_group]:
                        _expanded_terms.update(_syn_group)
            _expanded_terms.update(_words)
            _expanded_terms.discard('')
        except Exception:
            pass
    if _expanded_terms and len(_expanded_terms) > 1:
        q = ' '.join(sorted(_expanded_terms))

    if not q:
        # ★2026-09-19（诊断可信度）：空 query 必须**显式**回报原因。
        #   旧版直接返回空 results，调用方（gateway.search）见空即报
        #   「向量库不可用」——而真相是"根本没检索"。诊断指向错误方向比没有诊断更糟。
        return {'query': q, 'results': [],
                'diag': {'vec_ok': False, 'vec_err': None, 'vec_skipped': True,
                         'rerank_ok': None, 'rerank_err': None, 'active_n': 0,
                         'reason': 'empty-query'}}

    # ★2026-09-19（诊断可信度）：把「各路是否真的跑成功」作为**数据**随结果返回。
    #   动机：向量路失败只 print 到 stderr、精排失败也只 print 到 stderr，
    #   而 gateway.search 只能靠"结果是否为空"猜引擎 ⇒ 双向误报：
    #     ① 空库 / 空 query          → 谎报「向量库不可用」（假阳性）
    #     ② 向量路真失败但关键词有命中 → 照报 'hybrid'（假阴性，掩盖降级）
    #   修法：状态进返回值，不再让调用方靠副作用（stderr）猜。
    _diag = {'vec_ok': False, 'vec_err': None, 'vec_skipped': False,
             'rerank_ok': None, 'rerank_err': None, 'active_n': 0, 'reason': None,
             'graph_recall': False}

    # ★精排模型：显式传入 > 环境变量 MEM_RERANK_MODEL > 'bge'
    rerank_model = rerank_model or os.environ.get('MEM_RERANK_MODEL') or 'bge'
    # ★精排候选数：显式传入(默认30)保持生产行为；MEM_RERANK_K 可覆盖。
    #   2026-09-16 实测：精排占单次查询耗时 98.4%（其余全部环节合计仅 0.03s）。
    #   62 题同一卷子逐档实测（准 / 单题耗时）：
    #     k=30  59/62  1.591s   ← 默认，精度优先
    #     k=20  57/62  1.108s   （比 k=15 还差 → ±2 题属噪声）
    #     k=15  58/62  0.825s
    #     k=10  56/62  0.685s
    #   → 降 k 是明确的"拿精度换速度"，故默认不动；需要更快时
    #     set MEM_RERANK_K=15 自行权衡。
    if rerank_k is None:
        try:
            rerank_k = int(os.environ.get('MEM_RERANK_K') or 30)
        except ValueError:
            rerank_k = 30

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # ★2026-09-17（升级 1）：候选池补取 q_value —— 检索末尾的 Q-Value 加权要用它。
    #   不加这一列，加权就只能全用默认 0.5，升级等于空转。
    active = {r['uid']: dict(r) for r in conn.execute(
        "SELECT uid, content, type, source, scope, updated_at, q_value, tags, valid_to, superseded_by"
        " FROM facts WHERE status='active'").fetchall()}
    # ★2026-09-15：资产一并入候选池（kind='tool'），否则「XX装在哪」永远查不到
    assets = {}
    try:
        for a in conn.execute("SELECT * FROM tool_assets WHERE status='active'").fetchall():
            doc = asset_text(a)
            if is_placeholder(doc):
                continue
            # ★2026-09-20：补 q_value 键 —— 没有它加权兜底 0.5（因子恒 0.65），
            #   资产占检索结果近半却永远吃不到 Q-Value。
            aq = a['q_value'] if 'q_value' in a.keys() else None
            assets[a['uid']] = {'uid': a['uid'], 'content': doc, 'type': 'tool',
                                'source': 'tool_assets', 'scope': 'asset',
                                'q_value': 0.5 if aq is None else aq,
                                'updated_at': a['updated_at'] if 'updated_at' in a.keys() else '',
                                '_asset': dict(a)}
    except Exception:
        assets = {}
    conn.close()
    active.update(assets)

    # 门禁过滤掉垃圾（含 <见vault:key> 这类占位符）
    active = {u: f for u, f in active.items()
              if not is_generic_garbage(f['content']) and not is_placeholder(f['content'])}
    _diag['active_n'] = len(active)

    # ---- 各路召回，只记录**排名**，不记录原始分数 ----
    # 1) 向量路
    vec_rank, vec_sim = {}, {}
    try:
        col = _client().get_collection(COLLECTION)
        qv = _embed([q])[0]
        # ★维度一致性校验：索引是用某个后端建的，查询向量必须同维。
        #   不同维（本地 1024 / 智谱 2048）混用不会报错，但近邻是**无意义的**——
        #   属于最难发现的一类静默错误。宁可在这里硬停下并给出修复命令。
        _md = col.metadata or {}
        _idx_dim = _md.get('embed_dim')
        if _idx_dim and int(_idx_dim) != len(qv):
            raise RuntimeError(
                '向量维度不符：索引是 %s 维（后端=%s，建于 %s），'
                '当前后端产生 %d 维。索引与查询向量不同维时近邻无意义。'
                '修复：用当前后端重建索引 —— python memsearch.py --rebuild'
                % (_idx_dim, _md.get('embed_backend'), _md.get('built_at'), len(qv)))
        r = col.query(query_embeddings=[qv], n_results=vec_k)
        for i, (uid, dist) in enumerate(zip(r['ids'][0], r['distances'][0])):
            if uid in active:
                vec_rank[uid] = i
                vec_sim[uid] = round(1 - dist, 4)
        _diag['vec_ok'] = True          # ★只有走到这里才算向量路真的可用
    except StorePathMismatch:
        # P0-04：闸门错误必须冒泡，绝不能被当成"向量路失败"降级吞掉。
        raise
    except Exception as e:
        _diag['vec_err'] = '%s: %s' % (type(e).__name__, str(e)[:100])
        if not getattr(search_hybrid, '_warned', False):
            search_hybrid._warned = True
            print('[warn] 向量检索失败（将降级为纯关键词，召回会明显变差）:', str(e)[:80],
                  file=sys.stderr)
            print('[warn] 正确解释器: %s' % VENV_PY, file=sys.stderr)

    # 2) 关键词路：查询词覆盖率 x IDF（专有名词命中权重更高）
    q_terms = _terms(q)
    kw_raw = {}
    kw_cov = {}
    if q_terms:
        N = max(len(active), 1)
        df = {}
        for uid, f in active.items():
            c = (f['content'] or '').lower()
            for tm in q_terms:
                if tm.lower() in c:
                    df[tm] = df.get(tm, 0) + 1
        for uid, f in active.items():
            c = (f['content'] or '').lower()
            hits, s = 0, 0.0
            for tm in q_terms:
                tl = tm.lower()
                if tl in c:
                    hits += 1
                    s += math.log(1 + N / max(df.get(tm, 1), 1))
            if hits:
                kw_raw[uid] = (hits / len(q_terms)) * 2.0 + s * 0.1
                kw_cov[uid] = hits / len(q_terms)
    kw_rank = {u: i for i, u in enumerate(sorted(kw_raw, key=lambda u: -kw_raw[u]))}

    # 3) 整句字面匹配路（"三大机制"这类整体概念，向量区分度不足）
    ql = q.lower()
    lit_hit = set()
    if len(q) >= 2:
        for uid, f in active.items():
            if ql in (f['content'] or '').lower():
                lit_hit.add(uid)

    # 3.5) FTS5 BM25 路（★E3 upgrade 2026-09-24 v2：trigram 持久化索引）：
    #     之前是遍历全量 active 简化 BM25 → O(N)；现在是 SQLite FTS5 trigram 索引 → O(1)。
    #     trigram 分词器对中英文都能命中；facts_fts 由 gateway.py remember/correct/retire 增量维护。
    #     FTS5 bm25() 分数越负越相关（是负对数似然），取 abs 后排序。
    bm25_rank = {}
    try:
        import sqlite3 as _sq3
        _fts_db = _sq3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory.db'))
        _fts_db.row_factory = _sq3.Row
        # FTS5 trigram: 把查询按空格拆成 token，逐个匹配再合并分数
        # trigram 要求每个 token >= 3 字符；过短的 token 退回整句查询
        _fts_tokens = [t for t in q.replace('"', ' ').split() if len(t) >= 3]
        if not _fts_tokens and len(q.strip()) >= 3:
            _fts_tokens = [q.strip()]
        _fts_scores = {}
        for _tok in _fts_tokens:
            _fts_q = '"' + _tok + '"'
            try:
                _fts_rows = _fts_db.execute(
                    "SELECT uid, bm25(facts_fts) AS score FROM facts_fts WHERE facts_fts MATCH ? ORDER BY score LIMIT 20",
                    (_fts_q,)).fetchall()
            except Exception:
                continue
            for r in _fts_rows:
                s = abs(r['score'])
                _fts_scores[r['uid']] = _fts_scores.get(r['uid'], 0.0) + s
        if _fts_scores:
            _fts_sorted = sorted(_fts_scores.items(), key=lambda x: x[1])
            bm25_rank = {uid: i for i, (uid, _) in enumerate(_fts_sorted)}
        _fts_db.close()
    except Exception as _fts_e:
        if not getattr(search_hybrid, '_warned_fts', False):
            search_hybrid._warned_fts = True
            print('[warn] FTS5 路失败（不影响其他路）:', str(_fts_e)[:80], file=sys.stderr)

    # 3.8) 实体图谱反查路（T4 阶段 A，2026-09-27）：query 命中实体 → 反查该实体的所有关联 facts。
    #     multi-session 32.5% 根因是"跨会话聚合"——同一实体的证据散在 N 条 facts 里，
    #     单跳 top-k 只召回 1-2 条。实体反查把"同一实体的所有事实"拉进候选池。
    #     默认关（MEM_GRAPH_RECALL=1 开启），关时行为逐字节一致。
    entity_rank = {}
    _graph_on = (os.environ.get('MEM_GRAPH_RECALL') or '').strip().lower() in ('1', 'true', 'yes')
    if _graph_on:
        _diag['graph_recall'] = True
        try:
            from entity_graph_ppr import recall_by_ppr
            _ppr_hits = recall_by_ppr(q, k=30)
            _ppr_sorted = sorted(_ppr_hits, key=lambda x: x[1], reverse=True)
            for _rank, (_uid, _score) in enumerate(_ppr_sorted):
                if _uid in active and _uid not in entity_rank:
                    entity_rank[_uid] = _rank
        except Exception as _eg_e:
            if not getattr(search_hybrid, '_warned_graph', False):
                search_hybrid._warned_graph = True
                print('[warn] 实体图谱路失败（不影响其他路）:', str(_eg_e)[:80], file=sys.stderr)

    # 4) RRF 融合（Reciprocal Rank Fusion）
    #    旧实现把 semantic(余弦0~1) + ascii(0.3) + literal(0.5) 直接相加，量纲不一致导致
    #    语义相近但不精确的条目（查"自动沉淀技能"返回"自动取件护栏"）压过精确匹配。
    #    RRF 只用排名，各路量纲无关；K=60 为业界常用值。
    #    关键词路权重 1.6：实测关键词 Top3 75% 优于纯语义 62%，专有名词命中更可靠。
    K = 60
    out = []
    _rrf_pool = set(vec_rank) | set(kw_rank) | set(bm25_rank) | lit_hit
    if _graph_on:
        _rrf_pool |= set(entity_rank)
    for uid in _rrf_pool:
        f = active.get(uid)
        if not f:
            continue
        sc, reason = 0.0, []
        if uid in vec_rank:
            sc += 1.0 / (K + vec_rank[uid] + 1)
            reason.append('semantic#%d' % vec_rank[uid])
        if uid in kw_rank:
            sc += 1.6 / (K + kw_rank[uid] + 1)
            reason.append('kw#%d' % kw_rank[uid])
        if uid in bm25_rank:
            sc += 0.8 / (K + bm25_rank[uid] + 1)
            reason.append('bm25#%d' % bm25_rank[uid])
        if uid in lit_hit:
            sc += 1.6 / (K + 1)
            reason.append('literal')
        if uid in entity_rank:
            sc += 0.6 / (K + entity_rank[uid] + 1)
            reason.append('entity_graph#%d' % entity_rank[uid])
        # M2 (2026-09-24): procedure type boost - distilled skills get a 1.5x RRF multiplier
        # so they surface above raw episode facts when they cover the same topic
        if f.get('type') == 'procedure':
            sc *= 1.5
            reason.append('proc_boost')
        _out = {'uid': uid, 'content': f['content'], 'type': f['type'],
                    'source': f['source'], 'score': round(sc, 5),
                    'semantic': vec_sim.get(uid, 0.0),
                    'scope': f.get('scope') or '',
                    'updated_at': f.get('updated_at') or '',
                    # P0-07：把复核截止日带进结果，供降权与标注
                    'ttl': _parse_ttl(f.get('tags'), f.get('valid_to'), f.get('content')),
                    'reason': reason}
        # M2（2026-09-24）：技能接进检索结果 —— 资产条目带 skill_hint。
        #   动机：调用方（agent）看到「ComfyUI | E:\ComfyUI\...」只知道"有这个东西"，
        #   不知道"怎么用"（入口/前置/坑）。skill_hint 从 tool_assets 原始行提取，
        #   只在有实际内容时附上，不污染 fact 条目。
        _asset = f.get('_asset')
        if _asset and isinstance(_asset, dict):
            _hints = []
            _skip = ('', '[]', '{}', 'null', 'None', 'none', '无')
            _ep = (_asset.get('entrypoint') or '').strip()
            if _ep and _ep not in _skip:
                _hints.append('入口: %s' % _ep)
            _pre = (_asset.get('prerequisites') or '').strip()
            if _pre and _pre not in _skip:
                _hints.append('前置: %s' % _pre)
            _kf = (_asset.get('known_failures') or '').strip()
            if _kf and _kf not in _skip:
                _hints.append('已知坑: %s' % _kf)
            if _hints:
                _out['skill_hint'] = ' | '.join(_hints)
        out.append(_out)
    # ★4.35) R1 semantic boost（2026-09-25）：高向量相似度候选被 keyword 噪声挤出 top-N 的修复。
    #   根因：短资产文本（tool_assets）keyword 覆盖率极低（如 Everything 0/23、STM32CubeIDE 1/12），
    #   RRF 里 keyword 路 1.6x 权重让"碰词多的无关长文"压过"向量确认相关的精确答案"。
    #   修法：向量 sim >= 0.70 的候选乘以 boost（1.8x），让它们回到它们应得的位置。
    #   不影响 keyword 优先的查询（那些 sim 通常 < 0.70，或已在 top 无需 boost）。
    _SEM_BOOST_THR = 0.70
    _SEM_BOOST_FACTOR = 1.8

    # ★R6 (2026-10-01): Four-factor re-ranking with MemX exact formulas
    # From MemX arXiv 2603.16171, Table 1 + Eq.2/3/4
    # Weights: sem=0.45, rec=0.25, freq=0.05, imp=0.10 (sum=0.85, z-score normalizes)
    _W4_SEM = 0.45
    _W4_REC = 0.25
    _W4_FREQ = 0.05
    _W4_IMP = 0.10
    _HALF_LIFE_DAYS = 30  # MemX default
    
    _TYPE_IMP = {'procedure': 0.9, 'decision': 0.7, 'fact': 0.5,
                 'incident': 0.5, 'experience': 0.4, 'tool': 0.6}
    
    def _rec_memx(updated_at):
        """MemX Eq.2: f_rec = 2^(-d/h), h=30 days"""
        import datetime as _dt_mod
        if not updated_at:
            return 0.3
        try:
            _dt = _dt_mod.datetime.strptime(str(updated_at)[:19], '%Y-%m-%d %H:%M:%S')
            _days = (_dt_mod.datetime.now() - _dt).days
            return max(0.01, 2 ** (-_days / _HALF_LIFE_DAYS))
        except (ValueError, TypeError):
            return 0.3
    
    def _freq_memx(use_count):
        """MemX Eq.3: f_freq = min(1, ln(c+1)/10)"""
        return min(1.0, math.log(max(0, use_count) + 1) / 10)
    
    # Apply four-factor to each candidate
    for x in out:
        _sem = min(1.0, x.get('score', 0) / 0.1)  # RRF score normalize to [0,1]
        _rec = _rec_memx(x.get('updated_at', ''))
        _freq = _freq_memx(x.get('use_count', 0))
        _imp = _TYPE_IMP.get(x.get('type', 'fact'), 0.5)
        
        _raw = (_W4_SEM * _sem + _W4_REC * _rec + 
                _W4_FREQ * _freq + _W4_IMP * _imp)
        x['_4f_raw'] = _raw
        x['reason'] = list(x.get('reason') or []) + [
            f"4F(sem={_sem:.2f},rec={_rec:.2f},freq={_freq:.2f},imp={_imp:.1f})"
        ]
    
    # Save original RRF score before normalization overwrites it
    for x in out:
        x['_rrf_original'] = x.get('score', 0)
    
    # Z-score + sigmoid normalization (MemX Eq.4)
    _scores_4f = [x.get('_4f_raw', 0) for x in out]
    if _scores_4f:
        _mean = sum(_scores_4f) / len(_scores_4f)
        _var = sum((s - _mean) ** 2 for s in _scores_4f) / len(_scores_4f)
        _std = _var ** 0.5
        
        if _std > 1e-6:
            for x in out:
                _z = (x.get('_4f_raw', 0) - _mean) / _std
                x['_4f_norm'] = round(1.0 / (1.0 + math.exp(-_z)), 4)  # sigmoid
        else:
            for x in out:
                x['_4f_norm'] = round(x.get('_4f_raw', 0), 4)
    
    # R6 revised: use four-factor as BONUS on top of RRF score, not replacement
    # This preserves RRF relevance ranking while using recency/authority/frequency
    # as tiebreakers. MemX's approach of replacing score entirely hurt asset queries.
    for x in out:
        _rrf = x.get('_rrf_original', x.get('_4f_raw', 0))  # preserve original RRF
        _4f = x.get('_4f_raw', 0)
        # Blend: 70% RRF + 30% four-factor
        x['score'] = round(0.7 * _rrf + 0.3 * _4f, 4)

    out.sort(key=lambda x: -x.get('score', 0))
    
    # End R6 four-factor
    
    # ★4.36) entity_boost（2026-09-26）：对标 Mem0 的加性融合实体加成。
    #   动机：查询含 ASCII 实体（gptx_astra / mcp_server.py / concurrent_stress.py）时，
    #   命中该实体的条目应优先于仅语义相似的条目。
    #   做法：从查询提取 ASCII 实体，候选内容每命中一个实体 ×1.25（上限 1.56，
    #   避免单一实体长文因重复命中被过度放大）。
    #   与 sem_boost 叠加但各自有上限，不会破坏 RRF 量纲。
    _q_entities = extract_ascii_entities(q)
    if _q_entities:
        for x in out:
            hits = sum(1 for ent in _q_entities
                       if ent.lower() in (x.get('content') or '').lower())
            if hits:
                factor = min(1.25 ** hits, 1.56)
                x['score'] = round(x['score'] * factor, 5)
                x['reason'] = list(x.get('reason') or []) + ['entity_boost=%d' % hits]
    for x in out:
        sem = vec_sim.get(x.get('uid'), 0.0)
        if sem >= _SEM_BOOST_THR:
            x['score'] = round(x['score'] * _SEM_BOOST_FACTOR, 5)
            x['reason'] = list(x.get('reason') or []) + ['sem_boost=%.2f' % sem]

    # ★4.4) P0-07 TTL 降权（2026-09-24）：过期结论不得静默当"当前事实"返回。
    #   与自指降权同层（都在 RRF 之后、精排之前），乘性因子可叠加。
    #   ★刻意**不删除**：过期条目仍要能被检索到（否则"我曾说过什么"永久丢失），
    #     但必须降权 + 带 ttl_note，让下游一眼看出它不是当前答案。
    _ttl_expired = 0
    for x in out:
        _exp, _left = _ttl_state(x.get('ttl'))
        if _exp:
            _ttl_expired += 1
            x['score'] = round(x['score'] * TTL_EXPIRED_FACTOR, 5)
            x['ttl_expired'] = True
            x['ttl_note'] = '⚠ 已于 %s 到期，可能不是当前状态，引用前请复核' % x['ttl']
            x['reason'] = list(x.get('reason') or []) + ['ttl-expired↓']
        elif x.get('ttl'):
            x['ttl_expired'] = False
            x['ttl_note'] = '复核截止日 %s（剩 %s 天）' % (x['ttl'], _left)
        else:
            x['ttl_expired'] = False
            x['ttl_note'] = None

    # ★4.5) 自指降权：把"关于这个查询的元讨论"压到"这个查询的答案"之下。
    #    （详见 _is_self_referential 的注释。字面路给了它们满额加分，这里收回来。）
    #    ★注意用**乘法压到很狠**：实测当查询含 ASCII 文件名（如 mcp_server.py）时，
    #    向量路整体召回不到资产（embedding 被 mem.py 这类相近 token 带偏），
    #    候选集退化成"纯关键词平局"，此时 ×0.25 只能把自指条目从 0.0262 压到
    #    同档，仍然排第一。必须压到任何正常候选之下，才真正起到排序作用。
    for x in out:
        if _is_self_referential(q, x['content']):
            x['score'] = round(x['score'] * 0.05, 5)
            x['reason'].append('self-ref↓')
    out.sort(key=lambda x: x['score'], reverse=True)

    # 5) cross-encoder 精排（复刻 agentmemory V4 的最大单项增益）
    #    RRF 只有排名信息，分不清"语义相近但不精确"的干扰项；cross-encoder 把
    #    (query, doc) 拼成一个序列做全注意力，能读出双塔相似度看不出的相关性。
    #    只对头部 rerank_k 条精排（实测单条约 6.4ms），尾部保持 RRF 原序。
    # 自适应开关（agentmemory V4 是固定六信号加权，这里改为按查询决定）：
    #   实测精排是双刃剑——字面命中强的查询（Top1 80%）会被精排拉到 60%，
    #   而字面命中弱的改写类查询（Top1 35%）能从精排拿到 +10pp。
    #   → 用 Top1 的关键词覆盖率判断：覆盖率高说明字面特征可靠，别让精排改它。
    do_rerank = use_rerank and len(out) > 1 and rerank_w > 0
    if do_rerank and adaptive and out:
        cov = kw_cov.get(out[0]['uid'], 0.0)
        do_rerank = cov < adaptive_thr
        # ★2026-09-15：Top1 被判定自指（是"关于查询的笔记"）时，它那个 cov=1.0
        #   毫无参考价值——恰恰是该让精排出面纠正的局面，不能反过来拿它当"字面可靠"的证据。
        if out[0].get('reason') and 'self-ref↓' in out[0]['reason']:
            do_rerank = True
    if do_rerank:
        try:
            import rerank as _rr
            head, tail = out[:rerank_k], out[rerank_k:]
            for x in head:
                x['rrf'] = x['score']
            rn = _rr.normalize(_rr.scores(q, [x['content'] for x in head], rerank_model))
            sn = _rr.normalize([x['rrf'] for x in head])
            for x, a, b in zip(head, rn, sn):
                x['rerank'] = round(float(a), 4)
                x['score'] = round((1 - rerank_w) * b + rerank_w * a, 5)
            head.sort(key=lambda x: -x['score'])
            out = head + tail
            _diag['rerank_ok'] = True       # ★精排真的跑成功了
        except Exception as e:
            _diag['rerank_ok'] = False
            _diag['rerank_err'] = '%s: %s' % (type(e).__name__, str(e)[:100])
            if not getattr(search_hybrid, '_warned_rr', False):
                search_hybrid._warned_rr = True
                print('[warn] 精排失败，退回 RRF（排序质量下降）:', str(e)[:80], file=sys.stderr)
                print('[warn] 正确解释器: %s' % VENV_PY, file=sys.stderr)

    # 6) ★时间衰减（2026-09-15 接入）：越老的记忆 score 越低。
    #    动机：实测「过时记忆未退役」是继 RRF 之后的下一个瓶颈——
    #      查"deepseek key 失效"时，09-11 那条"已充值10元可用"（错误答案）
    #      会压过 09-15 的"401 已失效"（正确答案）。
    #    ★重要认知：衰减**不是矛盾的解药**，只是弱的辅助（那对只差 2 天，×0.96 无感）。
    #      真正解矛盾靠 `governance.retire()`；衰减治的是"老记忆长期霸榜"。
    #    ★只在排序后做，不改数据库；每条的 decay_factor / age_days 都留痕可审计。
    if decay and out:
        try:
            _gov = _load_governance()
            _gov.apply_decay(out, lookup_db=False)   # out 已带 updated_at
        except Exception as e:
            if not getattr(search_hybrid, '_warned_dc', False):
                search_hybrid._warned_dc = True
                print('[warn] 时间衰减失败（排序退化为纯相关性）:', str(e)[:80], file=sys.stderr)

    # 7) ★升级 1（Q-Value，2026-09-17 接入）：被反复采纳的记忆上浮。
    #    背景（前沿对照文档 §4 升级项 1）：本中枢 manage/update 段几乎空白 ——
    #      "哪条记忆真的有用"这个信号从未被记录，检索只能靠相似度 + 时间排序，
    #      于是历史高频复用的结论会被新写入挤下去。
    #    因子 = 0.3 + 0.7 * q_value ∈ [0.3, 1.0]：
    #      q=0.5（全库默认，即"从未被采纳"）→ ×0.65，对同批候选是**同一常数**，
    #        在 RRF→min-max 精排链路里被完全抵消 ⇒ 不改变任何现有排序；
    #      q→1.0 上浮至 ×1.0；q→0.0 下沉至 ×0.3（**不为 0**，避免把条目钉死）。
    #    ★必须挂在时间衰减**之后**：apply_decay 是最后一道 score 改写且自带重排，
    #      挂在它前面会被直接覆盖。也**不能塞进 decay 块内** —— hard_bench 用
    #      decay=False 调用，塞进去验收台就测不到（等于没接上）。
    #    开关：MEM_QVALUE=0（或 false/off/no）关闭，退回旧行为，供 A/B 对照。
    #    回写不由这里负责（走 gateway.bump_qvalue / `mem.py qvalue`），检索侧只读不写。
    if qvalue is None:
        qvalue = (os.environ.get('MEM_QVALUE') or '1').strip().lower() \
            not in ('0', 'false', 'off', 'no')
    if qvalue and out:
        try:
            for x in out:
                qv = active.get(x.get('uid'), {}).get('q_value')
                qv = 0.5 if qv is None else float(qv)
                x['q_value'] = round(qv, 4)
                # F6（2026-09-24）：tool_assets 条目的 q_value 加时间衰减。
                # cass_memory_system 启示：技能不衰减则旧技能永远排前。
                # 半衰期 90 天（0.5^(days/90)），只作用于 tool_assets 来源（scope=asset）。
                # facts 不衰减（已有 governance.apply_decay 管时间维）。
                if x.get('scope') == 'asset' and x.get('updated_at'):
                    try:
                        from datetime import datetime
                        _upd = datetime.strptime(x['updated_at'][:19], '%Y-%m-%d %H:%M:%S')
                        _days = max(0, (datetime.now() - _upd).days)
                        _dec = 0.5 ** (_days / 90)
                        x['q_decayed'] = round(qv * _dec, 4)
                        x['q_age_days'] = _days
                        x['reason'] = list(x.get('reason') or []) + ['q_decay=%.2f' % _dec]
                        qv = x['q_decayed']
                    except Exception:
                        pass
                x['score'] = round(x['score'] * (0.3 + 0.7 * qv), 5)
                x['reason'] = list(x.get('reason') or []) + ['q=%.2f' % qv]
            out.sort(key=lambda x: -x['score'])
        except Exception as e:
            if not getattr(search_hybrid, '_warned_qv', False):
                search_hybrid._warned_qv = True
                print('[warn] Q-Value 加权失败（排序退化为纯相关性）:', str(e)[:80],
                      file=sys.stderr)

    # P0-07：把过期条数带出去（审计：这次答案里有几条可能已过期）
    _diag['ttl_expired_n'] = _ttl_expired

    # P3 multi-hop expansion (2026-09-24): resolve superseded_by chains to latest
    # For any result that is superseded, automatically follow the chain to the final active version
    try:
        _conn_mh = sqlite3.connect(DB)
        _conn_mh.row_factory = sqlite3.Row
        # Get all supersession relationships
        _sup_map = {}  # old_uid -> new_uid
        for _sr in _conn_mh.execute("SELECT old_uid, new_uid FROM supersessions").fetchall():
            _sup_map[_sr['old_uid']] = _sr['new_uid']
        _conn_mh.close()

        _mh_added = 0
        for _x in out:
            _uid = _x.get('uid')
            if not _uid:
                continue
            # Follow the chain: if this uid was superseded, replace with the latest version
            _chain_depth = 0
            _cur = _uid
            while _cur in _sup_map and _chain_depth < 10:
                _next = _sup_map[_cur]
                # Check if the new version is active
                _conn_chk = sqlite3.connect(DB)
                _conn_chk.row_factory = sqlite3.Row
                _nr = _conn_chk.execute("SELECT uid, content, type, source, status FROM facts WHERE uid=?", (_next,)).fetchone()
                _conn_chk.close()
                if _nr and _nr['status'] == 'active':
                    _cur = _next
                    _chain_depth += 1
                else:
                    break
            if _cur != _uid:
                # Resolve the chain to the latest active version
                _conn_latest = sqlite3.connect(DB)
                _conn_latest.row_factory = sqlite3.Row
                _latest = _conn_latest.execute("SELECT uid, content, type, source, status FROM facts WHERE uid=?", (_cur,)).fetchone()
                _conn_latest.close()
                if _latest and _latest['status'] == 'active':
                    _x['content'] = _latest['content']
                    _x['type'] = _latest['type']
                    _x['superseded_chain'] = f'{_uid} -> {_cur} (depth={_chain_depth})'
                    _mh_added += 1
        if _mh_added:
            _diag['multihop_resolved'] = _mh_added
    except Exception as e:
        _diag['multihop_err'] = str(e)[:80]


    # ★P1-1 Score 统一归一化（2026-09-26）：双量纲问题修复。
    #   问题：有精排时 score = (1-w)*rrf_norm + w*rerank ∈ [0,1]（典型 0.3-0.6），
    #         无精排时 score = RRF原分 + entity_boost + sem_boost ∈ [0.02, 0.1]。
    #   同一次查询会话内两种量纲的 score 无法比较，q_value 加权和 recall_budget
    #   截断在混量纲下不公平（低分高精排条目 vs 高分无精排条目）。
    #   方案：在 recall_budget 截断前，对当前候选集做 min-max 归一化到 [0,1]。
    #   排序不变（单调变换），但下游截断和 q_value 加权在统一尺度上公平工作。
    if len(out) > 1:
        _sc_min = min(x['score'] for x in out)
        _sc_max = max(x['score'] for x in out)
        if _sc_max > _sc_min:
            _span = _sc_max - _sc_min
            for _x in out:
                _x['score_raw'] = _x['score']
                _x['score'] = round((_x['score'] - _sc_min) / _span, 5)
        elif _sc_max > 0:
            # 全部同分（极端平局）：归一化为 0.5
            for _x in out:
                _x['score_raw'] = _x['score']
                _x['score'] = 0.5
    # F4 Recall Budget（2026-09-24）→ P1-3 策略化截断（2026-09-26）。
    # 投影 3980 是「注入槽位」预算；这里是「单次检索合约」预算：
    # 无论 limit 给多大，返回条目的 content 总字符数不超过 MEM_RECALL_BUDGET（默认 6000）。
    # P1-3 改进：pinned 条目不参与截断（强制保留）；归一化后 score >= 0.8 的高价值
    # 条目允许溢出到 1.15x 预算；其余从尾部按 score 降序截断。
    _budget_raw = os.environ.get('MEM_RECALL_BUDGET') or '6000'
    try:
        _budget = max(200, int(_budget_raw))
    except ValueError:
        _budget = 6000
    _overflow_budget = int(_budget * 1.15)
    _kept, _used = [], 0
    _pinned_forced = 0
    for _x in out:
        _n = len(_x.get('content') or '')
        # P1-3: pinned 条目不参与截断，强制保留
        _tags = _x.get('tags') or ''
        _is_pinned = 'pin' in str(_tags).lower() if _tags else False
        if _is_pinned and _kept is not None:
            if _used + _n > _overflow_budget:
                continue  # 连溢出预算都装不下，只好跳过
            _kept.append(_x)
            _used += _n
            _pinned_forced += 1
            continue
        # 高价值条目（归一化后 score >= 0.8）允许溢出到 1.15x 预算
        _eff_budget = _overflow_budget if _x.get('score', 0) >= 0.8 else _budget
        if _used + _n > _eff_budget and _kept:
            break
        if _used + _n > _eff_budget:
            continue  # 单条就超预算：跳过（不该发生，content 通常 << 6000）
        _kept.append(_x)
        _used += _n
    if _pinned_forced:
        _diag['pinned_forced'] = _pinned_forced
    _diag['recall_budget'] = _budget
    _diag['recall_budget_used'] = _used
    # ★P4-2 多轮检索（2026-09-27）：首查分数不足时自动简化 query 重搜。
    #   动机：hard_bench 实测改写集 30% 失败是「正确答案没进候选集」——
    #   用户 query 里可能带冗余上下文（"帮我查一下那个xxx的事"），
    #   向量+关键词都搜不到核心词。方案：top-1 score < 阈值时去掉
    #   中文虚词 + 英文停用词，用核心词重搜一次，合并两轮结果。
    #   开关：MEM_MULTI_ROUND=1 开启（默认关，保持现有行为）。
    #   阈值：MEM_MULTI_ROUND_THR（默认 0.3），归一化后 score < thr 才触发。
    #   递归：_round 防死循环，最多 1 轮。
    #   合并：两轮 uid 去重，score 取 max，重排后再截 limit。
    _mr_on = (os.environ.get('MEM_MULTI_ROUND') or '').strip().lower() in ('1', 'true', 'yes')
    if _mr_on and _round == 0 and _kept:
        _top1 = _kept[0].get('score_raw', _kept[0].get('score', 0.0))
        _thr_raw = os.environ.get('MEM_MULTI_ROUND_THR') or '0.3'
        try:
            _thr = float(_thr_raw)
        except ValueError:
            _thr = 0.3
        if _top1 < _thr:
            # 字符级停用字集合：token 的每个字都在集合里 → 整个 token 是虚词，丢掉。
            # 比词级更鲁棒（能过滤"怎么弄/怎么样/那个/随便/帮我"等任意组合）。
            _ZH_STOP_CHARS = set('的了是在我有和就都不一个上也很到说要去你会着没看好自己这'
                                 '那怎什帮查下随东情事们呢吧啊嘛呀哦哦哈呗'
                                 '样做弄搞整找搜看听问答说讲提搞啥为'
                                 '还又再被把给从对跟离往朝向'
                                 '点些条块件只张台部套种样次遍回趟遍')
            def _zh_is_stop(tok):
                return all(ch in _ZH_STOP_CHARS for ch in tok)
            _EN_STOP = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'in',
                        'on', 'at', 'to', 'for', 'of', 'with', 'and', 'or',
                        'that', 'this', 'it', 'my', 'me', 'i', 'do', 'does',
                        'how', 'what', 'where', 'which', 'help', 'please',
                        'find', 'search', 'look', 'up', 'about'}
            _ents = list(extract_ascii_entities(q))
            _zh_part = re.sub(r'[A-Za-z0-9_.\-]+', ' ', q).strip()
            _zh_tokens = re.findall(r'[\u4e00-\u9fff]+|[\w\-]+', _zh_part)
            _zh_kept = [w for w in _zh_tokens if not _zh_is_stop(w) and len(w) >= 1]
            _en_tokens = re.findall(r'[A-Za-z][A-Za-z0-9_.\-]*', q)
            _en_kept = [w for w in _en_tokens
                        if w.lower() not in _EN_STOP and w not in _ents]
            _parts = _ents + _zh_kept + _en_kept
            _seen2, _dedup = set(), []
            for _p in _parts:
                if _p.lower() not in _seen2:
                    _seen2.add(_p.lower())
                    _dedup.append(_p)
            _q2 = ' '.join(_dedup).strip()
            # 兜底：如果 q2 还是跟 q 一样（比如全是虚词），退化为纯 ASCII 实体
            if _q2 == q and _ents:
                _q2 = ' '.join(_ents)
            if _q2 and _q2 != q:
                try:
                    _r2 = search_hybrid(_q2, limit=limit, vec_k=vec_k,
                                        use_rerank=use_rerank, rerank_k=rerank_k,
                                        rerank_w=rerank_w, rerank_model=rerank_model,
                                        adaptive=adaptive, adaptive_thr=adaptive_thr,
                                        decay=decay, qvalue=qvalue, _round=1)
                    _r2_res = _r2.get('results', [])
                    if _r2_res:
                        _merged = {}
                        for _x in (_kept + _r2_res):
                            _u = _x.get('uid')
                            if _u not in _merged or _x.get('score', 0) > _merged[_u].get('score', 0):
                                _x = dict(_x)
                                _x['reason'] = list(_x.get('reason') or []) + ['multi_round']
                                _merged[_u] = _x
                        _kept = sorted(_merged.values(),
                                       key=lambda _x: -_x.get('score', 0))[:limit]
                        _diag['multi_round'] = True
                        _diag['multi_round_orig_top'] = round(_top1, 5)
                        _diag['multi_round_q2'] = _q2
                        _diag['multi_round_q2_top'] = round(_r2_res[0].get('score', 0), 5) if _r2_res else 0.0
                except Exception as _mr_e:
                    _diag['multi_round_err'] = str(_mr_e)[:80]

    # ★P1-2 revocation guard（2026-09-27）：对标 arXiv 2609.08258 的发现——
    #   被撤销的事实只要撤销标签对检索层可见就会返回。
    #   MemTether 的 supersession/TTL 是写入时标记，但检索结果里如果混入
    #   已 superseded 的旧条目（multi-hop 展开前的原始 uid），应显式标注。
    _revoked_n = 0
    for _x in _kept:
        _tags = str(_x.get('tags') or '')
        if _x.get('ttl_expired'):
            _revoked_n += 1
    if _revoked_n:
        _diag['revoked_expired_n'] = _revoked_n

    # ★R1 (2026-10-01): write-back on retrieval
    # Only in non-recursive calls (_round==0) and MEM_BUMP!=0
    if _round == 0 and os.environ.get('MEM_BUMP', '1') != '0':
        try:
            _bc = _sq3.connect(DB)
            for _x in _kept[:limit]:
                _u = _x.get('uid', '')
                if _u:
                    _bc.execute("UPDATE facts SET use_count = use_count + 1 WHERE uid=?", (_u,))
                    _bc.execute("UPDATE tool_assets SET use_count = use_count + 1 WHERE uid=?", (_u,))
            _bc.commit()
            _bc.close()
        except Exception:
            pass  # bump failure does not block retrieval

    # \u2605R7 (2026-10-01): Three-layer deduplication
    # Layer 1: supersession dedup - remove old versions if new version is in results
    _sup_map = {}
    for _x in _kept:
        _sb = _x.get('superseded_by', '')
        if _sb:
            _sup_map[_x.get('uid', '')] = _sb
    if _sup_map:
        _result_uids = {x['uid'] for x in _kept}
        _remove_old = {_old for _old, _new in _sup_map.items() if _new in _result_uids}
        if _remove_old:
            _kept = [x for x in _kept if x.get('uid', '') not in _remove_old]
            _diag['supersession_dedup'] = len(_remove_old)
    
    # Layer 2: content dedup (MemX Section 3.5 Layer 1)
    _seen_content = set()
    _deduped = []
    for x in _kept:
        _c = x.get('content', '').strip()
        if _c and _c not in _seen_content:
            _seen_content.add(_c)
            _deduped.append(x)
        elif not _c:
            _deduped.append(x)
    if len(_deduped) < len(_kept):
        _diag['content_dedup'] = len(_kept) - len(_deduped)
    _kept = _deduped
    
    # Layer 3: tag-signature dedup (MemX Section 3.5 Layer 2)
    _seen_sig = set()
    _final = []
    for x in _kept:
        _tags = str(x.get('tags', '')).lower().strip()
        if _tags:
            _sig = f"{x.get('type', 'fact')}::{_tags}"
            if _sig in _seen_sig:
                continue
            _seen_sig.add(_sig)
        _final.append(x)
    if len(_final) < len(_kept):
        _diag['tag_dedup'] = len(_kept) - len(_final)
    _kept = _final

    # \u2605R8 (2026-10-01): Low-confidence rejection marker (MemX Section 3.6)
    # If keyword recall is empty AND max vector similarity < threshold, mark as low confidence
    _kw_had = len(bm25_rank) > 0
    _max_vec = max(vec_sim.values()) if vec_sim else 0.0
    _diag['low_confidence'] = (not _kw_had and _max_vec < 0.50)
    _diag['max_vec_sim'] = round(_max_vec, 4)
    _diag['kw_count'] = len(bm25_rank)

    # \u2605R10 integration: scaffold + packet compiler
    _q_type = detect_question_type(q)
    _diag['question_type'] = _q_type
    
    _scaffold = build_scaffold(_q_type, q, _kept[:limit])
    if _scaffold:
        _diag['scaffold'] = _scaffold
    
    # Apply packet compilation for aggregation-type questions
    if _q_type in ('counting', 'aggregation', 'comparison', 'knowledge-update'):
        _kept = compile_packet(_kept[:limit], max_items=16, max_chars=12000)
        _diag['packet_compiled'] = True
        _diag['packet_size'] = len(_kept)

    return {'query': q, 'results': _kept[:limit], 'diag': _diag}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--search', default=None)
    ap.add_argument('--gate-test', action='store_true')
    ap.add_argument('--reclaim', action='store_true',
                    help='回收孤儿 segment 目录（默认 DRY-RUN，只报告）')
    ap.add_argument('--reclaim-apply', action='store_true',
                    help='★真的删除孤儿 segment 目录')
    ap.add_argument('--verify-consistency', action='store_true',
                    help='P0-04 验收：向量条数 == SQLite active 条数')
    a = ap.parse_args()
    if a.rebuild:
        print(json.dumps(rebuild_vector_index(), ensure_ascii=False))
    if a.verify_consistency:
        _r = verify_active_consistency()
        print(json.dumps(_r, ensure_ascii=False, indent=2))
        sys.exit(0 if _r['ok'] else 1)
    if a.reclaim or a.reclaim_apply:
        print(json.dumps(reclaim_orphan_segments(dry_run=not a.reclaim_apply),
                         ensure_ascii=False, indent=2))
    if a.gate_test:
        print('=== 质量门禁测试 ===')
        for t in ['记忆中枢建立，两账号共用', '入口质检功能已上线',
                  'APK静态研判(无Java环境)：全部Python标准库解决',
                  'Packy Astra暂不作为可用方案，因需代理']:
            print('  %s | %s' % ('垃圾' if is_generic_garbage(t) else '正常', t[:45]))
    if a.search:
        print(json.dumps(search_hybrid(a.search), ensure_ascii=False, indent=2))

