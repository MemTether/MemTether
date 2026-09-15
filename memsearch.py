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
    """查询/文档分词：中文 2~4gram + ASCII 实体。用于关键词路打分。"""
    t = text or ''
    out = set()
    for w in re.findall(r'[\u4e00-\u9fa5]{2,4}', t):
        if w not in _STOP:
            out.add(w)
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', t):
        out.add(w.lower())
    return out


# ---- embedding ----
def _embed(texts):
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


def _client():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH)


def extract_ascii_entities(text):
    """提取 ASCII 实体（连续>=3字母数字，含 _ - . 路径片段）"""
    return set(re.findall(r'[A-Za-z][A-Za-z0-9_.\-]{2,}', text or ''))


def rebuild_vector_index(verbose=True):
    """从 SQLite active facts 重建干净的向量索引（排除垃圾/测试源）。幂等。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid, content, type, source, scope FROM facts WHERE status='active'").fetchall()
    conn.close()

    kept, quarantined = [], []
    for r in rows:
        if is_generic_garbage(r['content']) or (r['source'] or '') in ('test', 'test_hub', 'fixture'):
            quarantined.append(r)
        else:
            kept.append(r)

    client = _client()
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={'hnsw:space': 'cosine'})

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
                            'source': r['source'] or '', 'kind': 'fact'} for r in kept])
    if verbose:
        print('向量索引重建完成：active %d 条，入索引 %d 条，隔离垃圾 %d 条'
              % (len(rows), len(kept), len(quarantined)))
        for q in quarantined:
            print('  隔离: %s' % q['content'][:50])
    return {'active': len(rows), 'indexed': len(kept), 'quarantined': len(quarantined)}


def search_hybrid(query, limit=10, vec_k=30):
    """混合检索：质量门禁 + 向量 + ASCII精确 + 融合排序。"""
    q = (query or '').strip()
    if not q:
        return {'query': q, 'results': []}

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    active = {r['uid']: dict(r) for r in conn.execute(
        "SELECT uid, content, type, source, scope, updated_at FROM facts WHERE status='active'").fetchall()}
    conn.close()

    # 门禁过滤掉垃圾（含 <见vault:key> 这类占位符）
    active = {u: f for u, f in active.items()
              if not is_generic_garbage(f['content']) and not is_placeholder(f['content'])}

    # ---- 各路召回，只记录**排名**，不记录原始分数 ----
    # 1) 向量路
    vec_rank, vec_sim = {}, {}
    try:
        col = _client().get_collection(COLLECTION)
        qv = _embed([q])[0]
        r = col.query(query_embeddings=[qv], n_results=vec_k)
        for i, (uid, dist) in enumerate(zip(r['ids'][0], r['distances'][0])):
            if uid in active:
                vec_rank[uid] = i
                vec_sim[uid] = round(1 - dist, 4)
    except Exception as e:
        print('[warn] 向量检索失败:', str(e)[:80], file=sys.stderr)

    # 2) 关键词路：查询词覆盖率 x IDF（专有名词命中权重更高）
    q_terms = _terms(q)
    kw_raw = {}
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
                    'reason': reason})
    out.sort(key=lambda x: x['score'], reverse=True)
    return {'query': q, 'results': out[:limit]}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--search', default=None)
    ap.add_argument('--gate-test', action='store_true')
    a = ap.parse_args()
    if a.rebuild:
        print(json.dumps(rebuild_vector_index(), ensure_ascii=False))
    if a.gate_test:
        print('=== 质量门禁测试 ===')
        for t in ['记忆中枢建立，两账号共用', '入口质检功能已上线',
                  'APK静态研判(无Java环境)：全部Python标准库解决',
                  'Packy Astra暂不作为可用方案，因需代理']:
            print('  %s | %s' % ('垃圾' if is_generic_garbage(t) else '正常', t[:45]))
    if a.search:
        print(json.dumps(search_hybrid(a.search), ensure_ascii=False, indent=2))
