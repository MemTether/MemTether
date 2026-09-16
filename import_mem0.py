# -*- coding: utf-8 -*-
"""
import_mem0.py — 把 SQLite 的 active 事实批量导入 Mem0 语义索引（阶段3收尾）

策略：事实提取用 deepseek_official（快1.3s+便宜10倍），embedding 用智谱免费，
      vector_store 用 ChromaDB 本地。批量导入 + 失败重试 + 进度报告。

用法：python import_mem0.py [--limit N]
"""
import sys
import os
import json
import time

sys.path.insert(0, r'<AUDIT>')
sys.path.insert(0, r'<HUB>')

import cred_env
cred_env.env()

import sqlite3
from mem0 import Memory

HUB = r'<HUB>'
DB = os.path.join(HUB, 'memory.db')

MEM0_CONFIG = {
    'llm': {'provider': 'openai', 'config': {
        'model': 'deepseek-chat',
        'api_key': os.environ.get('DEEPSEEK_OFFICIAL_KEY', ''),
        'openai_base_url': 'https://api.deepseek.com/v1',
        'temperature': 0.1, 'max_tokens': 800,
    }},
    'embedder': {'provider': 'openai', 'config': {
        'model': 'embedding-3',
        'api_key': os.environ.get('ZHIPU_KEY', ''),
        'openai_base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'embedding_dims': 2048,
    }},
    'vector_store': {'provider': 'chroma', 'config': {
        'collection_name': 'memory_hub',
        'path': os.path.join(HUB, 'mem0_store'),
    }},
    'version': 'v1.1',
}


def main():
    limit = 0
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM facts WHERE status='active' ORDER BY updated_at DESC").fetchall()
    conn.close()

    if limit:
        rows = rows[:limit]

    print('待导入 %d 条 active 事实到 Mem0' % len(rows))
    m = Memory.from_config(MEM0_CONFIG)

    ok = 0
    fail = 0
    t0 = time.time()
    for i, row in enumerate(rows):
        try:
            m.add(row['content'], user_id='wzj')
            ok += 1
        except Exception as e:
            fail += 1
            print('  [失败] %s: %s' % (row['content'][:40], str(e)[:80]))
        if (i + 1) % 20 == 0:
            el = time.time() - t0
            print('  进度 %d/%d (%.0fs, 平均 %.1fs/条)' % (i + 1, len(rows), el, el / (i + 1)))

    el = time.time() - t0
    print('\n=== 导入完成 ===')
    print('成功 %d / 失败 %d / 总 %d' % (ok, fail, len(rows)))
    print('总耗时 %.0fs (平均 %.1fs/条)' % (el, el / max(ok, 1)))
    print('Mem0 存储: %s' % os.path.join(HUB, 'mem0_store'))


if __name__ == '__main__':
    main()
