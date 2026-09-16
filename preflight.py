# -*- coding: utf-8 -*-
"""
preflight.py — 每轮回答前扫描记忆中枢（astra 方案落地，2026-09-13）

用法：
  python preflight.py "用户这轮说的话"

输出：一段 [MEMORY_PREFLIGHT] ... [/MEMORY_PREFLIGHT] 区块，
     包含与本轮问题相关的记忆片段，供 DeepSeek 在回答前注入。

扫描策略（三层，astra 定）：
  1. 必读核心（profile.md 精简 + active_tasks.md + facts/decisions/incidents 摘要）
  2. 从用户消息提取关键词，在 facts/decisions/incidents/experience/sink.json 检索
  3. 输出去重 Top 片段，带来源，总长 ≤ 12KB

若 --raw 则只输出检索片段，不套 MEMORY_PREFLIGHT 包裹（便于程序拼接）。
"""
import os
import re
import sys
import json

HUB = r'<HUB>'

# 权重：facts/decisions/incidents 高于 experience
WEIGHT = {
    'facts.md': 5, 'decisions.md': 5, 'incidents.md': 5,
    'active_tasks.md': 4, 'profile.md': 4,
    'experience.md': 2, 'DIGEST.md': 3,
}


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


def load_sink_entries():
    """从 sink.json 读条目（真源），返回 [(type, entry)]"""
    try:
        d = json.load(open(os.path.join(HUB, 'sink.json'), encoding='utf-8'))
    except Exception:
        return []
    out = []
    if isinstance(d, dict):
        for t in ('fact', 'decision', 'incident', 'experience', 'todo'):
            for e in d.get(t, []) if isinstance(d.get(t), list) else []:
                out.append((t, e))
    return out


def extract_keywords(msg):
    """从用户消息提取关键词：中文连续片段 + 英文/数字 token"""
    kws = set()
    # 英文/数字/下划线 token（>=2 字符）
    for m in re.findall(r'[A-Za-z0-9_\-]{2,}', msg):
        kws.add(m.lower())
    # 中文 2-6 字连续片段（粗略：按常见标点/空格切分后再滑窗）
    zh = re.sub(r'[^\u4e00-\u9fff]+', ' ', msg)
    for seg in zh.split():
        if 2 <= len(seg) <= 12:
            kws.add(seg)
            for i in range(len(seg) - 1):  # 二元滑窗
                kws.add(seg[i:i + 2])
    return kws


def search_entries(msg, limit=10):
    """检索相关记忆。主路走 gateway 混合检索（向量+ASCII+字面）；失败退回关键词匹配。"""
    # 主路：gateway 混合检索（2026-09-13 升级，8/8 回归通过）
    try:
        sys.path.insert(0, HUB)
        import gateway
        r = gateway.search(msg, limit=limit)
        hits = []
        for i, item in enumerate(r.get('results', [])):
            hits.append((100 - i, item.get('type', 'fact'),
                         item.get('content', '')[:400],
                         item.get('source', ''),
                         'gw:%s' % ','.join(item.get('reason', []))))
            if len(hits) >= limit:
                break
        if hits:
            return hits
    except Exception:
        pass
    return _search_entries_keyword(msg, limit)


def _search_entries_keyword(msg, limit=10):
    """[兜底] 旧版：关键词匹配 sink.json 条目 + 各 md 文件。"""
    kws = extract_keywords(msg)
    hits = []
    # 1) sink.json 条目
    for t, e in load_sink_entries():
        text = str(e.get('text', '')) + ' ' + str(e.get('tag', ''))
        score = sum(1 for k in kws if k in text.lower())
        if score > 0:
            hits.append((score * 2, t, text[:400], e.get('ts', ''), e.get('source', '')))
    # 2) md 文件按段落
    for fname, w in WEIGHT.items():
        content = read(os.path.join(HUB, fname))
        # 按空行分块
        for block in content.split('\n\n'):
            b = block.strip()
            if len(b) < 10:
                continue
            score = sum(1 for k in kws if k in b.lower())
            if score > 0:
                hits.append((score * w, fname, b[:400], '', ''))
    hits.sort(key=lambda x: -x[0])
    # 去重（按内容前 80 字）
    seen = set()
    uniq = []
    for h in hits:
        key = h[2][:80]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq[:limit]


