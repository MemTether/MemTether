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
"""
import sys, io, os, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_CONTEXT_CHARS = int(os.environ.get('MEM_CONTEXT_BUDGET') or '3500')


def recall(query, limit=10, use_guard=True, format='dict', **kwargs):
    """统一检索入口：混合检索 + guard 扫描 + skill hint + 可注入 context 生成。

    kwargs 透传给 memsearch.search_hybrid（如 vec_k, rerank_w 等）。
    """
    import memsearch
    r = memsearch.search_hybrid(query, limit=limit, **kwargs)
    results = r.get('results', [])
    diag = r.get('diag', {})

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