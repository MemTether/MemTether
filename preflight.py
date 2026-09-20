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

HUB = os.path.dirname(os.path.abspath(__file__))

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
    """检索相关记忆。主路走 gateway 混合检索（向量+ASCII+字面）；失败退回关键词匹配。

    返回 [(score, type, content, source, origin)]。
    ★ 2026-09-19 修正：score 改用 gateway 返回的**真实融合分**（原实现写 `100 - i`，
      是纯序号伪分数，把真实分丢了 → 无法区分强弱，语义路第 34 条与第 3 条看起来一样）。
    ★ 量纲（实测确认）：主路 score = 混合检索最终分，**越大越相关**，按序递减；
      兜底路 score = 关键词计分（越大越相关），origin 带 `kw:` 前缀以便区分。
    ★ 另注：reason 里的 `q=0.50` 不是区分度——全库 q_value 恒 0.5，该因子是常数乘子，
      在 RRF→精排链路里被完全抵消（qvalue_ab.py 已写明属预期）。别拿它当强弱依据。
    """
    # 主路：gateway 混合检索（2026-09-13 升级，8/8 回归通过）
    try:
        sys.path.insert(0, HUB)
        import gateway
        r = gateway.search(msg, limit=limit)
        hits = []
        for i, item in enumerate(r.get('results', [])):
            try:
                sc = float(item.get('score'))
            except (TypeError, ValueError):
                sc = -1.0
            reason = ','.join(item.get('reason', []))
            hits.append((sc, item.get('type', 'fact'),
                         item.get('content', '')[:400],
                         item.get('source', ''),
                         'gw#%d%s' % (i + 1, (':%s' % reason) if reason else '')))
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
            hits.append((score * 2, t, text[:400], e.get('ts', ''),
                         'kw:sink %s' % e.get('source', '')))
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
                hits.append((score * w, fname, b[:400], '', 'kw:md'))
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


def _ro_conn():
    """只读打开真源库。★只读命令不得创建文件 → 用 URI mode=ro（不会建 -wal/-journal）。"""
    import sqlite3
    db = os.environ.get('MEM_DB') or os.path.join(HUB, 'memory.db')
    if not os.path.exists(db):
        return None, db
    uri = 'file:%s?mode=ro' % db.replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True, timeout=5), db


# 核心状态各类型取样条数（真源直读，不再依赖 md 投影）
CORE_PER_TYPE = (('decision', 6), ('incident', 5), ('experience', 4), ('fact', 8))


def _core_from_db():
    """从真源 memory.db 直读核心状态。

    ★ 2026-09-19 修正：原 core_summary() 读的是 md 投影（facts.md/decisions.md/…），
      而 agents.json v4 已声明 true_source=memory.db → 走错真源，且投影会过时
      （实测 facts.md 里 Agent 标识仍写着旧值、active_tasks.md 停在旧任务）。
    """
    con, db = _ro_conn()
    if con is None:
        return ''
    try:
        con.row_factory = None
        L = []
        try:
            total = con.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
        except Exception as e:
            return '【真源 memory.db】查询失败：%s: %s' % (type(e).__name__, e)
        L.append('【真源 memory.db】active %d 条（%s）' % (total, os.path.basename(db)))
        for t, n in CORE_PER_TYPE:
            try:
                rows = con.execute(
                    "SELECT content, source, created_at FROM facts "
                    "WHERE status='active' AND type=? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?", (t, n)).fetchall()
            except Exception:
                rows = []
            if not rows:
                continue
            L.append('· %s（最新 %d 条，倒序）' % (t, len(rows)))
            for c, s, ts in rows:
                c = re.sub(r'\s+', ' ', (c or '')).strip()
                L.append('  - [%s|%s] %s' % ((ts or '')[:10], s or '?', c[:160]))
        # 投影新鲜度（提醒投影是否落后于真源）
        proj = os.path.join(os.path.expanduser('~'), '.workbuddy', 'MEMORY.md')
        if os.path.exists(proj):
            import time
            L.append('· 投影 %s  mtime=%s  size=%d B' % (
                proj,
                time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(proj))),
                os.path.getsize(proj)))
        return '\n'.join(L)
    except Exception as e:
        return '【真源 memory.db】读取失败：%s: %s' % (type(e).__name__, e)
    finally:
        try:
            con.close()
        except Exception:
            pass


