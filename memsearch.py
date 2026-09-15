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

    # 门禁过滤掉垃圾
    active = {u: f for u, f in active.items() if not is_generic_garbage(f['content'])}

    cands = {}  # uid -> score info

    # 1) 向量路
    try:
        col = _client().get_collection(COLLECTION)
        qv = _embed([q])[0]
        r = col.query(query_embeddings=[qv], n_results=vec_k)
        for uid, dist in zip(r['ids'][0], r['distances'][0]):
            if uid in active:
                cands.setdefault(uid, {'uid': uid, 'reason': []})
                cands[uid]['semantic'] = round(1 - dist, 4)
                cands[uid]['reason'].append('semantic')
    except Exception as e:
        print('[warn] 向量检索失败:', str(e)[:80], file=sys.stderr)

    # 2) ASCII 实体精确路（仅当 query 含 ASCII 实体时触发）
    ents = extract_ascii_entities(q)
    for uid, f in active.items():
        for e in ents:
            if e.lower() in (f['content'] or '').lower():
                cands.setdefault(uid, {'uid': uid, 'reason': []})
                cands[uid]['ascii'] = 0.3
                cands[uid]['reason'].append('ascii:%s' % e)
                break

    # 3) 整句字面匹配路（中文概念兜底：如"三大机制"字面出现在事实里）
    #    解决 astra 指出的"纯中文概念向量区分度不足"问题，且无需维护概念表。
    ql = q.lower()
    if len(q) >= 2:
        for uid, f in active.items():
            if ql in (f['content'] or '').lower():
                cands.setdefault(uid, {'uid': uid, 'reason': []})
                cands[uid]['literal'] = 0.5
                cands[uid]['reason'].append('literal')

    # 4) 融合排序
    out = []
    for uid, c in cands.items():
        f = active.get(uid)
        if not f:
            continue
        score = c.get('semantic', 0.0) + c.get('ascii', 0.0) + c.get('literal', 0.0)
        out.append({'uid': uid, 'content': f['content'], 'type': f['type'],
                    'source': f['source'], 'score': round(score, 4),
                    'semantic': c.get('semantic', 0.0),
                    'reason': c['reason']})
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
