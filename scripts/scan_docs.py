# -*- coding: utf-8 -*-
"""
scan_docs.py — 共享文档与记忆中枢深度互联（astra 方案，2026-09-13）

扫描共享文档目录，建立：
  1. docs/catalog.json —— 文档清单（路径/类型/大小/mtime/SHA256/可信级别）
  2. docs/doc_index.jsonl —— 每个文档提炼出的"结论候选"（供 preflight 检索 + 后续入库）

分级：
  - 高可信（交接档案/核验报告/指令）→ 全文提炼结论
  - 中可信（会话记录/报告）→ 提取标题+摘要+关键段
  - 低可信（大文件如 chat.txt 6.7MB）→ 只记录元信息，不全文处理

用法：
  python scripts/scan_docs.py [--docdir 路径] [--force]
"""
import os
import sys
import json
import hashlib
import time
import re

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本机专有路径外置（开源版无 local_paths.json → 相关检查项自动跳过，不报假失败）
_LOCAL = {}
try:
    with open(os.path.join(HUB, 'local_paths.json'), encoding='utf-8') as _f:
        _LOCAL = json.load(_f)
except (OSError, ValueError):
    pass

DOCS_DIR = os.path.join(HUB, 'docs')
CATALOG = os.path.join(DOCS_DIR, 'catalog.json')
DOC_INDEX = os.path.join(DOCS_DIR, 'doc_index.jsonl')

# 默认扫描的共享文档目录
DEFAULT_DOCDIRS = list(_LOCAL.get('doc_dirs') or [])

# 高可信关键词（这些文件重点提炼）
HIGH_TRUST = ['交接', '核验', '指令', '报告', '结论', '方案', '档案', 'README']
# 跳过的大文件/无意义文件
SKIP_EXT = {'.pyc', '.exe', '.dll', '.bin', '.png', '.jpg', '.apk', '.zip'}
SKIP_NAME = {'chat.txt', 'agent_cot.txt', 'bg.txt', 'launcher_tech.txt', 'launcher_zh.txt', 'main_tech.txt', 'main_zh.txt'}
MAX_FILE = 2 * 1024 * 1024  # 2MB 以上只记元信息


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def file_trust(name):
    for kw in HIGH_TRUST:
        if kw in name:
            return 'high'
    return 'medium'


def extract_conclusions(path, trust):
    """从文档提炼结论候选（规则式，不调模型）。返回 [(类型, 文本, 跨度)]"""
    if trust != 'high':
        return []  # 中低可信不提炼，只记元信息
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception:
        return []
    out = []
    # 找含"结论/根因/决策/方案/修复"的段落
    for m in re.finditer(r'(?m)^(#{1,6}\s*)?[^\n]*(结论|根因|决策|方案|修复|已确认|不可修|已跑路|血泪|教训)[^\n]*', content):
        line = m.group(0).strip()
        if 10 < len(line) < 400:
            out.append(('fact', line))
    # 找待办/下一步
    for m in re.finditer(r'(?m)^[^\n]*(待办|待用户|下一步|遗留|阻塞|待确认)[^\n]*', content):
        line = m.group(0).strip()
        if 10 < len(line) < 300:
            out.append(('todo', line))
    return out[:20]


def main():
    docdirs = DEFAULT_DOCDIRS
    force = '--force' in sys.argv
    if '--docdir' in sys.argv:
        i = sys.argv.index('--docdir')
        docdirs = [sys.argv[i + 1]]

    os.makedirs(DOCS_DIR, exist_ok=True)

    catalog = {}
    if os.path.exists(CATALOG) and not force:
        catalog = json.load(open(CATALOG, encoding='utf-8'))

    index_lines = []
    scanned = 0
    new_concl = 0

    for d in docdirs:
        if not os.path.isdir(d):
            print('跳过（不存在）: %s' % d)
            continue
        for root, dirs, files in os.walk(d):
            # 跳过隐藏/无关目录
            dirs[:] = [x for x in dirs if not x.startswith('.') and x not in ('__pycache__', 'node_modules', '.git')]
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext in SKIP_EXT or fn in SKIP_NAME:
                    continue
                path = os.path.join(root, fn)
                try:
                    size = os.path.getsize(path)
                except Exception:
                    continue
                mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(path)))
                trust = file_trust(fn)
                if size > MAX_FILE:
                    trust = 'large'  # 大文件只记元信息
                h = sha256(path)
                key = path.replace('\\', '/')
                catalog[key] = {
                    'name': fn, 'size': size, 'mtime': mtime,
                    'sha256': h[:16], 'trust': trust,
                }
                scanned += 1
                # 高可信 → 提炼结论
                if trust == 'high':
                    for t, text in extract_conclusions(path, trust):
                        rec = {
                            'type': t, 'text': text,
                            'source_doc': key, 'trust': trust,
                            'ts': mtime, 'status': 'candidate',
                        }
                        index_lines.append(json.dumps(rec, ensure_ascii=False))
                        new_concl += 1

    json.dump(catalog, open(CATALOG, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    with open(DOC_INDEX, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines) + ('\n' if index_lines else ''))

    print('扫描完成：%d 个文档入 catalog，%d 条结论候选入 doc_index' % (scanned, new_concl))
    print('catalog: %s' % CATALOG)
    print('index:   %s' % DOC_INDEX)


if __name__ == '__main__':
    main()