def search_docs(msg, limit=5):
    """检索文档索引（doc_index.jsonl），返回相关文档结论候选"""
    idx = os.path.join(HUB, 'docs', 'doc_index.jsonl')
    if not os.path.exists(idx):
        return []
    kws = extract_keywords(msg)
    hits = []
    try:
        for line in open(idx, encoding='utf-8'):
            try:
                e = json.loads(line)
            except Exception:
                continue
            text = str(e.get('text', ''))
            score = sum(1 for k in kws if k in text.lower())
            if score > 0:
                hits.append((score, e.get('source_doc', ''), text[:400]))
    except Exception:
        pass
    hits.sort(key=lambda x: -x[0])
    seen = set()
    uniq = []
    for h in hits:
        if h[2][:60] in seen:
            continue
        seen.add(h[2][:60])
        uniq.append(h)
    return uniq[:limit]


def core_summary():
    """必读核心的精简摘要"""
    lines = []
    # 活跃任务（最重要，直接全量）
    at = read(os.path.join(HUB, 'active_tasks.md'))
    if at:
        lines.append('【当前任务 active_tasks.md】\n' + at[:1500])
    # facts/decisions/incidents 各取前 1200 字
    for f in ('facts.md', 'decisions.md', 'incidents.md'):
        c = read(os.path.join(HUB, f))
        if c:
            lines.append('【%s 摘要】\n%s' % (f, c[:1200]))
    return '\n'.join(lines)


def build_preflight(msg, raw=False):
    parts = []
    if not raw:
        parts.append('[MEMORY_PREFLIGHT]')
        parts.append('以下是本轮相关记忆（扫描于 memory_hub）。仅作辅助；若与用户最新明确信息冲突，以最新为准。')
    # 0) 任务配方（优先：按任务召回工具+路径+坑，gateway.resolve_task）
    try:
        import gateway
        task = gateway.resolve_task(msg[:50])
        tools = task.get('preferred_tools', [])
        if tools:
            parts.append('== 任务工具配方（gateway） ==')
            for t in tools:
                parts.append('- 工具 %s：%s → %s' % (t.get('name'), t.get('path', ''), t.get('entrypoint', '')))
                kf = t.get('known_failures', '[]')
                if kf and kf != '[]':
                    parts.append('  已知坑: %s' % kf[:200])
    except Exception:
        pass
    # 相关检索结果（优先）
    hits = search_entries(msg)
    if hits:
        parts.append('== 相关记忆（按相关度） ==')
        for score, src, text, ts, origin in hits:
            tag = ('%s %s' % (src, ts)) if ts else src
            if origin:
                tag += ' [%s]' % origin
            parts.append('- [%s] %s' % (tag, text))
    else:
        parts.append('== 相关记忆 == 未检索到直接相关条目')
    # 文档索引（深度互联：共享文档提炼的结论）
    dhits = search_docs(msg)
    if dhits:
        parts.append('== 共享文档相关结论（doc_index） ==')
        for score, doc, text in dhits:
            parts.append('- [doc:%s] %s' % (doc.split('/')[-1] if doc else '?', text))
    # 核心摘要
    parts.append('== 核心状态 ==')
    parts.append(core_summary())
    if not raw:
        parts.append('[/MEMORY_PREFLIGHT]')
    return '\n'.join(parts)


def main():
    msg = ' '.join(sys.argv[1:]) or ''
    raw = '--raw' in sys.argv
    msg = msg.replace('--raw', '').strip()
    if not msg:
        # 无消息时只输出核心状态
        msg = ''
    out = build_preflight(msg, raw=raw)
    # 截断到 12KB
    if len(out) > 12000:
        out = out[:12000] + '\n...[截断]'
    print(out)


if __name__ == '__main__':
    main()
