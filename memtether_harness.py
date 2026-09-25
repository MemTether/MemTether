# -*- coding: utf-8 -*-
"""
memtether_harness.py — L1 上下文工程统一 Harness（2026-09-24）

把 MemTether 的检索 + 安全 + 技能提示 组合成一个调用方可直接用的入口。

  from memtether_harness import recall
  result = recall("ComfyUI 怎么用")

  # result 结构：
  # {
  #   "query": "ComfyUI 怎么用",
  #   "context": "可用于直接注入 prompt 的纯文本段",
  #   "results": [...],       # 原始检索结果（含 skill_hint / _guard_flags）
  #   "diag": {...},          # 诊断信息
  #   "warnings": [...],      # 安全/过期警告摘要
  # }

用法（CLI）：
  python memtether_harness.py "查询内容" [--limit 10] [--no-guard] [--format json|text]

设计原则：
  - 一个函数调用拿到所有维度（检索+安全+过期+技能），调用方不需要串联多个模块
  - context 字段可直接拼进 prompt，不用自己筛格式
  - warnings 让调用方一眼看到"这条答案可能不可靠"的理由

★L1 路径自适应（2026-09-25）：
  发布库（memtether/）与生产库（memory_hub/）同源异码。本仓 memsearch.py 的
  默认 DB 是本目录的 memory.db（发布库占位库，64KB 空库）。若不显式切库，
  harness 会检索**空库**并静默返回空结果（跑起来不报错、但结果错的那一类）。
  解析顺序（fail-safe，不抛异常）：
    $MEM_DB（显式指定，最高优先） → local_paths.json 的 hub_dir/memory.db
    → 本目录 memory.db（开源克隆形态，无 memory_hub 时保持原行为）
  同时把 MEM_STORE 指到真源库同目录的 mem0_store，避免「真源库与向量库拆开」。
"""
import sys, io, os, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAX_CONTEXT_CHARS = int(os.environ.get('MEM_CONTEXT_BUDGET') or '3500')


def _resolve_hub_dir():
    """解析生产记忆中枢目录；找不到返回 None（开源克隆形态）。"""
    # 1) 显式环境变量优先
    env_hub = (os.environ.get('MEM_HUB_DIR') or '').strip()
    if env_hub:
        return env_hub
    # 2) local_paths.json（本机专有，gitignored）
    try:
        with open(os.path.join(HERE, 'local_paths.json'), encoding='utf-8') as f:
            cfg = json.load(f)
        hub = (cfg.get('hub_dir') or '').strip()
        if hub:
            return hub
    except (OSError, ValueError):
        pass
    return None


def _ensure_db_env():
    """若未显式指定 MEM_DB，则自动指向生产库。返回实际使用的 DB 路径（诊断用）。"""
    if os.environ.get('MEM_DB'):
        return os.environ['MEM_DB']
    hub = _resolve_hub_dir()
    if hub:
        cand = os.path.join(hub, 'memory.db')
        if os.path.exists(cand):
            os.environ['MEM_DB'] = cand
            # 向量库与真源库同步切换
            if not os.environ.get('MEM_STORE'):
                store = os.path.join(hub, 'mem0_store')
                if os.path.isdir(store):
                    os.environ['MEM_STORE'] = store
            return cand
    # 无生产库：保持本目录原行为（开源克隆）
    return os.path.join(HERE, 'memory.db')


# 在 import memsearch 之前完成路径注入（memsearch 在模块导入时计算 DB 常量）
_USED_DB = _ensure_db_env()


def recall(query, limit=10, use_guard=True, format='dict', **kwargs):
    """统一检索入口：混合检索 + guard 扫描 + skill hint + 可注入 context 生成。

    kwargs 透传给 memsearch.search_hybrid（如 vec_k, rerank_w 等）。
    """
    import memsearch
    r = memsearch.search_hybrid(query, limit=limit, **kwargs)
    results = r.get('results', [])
    diag = r.get('diag', {})
    diag['db'] = _USED_DB

    # Guard 扫描
    if use_guard:
        try:
            from memtether_guard import scan_results
            results = scan_results(results)
        except ImportError:
            diag['guard'] = 'unavailable'

    # 生成可注入的 context 文本
    warnings = []
    context_parts = []
    used = 0
    for item in results:
        content = (item.get('content') or '').strip()
        if not content:
            continue
        entry = content
        # 添加 skill_hint（如果有的话）
        hint = item.get('skill_hint')
        if hint:
            entry = content + '\n  ↳ ' + hint
        # 检查 guard 标记
        sev = item.get('_guard_max_severity', 0)
        if sev >= 2:
            warnings.append('BLOCK: uid=%s — %s' % (item.get('uid', '?')[:20],
                             (item.get('_guard_flags') or [{}])[0].get('description', '可疑内容')))
            # block 级不放进 context
            continue
        elif sev == 1:
            warnings.append('WARN: uid=%s 有可疑标记，请谨慎对待' % item.get('uid', '?')[:20])
        # 检查 TTL
        ttl = item.get('ttl')
        if ttl:
            entry += ' [⚠ 复核截止: %s]' % ttl
        # 检查预算
        if used + len(entry) > MAX_CONTEXT_CHARS:
            break
        context_parts.append(entry)
        used += len(entry)

    context = '\n\n'.join(context_parts)

    result = {
        'query': query,
        'context': context,
        'results': results,
        'diag': diag,
        'warnings': warnings,
    }

    if format == 'text':
        lines = ['=== MemTether Harness ===',
                 'Q: %s' % query, '',
                 '--- Context (%d chars) ---' % len(context)]
        lines.append(context or '(empty)')
        if warnings:
            lines.append('')
            lines.append('--- Warnings (%d) ---' % len(warnings))
            lines.extend(warnings)
        lines.append('')
        lines.append('--- Diag ---')
        for k, v in diag.items():
            lines.append('  %s: %s' % (k, v))
        return '\n'.join(lines)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='MemTether Unified Harness (L1)')
    parser.add_argument('query', help='检索查询')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--no-guard', action='store_true', help='跳过 guard 扫描')
    parser.add_argument('--format', choices=['json', 'text'], default='text')
    args = parser.parse_args()

    r = recall(args.query, limit=args.limit, use_guard=not args.no_guard, format=args.format)
    if args.format == 'json':
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        print(r)
