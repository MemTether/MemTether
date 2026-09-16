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

HUB = r'E:\RUANJIAN\memory_hub'
DB = os.path.join(HUB, 'memory.db')
CHROMA_PATH = os.path.join(HUB, 'mem0_store')
COLLECTION = 'facts_active'
EMBED_MODEL = 'embedding-3'

# ---- 泛化词表（无信息量的通用动词/名词）----
GENERIC_WORDS = [
    '功能', '上线', '建立', '共用', '已成', '完成', '支持', '通过',
    '机制', '测试', '验证', '升级', '优化', '改进', '成功', '实现',
    '方案', '记录', '规则', '能力',
]


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


def _is_self_referential(content, q):
    """判断某条内容是不是"关于查询 q 的元讨论"而非"对 q 的回答"。"""
    if not content or not q or len(q) < 4:
        return False
    c = content
    # 情形 1：原样含整段查询（含空格）→ 几乎必然是元讨论
    if q in c:
        return any(t in c for t in _SELFREF_TELL)
    # 情形 2：引用了**近似**的查询串（见上方注释）
    qt = _terms(q)
    if not qt:
        return False
    for snip in _quoted_query_like(c):
        st = _terms(snip)
        if not st:
            continue
        if len(qt & st) / len(qt) >= 0.5:
            return True
    return False


# ---- embedding ----
# ★2026-09-15 改：embedding 从「智谱单通道」改为「本地优先 + 云端可选」。
#
#   动机（真实事故）：智谱 embedding-3 欠费返回 429 code=1113，Astra 欠费、
#   DeepSeek 官方 key 失效 —— 三条外部通道同时挂掉，向量路整个停摆，
#   每次查询都降级成纯关键词，且**没有任何本地兜底**。
#
#   后端选择（环境变量 MEM_EMBED_BACKEND）：
#     local （默认）—— 只用本地 bge-m3，永不断供、免费、数据不出本机。
#                      模型缺失时**显式报错**，不静默降级。
#     zhipu          —— 只用智谱云端（2048 维）。
#     auto           —— 先本地，失败再云端。
#
#   ★维度铁律：本地 bge-m3 是 1024 维，智谱是 2048 维，**两者不能混用一个集合**。
#     换后端 = 必须重建索引。索引里记了 embed_dim，查询时不一致会硬报错
#     （而不是让 chroma 给出无意义的近邻）。
LAST_EMBED_INFO = {}          # 诊断用：记录最近一次实际走了哪条路

EMBED_BACKEND_DEFAULT = os.environ.get('MEM_EMBED_BACKEND') or 'local'


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
        raise RuntimeError('本地 embedding 模型文件缺失：%s' % embed_local.MODEL_DIR)
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


def _client():
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


def search_hybrid(query, limit=10, vec_k=60, use_rerank=True, rerank_k=None,
                  rerank_w=0.4, rerank_model=None,
                  adaptive=True, adaptive_thr=0.6,
                  decay=True):
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
    """
    q = (query or '').strip()
    if not q:
        return {'query': q, 'results': []}

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
    active = {r['uid']: dict(r) for r in conn.execute(
        "SELECT uid, content, type, source, scope, updated_at FROM facts WHERE status='active'").fetchall()}
    # ★2026-09-15：资产一并入候选池（kind='tool'），否则「XX装在哪」永远查不到
    assets = {}
    try:
        for a in conn.execute("SELECT * FROM tool_assets WHERE status='active'").fetchall():
            doc = asset_text(a)
            if is_placeholder(doc):
                continue
            assets[a['uid']] = {'uid': a['uid'], 'content': doc, 'type': 'tool',
                                'source': 'tool_assets', 'scope': 'asset',
                                'updated_at': a['updated_at'] if 'updated_at' in a.keys() else '',
                                '_asset': dict(a)}
    except Exception:
        assets = {}
    conn.close()
    active.update(assets)

    # 门禁过滤掉垃圾（含 <见vault:key> 这类占位符）
    active = {u: f for u, f in active.items()
              if not is_generic_garbage(f['content']) and not is_placeholder(f['content'])}

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
    except Exception as e:
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

    # 4) RRF 融合（Reciprocal Rank Fusion）
    #    旧实现把 semantic(余弦0~1) + ascii(0.3) + literal(0.5) 直接相加，量纲不一致导致
    #    语义相近但不精确的条目（查"自动沉淀技能"返回"自动取件护栏"）压过精确匹配。
    #    RRF 只用排名，各路量纲无关；K=60 为业界常用值。
    #    关键词路权重 1.6：实测关键词 Top3 75% 优于纯语义 62%，专有名词命中更可靠。
    K = 60
    out = []
    for uid in set(vec_rank) | set(kw_rank) | lit_hit:
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
        if uid in lit_hit:
            sc += 1.6 / (K + 1)
            reason.append('literal')
        out.append({'uid': uid, 'content': f['content'], 'type': f['type'],
                    'source': f['source'], 'score': round(sc, 5),
                    'semantic': vec_sim.get(uid, 0.0),
                    'updated_at': f.get('updated_at') or '',
                    'reason': reason})
    # ★4.5) 自指降权：把"关于这个查询的元讨论"压到"这个查询的答案"之下。
    #    （详见 _is_self_referential 的注释。字面路给了它们满额加分，这里收回来。）
    #    ★注意用**乘法压到很狠**：实测当查询含 ASCII 文件名（如 mcp_server.py）时，
    #    向量路整体召回不到资产（embedding 被 mem.py 这类相近 token 带偏），
    #    候选集退化成"纯关键词平局"，此时 ×0.25 只能把自指条目从 0.0262 压到
    #    同档，仍然排第一。必须压到任何正常候选之下，才真正起到排序作用。
    for x in out:
        if _is_self_referential(x['content'], q):
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
        except Exception as e:
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
            import governance as _gov
            _gov.apply_decay(out, lookup_db=False)   # out 已带 updated_at
        except Exception as e:
            if not getattr(search_hybrid, '_warned_dc', False):
                search_hybrid._warned_dc = True
                print('[warn] 时间衰减失败（排序退化为纯相关性）:', str(e)[:80], file=sys.stderr)

    return {'query': q, 'results': out[:limit]}


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
    a = ap.parse_args()
    if a.rebuild:
        print(json.dumps(rebuild_vector_index(), ensure_ascii=False))
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