def _core_from_md():
    """[兜底] 旧版：读 md 投影。真源不可用时才用，并在输出里标明是投影。"""
    lines = []
    at = read(os.path.join(HUB, 'active_tasks.md'))
    if at:
        lines.append('【当前任务 active_tasks.md（投影，可能过时）】\n' + at[:1200])
    for f in ('facts.md', 'decisions.md', 'incidents.md'):
        c = read(os.path.join(HUB, f))
        if c:
            lines.append('【%s 摘要（投影，可能过时）】\n%s' % (f, c[:900]))
    return '\n'.join(lines)


def core_summary(budget=3600):
    """必读核心的精简摘要。优先真源 memory.db；不可用时退回 md 投影。"""
    src = _core_from_db()
    if not src or src.startswith('【真源 memory.db】读取失败') or src.startswith('【真源 memory.db】查询失败'):
        md = _core_from_md()
        return (src + '\n' + md) if src else md
    if len(src) > budget:
        src = src[:budget] + '\n...[核心状态截断]'
    return src


def gate_block(msg, budget=2000):
    """禁区 / 血证区块 —— 硬规则第 15 条「不重复造轮子」的机械判据。

    ★ fail-closed：比对失败时必须输出显式说明，**绝不静默消失**。
      （静默消失 = 闸门形同不存在，正是「否决结论被摊平成普通 fact」的同一类病。）
    """
    header = '== 禁区 / 血证（先看这个 · 硬规则第15条判据） =='
    try:
        sys.path.insert(0, HUB)
        import gate
        blk = gate.format_block(gate.check(msg))
    except Exception as e:
        return (header + '\n'
                '闸门未能比对（%s: %s）—— 这不是「无禁区」，请手工核对。' % (type(e).__name__, e))
    if not blk:
        return header + '\n闸门返回空内容 —— 这不是「无禁区」，请手工核对。'
    if len(blk) > budget:
        blk = blk[:budget] + '\n...[禁区区块截断]'
    return blk


def _fmt_score(score):
    """打印真实分数（越大越相关）。两路量纲不同，看 origin 前缀区分：`gw#` = 混合检索分，`kw:` = 关键词分。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return '?'
    if s < 0:
        return '?'
    return ('%.4f' % s).rstrip('0').rstrip('.') or '0'


def build_preflight(msg, raw=False):
    parts = []
    if not raw:
        parts.append('[MEMORY_PREFLIGHT]')
        parts.append('以下是本轮相关记忆（扫描于 memory_hub）。仅作辅助；若与用户最新明确信息冲突，以最新为准。')
    # -1) 禁区 / 血证（★置顶：动手前先看这个，硬规则第15条判据）
    parts.append(gate_block(msg))
    # 0) 任务配方（按任务召回工具+路径+坑，gateway.resolve_task）
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
        else:
            # ★ 2026-09-19：recipes 表实测 0 条 → 此区块原先恒不出现（静默），改为显式说明
            n = None
            try:
                con, _db = _ro_conn()
                if con is not None:
                    try:
                        n = con.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
                    finally:
                        con.close()
            except Exception:
                pass
            parts.append('== 任务工具配方（gateway） == 无配方'
                         + ('（recipes 表 %d 条，resolve_task 无数据可返）' % n if n is not None else '')
                         + '；工具资产见 tool_assets 表，用 `python mem.py` / gateway 查。')
    except Exception as e:
        parts.append('== 任务工具配方（gateway） == 查询失败（%s: %s）' % (type(e).__name__, e))
    # 相关检索结果（优先）
    hits = search_entries(msg)
    if hits:
        parts.append('== 相关记忆（按相关度） ==')
        for score, src, text, ts, origin in hits:
            tag = ('%s %s' % (src, ts)) if ts else src
            if origin:
                tag += ' [%s]' % origin
            parts.append('- (%s) [%s] %s' % (_fmt_score(score), tag, text))
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
    # 截断到 12KB。★ 2026-09-19：改为「保头保尾」——核心状态在尾部，
    # 原实现硬切尾部会把最该看的东西（真源摘要/投影新鲜度）整段丢掉。
    if len(out) > 12000:
        out = out[:8800] + '\n\n...[中间截断，保留头尾]...\n\n' + out[-3000:]
    print(out)


if __name__ == '__main__':
    main()
