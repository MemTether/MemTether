# -*- coding: utf-8 -*-
"""mem.py — 多智能体共享记忆总线（memory_hub v3）

一个中枢，三个 agent（WorkBuddy 侧 DeepSeek / OpenClaw 🦞 / 豆包），各自读写同一份记忆。

核心保证（对应 v2 的四个硬伤）：
  1. 唯一真源 —— sink.json 是机器可读的唯一权威。experience.md 是人读镜像（只追加，不重写手写段落）；
     DIGEST.md 与 projections/*.md 全是渲染产物，可随时重生成。
     （v2 是 sink.json 与 experience.md 双写，两边会漂移。）
  2. 并发安全 —— 跨进程文件锁（msvcrt）+ 原子替换写 + 写入前备份轮转。
     多 agent 同一秒写也不会丢条目。（v2 是裸的读-改-写。）
  3. 全程归属 —— 每条记忆带 id / source / ts / seq，谁记的、什么时候记的写死在条目里。
     （v2 无 source，三个 agent 写进去分不清出处。）
  4. 去重合并 —— 同一（类型 + 规范化文本）不重复入库，改为累积 sources 与 count。
  5. 增量水位 —— 每个 agent 记录自己读到哪条（last_seq），drain 只吐它没读过的。

用法:
  python mem.py add --type experience --text "..." --source openclaw [--tag x] [--ref 路径]
  python mem.py list  [--type T] [--tail N] [--source S]
  python mem.py search "关键词" [--limit N]
  python mem.py since "2026-09-12 12:00" [--source S]
  python mem.py recall --agent openclaw [--budget 4000]   # 生成会话注入块（不推进水位）
  python mem.py drain --agent openclaw [--limit 20]       # 取未读并推进水位（--peek 只看不推）
  python mem.py agents                                    # 列出注册的 agent
  python mem.py verify [--fix]                            # 完整性校验（可修）
  python mem.py render                                    # 重生成 DIGEST.md 与全部投影
  python mem.py migrate                                   # 存量补 id/source/seq
  python mem.py stats

依赖：仅标准库。敏感串（sk-…/长 key）写入前一律脱敏为 <见vault:key>。
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HUB = Path(os.path.dirname(os.path.abspath(__file__)))
DB = HUB / 'sink.json'
MD = HUB / 'experience.md'
LOCK = HUB / '.sink.lock'
WM = HUB / 'watermarks.json'
AGENTS_F = HUB / 'agents.json'
BACKUP = HUB / '.backup'
# ★2026-09-16 修正：原值只有 ('experience','fact','todo')，但 facts 表里实际还有
#   decision(20 条)/incident(6 条)，导致 `mem.py asof --type decision` 会被 argparse
#   直接拒绝（报 invalid choice），而这类"决策/事故"恰恰是时序查询最常问的对象。
TYPES = ('experience', 'fact', 'todo', 'decision', 'incident', 'preference', 'environment')
KEEP_BACKUPS = 12

# ---------- gateway 桥接（2026-09-13）----------
# 消除双真源：历史上 mem.py 写 sink.json、gateway 写 memory.db，两套并存。
# 现在 memory.db 是唯一真源，mem.py 的写入/检索一律委托 gateway。
def _gw():
    sys.path.insert(0, str(HUB))
    import gateway
    return gateway


def _gw_facts(status='active'):
    """从 memory.db（唯一真源）读事实。"""
    import sqlite3
    conn = sqlite3.connect(str(HUB / 'memory.db'))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid,type,subject,content,status,source,scope,confidence,tags,created_at,updated_at "
        "FROM facts WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ---------- 脱敏 ----------
_SK_RE = re.compile(r'sk-[A-Za-z0-9_-]{20,}')
_KEY_RE = re.compile(r'[A-Za-z0-9]{32,}\.[A-Za-z0-9_-]{20,}')
_MASK = '<见vault:key>'


def sanitize(s: str) -> str:
    s = _SK_RE.sub(_MASK, s)
    s = _KEY_RE.sub(_MASK, s)
    return s


# ---------- 基础 IO ----------
def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M')


def load() -> dict:
    if DB.exists():
        try:
            d = json.loads(DB.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}


def _atomic_write(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(str(tmp), str(p))


def _rotate_backup() -> None:
    """写入前留档，最多 KEEP_BACKUPS 份。"""
    if not DB.exists():
        return
    try:
        BACKUP.mkdir(exist_ok=True)
        dst = BACKUP / ('sink.json.' + time.strftime('%Y%m%d_%H%M%S'))
        dst.write_bytes(DB.read_bytes())
        olds = sorted(BACKUP.glob('sink.json.*'))
        for f in olds[:-KEEP_BACKUPS]:
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def save(d: dict) -> None:
    _rotate_backup()
    _atomic_write(DB, json.dumps(d, ensure_ascii=False, indent=2))


# ---------- 跨进程文件锁 ----------
class _Lock:
    """Windows: msvcrt 独占锁；其它平台退化为 flock/O_EXCL 轮询。"""

    def __init__(self, timeout=20.0):
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK, 'a+b')
        deadline = time.time() + self.timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError('获取记忆中枢锁超时（另一进程长时间占用）')
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if os.name == 'nt':
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        finally:
            if self.fh:
                self.fh.close()
        return False


# ---------- 条目工具 ----------
def _rev(text: str) -> str:
    norm = re.sub(r'\s+', ' ', text).strip().lower()
    return hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]


def _max_seq(d: dict) -> int:
    m = 0
    for t in TYPES:
        for e in d.get(t, []) or []:
            try:
                m = max(m, int(e.get('seq') or 0))
            except Exception:
                pass
    return m


def all_entries(d: dict = None):
    d = d if d is not None else load()
    out = []
    for t in TYPES:
        for e in d.get(t, []) or []:
            if isinstance(e, dict):
                out.append((t, e))
    return out


# ---------- agents 注册表 ----------
def load_agents() -> dict:
    if AGENTS_F.exists():
        try:
            return json.loads(AGENTS_F.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'version': 3, 'agents': {}}


def known_sources() -> list:
    return list((load_agents().get('agents') or {}).keys())


def _validate_source(source: str) -> str:
    """来源必须已注册，防止各 agent 自造名把归属搞乱。"""
    src = (source or '').strip()
    reg = known_sources()
    if not src:
        src = 'unknown'
    if reg and src not in reg:
        raise SystemExit('ERROR: 未注册的来源 %r。已注册: %s\n（如需新增，编辑 %s）'
                         % (src, ', '.join(reg), AGENTS_F))
    return src


# ---------- 入口质检（2026-09-12 新增：拦截垃圾，防中枢变仓库） ----------
_JUNK_TAGS = {'test', 'ping', 'load', 'probe', '压测', '测试', '自检', '连通性', 'benchmark', 'concurrency', '并发'}
_JUNK_PAT = re.compile(
    r'(压测|并发测试|测试条目|test\s*entry|ping\b|连通性测试|load\s*test|benchmark|自检|冒烟|探针)',
    re.IGNORECASE)


def is_junk(text: str, tag: str) -> str:
    """判断一条记忆是否是垃圾/一次性事件。返回原因字符串；不是垃圾返回空串。"""
    t = (text or '').strip()
    tg = (tag or '').strip().lower()
    if tg in _JUNK_TAGS:
        return '标签 %r 属测试/压测类' % tag
    if len(t) < 4:
        return '正文过短（<4字），疑似占位'
    m = _JUNK_PAT.search(t)
    if m:
        return '命中垃圾特征词 %r' % m.group(0)
    return ''


# ---------- 水位 ----------
def load_wm() -> dict:
    if WM.exists():
        try:
            return json.loads(WM.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_wm(w: dict) -> None:
    _atomic_write(WM, json.dumps(w, ensure_ascii=False, indent=2))


# ---------- 渲染 ----------
def render_all(quiet=False) -> None:
    """重生成 DIGEST.md 与全部投影。失败不阻断写入。"""
    try:
        sys.path.insert(0, str(HUB))
        import project as proj
        proj.regenerate_all()
        _render_digest()
        if not quiet:
            print('OK: DIGEST.md 与投影已重生成')
    except Exception as e:
        sys.stderr.write('[warn] 渲染失败（不影响已落盘的记忆）: %s\n' % e)


def _render_digest() -> None:
    d = load()
    tb = {}
    try:
        tb = json.loads((HUB / 'toolbox.json').read_text(encoding='utf-8'))
    except Exception:
        pass
    lines = ['# 记忆中枢摘要 DIGEST（自动生成，勿手改）', '',
             '> 由 `mem.py render` 生成，时间 %s。机器可读权威是 `sink.json`；本文件供会话注入。' % _now(),
             '> 手写经验正文在 `experience.md`；用户画像在 `profile.md`。', '']
    tb_lines = []
    for k, v in tb.items():
        if isinstance(v, dict):
            tb_lines.append('- **%s**: %s' % (k, '；'.join('%s=%s' % (a, sanitize(str(b))[:70]) for a, b in v.items())))
        else:
            tb_lines.append('- **%s**: %s' % (k, sanitize(str(v))[:100]))
    lines += ['## 工具箱 / 通道', *tb_lines, '']
    lines += ['## 记忆条目计数', ' / '.join('%s=%d' % (t, len(d.get(t, []) or [])) for t in TYPES), '']
    for t in TYPES:
        arr = (d.get(t, []) or [])[-8:]
        lines.append('## 最近 %s（最新 8 条）' % t)
        if not arr:
            lines.append('- （空）')
        for e in arr:
            lines.append('- `%s` **[%s]** %s%s' % (
                e.get('ts', ''), e.get('source', '?'), sanitize(str(e.get('text', '')))[:160],
                ('  #' + e['tag']) if e.get('tag') else ''))
        lines.append('')
    _atomic_write(HUB / 'DIGEST.md', '\n'.join(lines))


# ---------- 命令实现 ----------
def _add_inproc(text: str, source: str, tag: str = '', force: bool = False) -> bool:
    """进程内写入一条记忆（供 distill --commit 复用，避免子进程 spawn）。
    返回 True=已写入/合并，False=被质检拒绝。"""
    text = sanitize((text or '').strip())
    if not text:
        return False
    junk_reason = is_junk(text, tag)
    if junk_reason and not force:
        return False
    src = _validate_source(source)
    with _Lock():
        d = load()
        arr = d.setdefault('fact', [])
        rev = _rev(text)
        for e in arr:
            if e.get('rev') == rev:
                e.setdefault('sources', [e.get('source', 'legacy')])
                if src not in e['sources']:
                    e['sources'].append(src)
                e['count'] = int(e.get('count') or 1) + 1
                e['last_ts'] = _now()
                if tag and not e.get('tag'):
                    e['tag'] = tag
                save(d)
                return True
        seq = _max_seq(d) + 1
        entry = {
            'id': 'm-%s-%04d' % (time.strftime('%Y%m%d%H%M%S'), seq % 10000),
            'seq': seq, 'ts': _now(), 'type': 'fact', 'text': text,
            'tag': tag, 'ref': '', 'source': src,
            'sources': [src], 'count': 1, 'first_ts': _now(), 'rev': rev,
        }
        arr.append(entry)
        save(d)
    return True


def cmd_add(a) -> None:
    src = _validate_source(a.source)
    if a.type not in TYPES:
        raise SystemExit('ERROR: --type 必须是 %s' % '|'.join(TYPES))
    text = sanitize((a.text or '').strip())
    if not text:
        raise SystemExit('ERROR: --text 不能为空')
    tag = (a.tag or '').strip()
    ref = (a.ref or '').strip()
    # 入口质检：垃圾/一次性事件默认拒绝，除非显式 --force
    junk_reason = is_junk(text, tag)
    if junk_reason and not getattr(a, 'force', False):
        raise SystemExit('REJECTED: 命中入口质检 —— %s。\n'
                         '（若确为有效结论，加 --force 强制写入）' % junk_reason)
    # 【2026-09-13】主路：委托 gateway 写入 memory.db（唯一真源），不再写 sink.json
    try:
        gw = _gw()
        r = gw.remember(text, type=a.type, source=src, tags=tag, mem0=True)
        print('OK: %s 已沉淀到 memory.db (%s, uid=%s)' % (a.type, r.get('op'), r.get('uid')))
        try:
            gw.rebuild()
        except Exception:
            pass
        return
    except Exception as e:
        sys.stderr.write('[warn] gateway 写入失败，退回 sink.json 兜底: %s\n' % e)
    with _Lock():
        d = load()
        arr = d.setdefault(a.type, [])
        rev = _rev(text)
        for e in arr:
            if e.get('rev') == rev:
                e.setdefault('sources', [e.get('source', 'legacy')])
                if src not in e['sources']:
                    e['sources'].append(src)
                e['count'] = int(e.get('count') or 1) + 1
                e['last_ts'] = _now()
                if tag and not e.get('tag'):
                    e['tag'] = tag
                save(d)
                print('MERGED: 已有同一记忆 %s（来源累积为 %s，count=%d）'
                      % (e.get('id'), ','.join(e['sources']), e['count']))
                render_all()
                return
        seq = _max_seq(d) + 1
        entry = {
            'id': 'm-%s-%04d' % (time.strftime('%Y%m%d%H%M%S'), seq % 10000),
            'seq': seq, 'ts': _now(), 'type': a.type, 'text': text,
            'tag': tag, 'ref': ref, 'source': src,
            'sources': [src], 'count': 1, 'first_ts': _now(), 'rev': rev,
        }
        arr.append(entry)
        save(d)
        # 人读镜像：只追加，绝不重写已有手写段落
        try:
            with open(MD, 'a', encoding='utf-8') as f:
                f.write('\n- [%s] %s%s  <src=%s>\n' % (
                    time.strftime('%Y-%m-%d'), text,
                    (' (%s)' % tag) if tag else '', src))
        except Exception:
            pass
    print('OK: %s 已沉淀 %s (seq=%d, 共 %d 条)' % (a.type, entry['id'], seq, len(arr)))
    render_all()


def _fmt(t, e, mark_own=None) -> str:
    own = ' ←自己' if (mark_own and e.get('source') == mark_own) else ''
    return '[%s|%s] %s | %s%s%s' % (
        t, e.get('source', '?'), e.get('ts', ''),
        sanitize(str(e.get('text', '')))[:200],
        (' | #' + e['tag']) if e.get('tag') else '', own)


def cmd_list(a) -> None:
    try:
        facts = _gw_facts('active')
    except Exception:
        facts = []
    if facts:
        if a.type:
            facts = [f for f in facts if f.get('type') == a.type]
        if a.source:
            facts = [f for f in facts if f.get('source') == a.source]
        print('记忆中枢(memory.db, 唯一真源): active=%d' % len(facts))
        if a.by_source:
            agg = {}
            for f in facts:
                agg[f.get('source', '?')] = agg.get(f.get('source', '?'), 0) + 1
            print('按来源: ' + ' / '.join('%s=%d' % kv for kv in sorted(agg.items())))
        for f in (facts[:a.tail] if a.tail else facts):
            print('  [%s|%s] %s | %s%s' % (f.get('type'), f.get('source'), f.get('updated_at'),
                                          sanitize(str(f.get('content')))[:200],
                                          (' | #' + f['tags']) if f.get('tags') else ''))
        return
    d = load()
    items = all_entries(d)
    if a.type:
        items = [x for x in items if x[0] == a.type]
    if a.source:
        items = [x for x in items if x[1].get('source') == a.source
                 or a.source in (x[1].get('sources') or [])]
    items.sort(key=lambda x: int(x[1].get('seq') or 0), reverse=True)
    print('记忆中枢: ' + ' / '.join('%s=%d' % (t, len(d.get(t, []) or [])) for t in TYPES))
    if a.by_source:
        agg = {}
        for _, e in items:
            agg[e.get('source', '?')] = agg.get(e.get('source', '?'), 0) + 1
        print('按来源: ' + ' / '.join('%s=%d' % kv for kv in sorted(agg.items())))
    for t, e in (items[:a.tail] if a.tail else items):
        print('  ' + _fmt(t, e))


def cmd_search(a) -> None:
    """检索记忆。主路走 gateway 混合检索（向量+ASCII+字面），失败退回关键词匹配。

    ★支持一次问多个问题：`mem.py search "q1" "q2" "q3"`。
      动机（2026-09-15 实测）：本地 embedding 模型的**冷启动约 2.3 秒**，
      但模型加载后**单次查询只要 0.04 秒**（差 50~100 倍）。
      而短进程模式下每个查询都要重新加载模型 —— 于是"一次问 5 件事"
      从 5×4.2 秒变成 1 次加载 + 5 次查询。
      这是**不做常驻服务**的替代方案：既不长期占 ~930MB 内存，又不为每个问题重复付冷启动。
    """
    kws = a.kw if isinstance(a.kw, list) else [a.kw]
    try:
        gw = _gw()
        for qi, kw in enumerate(kws):
            if len(kws) > 1:
                print('─' * 60)
            r = gw.search(kw, limit=a.limit)
            res = r.get('results', [])
            print('命中 %d 条（engine=%s, 查询 %r）' % (len(res), r.get('engine', '?'), kw))
            for item in res:
                print('  [%s|%s] %.3f %s' % (item.get('type', 'fact'), item.get('source', '?'),
                                              item.get('score', 0), str(item.get('content', ''))[:200]))
        return
    except Exception as e:
        sys.stderr.write('[warn] gateway 检索失败，退回关键词匹配: %s\n' % e)
    for kw in kws:
        kw = (kw or '').lower()
        hits = []
        for t, e in all_entries():
            hay = (str(e.get('text', '')) + ' ' + str(e.get('tag', '')) + ' ' + str(e.get('ref', ''))).lower()
            if kw in hay:
                hits.append((t, e))
        hits.sort(key=lambda x: int(x[1].get('seq') or 0), reverse=True)
        print('命中 %d 条（关键词 %r）' % (len(hits), kw))
        for t, e in hits[:a.limit]:
            print('  ' + _fmt(t, e))


def cmd_since(a) -> None:
    ts = (a.ts or '').strip()
    items = [(t, e) for t, e in all_entries() if str(e.get('ts', '')) > ts]
    if a.source:
        items = [x for x in items if x[1].get('source') == a.source]
    items.sort(key=lambda x: int(x[1].get('seq') or 0))
    print('自 %s 起新增 %d 条' % (ts, len(items)))
    for t, e in items:
        print('  ' + _fmt(t, e))


def cmd_drain(a) -> None:
    """取未读条目。数据源 = memory.db，水位基于 updated_at。"""
    try:
        facts = _gw_facts('active')
    except Exception:
        facts = []
    if facts:
        w = load_wm()
        last = str((w.get(a.agent) or {}).get('last_updated') or '')
        items = [f for f in facts if not last or str(f.get('updated_at') or '') > last]
        items.sort(key=lambda x: str(x.get('updated_at') or ''))
        if a.limit:
            items = items[:a.limit]
        print('未读 %d 条（%s 水位 last_updated=%s）' % (len(items), a.agent, last or '(无)'))
        for f in items:
            print('  [%s|%s] %s | %s%s' % (f.get('type'), f.get('source'), f.get('updated_at'),
                                          sanitize(str(f.get('content')))[:200],
                                          (' | #' + f['tags']) if f.get('tags') else ''))
        if not a.peek and items:
            new_last = max(str(f.get('updated_at') or '') for f in items)
            w[a.agent] = {'last_updated': new_last, 'at': _now()}
            save_wm(w)
            print('水位已推进 -> last_updated=%s' % new_last)
        elif not items:
            w.setdefault(a.agent, {'last_updated': last, 'at': _now()})
            save_wm(w)
            print('无未读，水位保持 %s' % (last or '(无)'))
        return
    # 兜底：旧 sink.json 路径
    d = load()
    w = load_wm()
    last = int((w.get(a.agent) or {}).get('last_seq') or 0)
    items = [(t, e) for t, e in all_entries(d) if int(e.get('seq') or 0) > last]
    items.sort(key=lambda x: int(x[1].get('seq') or 0))
    if a.limit:
        items = items[:a.limit]
    print('未读 %d 条（%s 水位 last_seq=%d）' % (len(items), a.agent, last))
    for t, e in items:
        print('  ' + _fmt(t, e, mark_own=a.agent))
    if not a.peek and items:
        new_last = max(int(e.get('seq') or 0) for _, e in items)
        w[a.agent] = {'last_seq': new_last, 'at': _now()}
        save_wm(w)
        print('水位已推进 -> last_seq=%d' % new_last)
    elif not items:
        w.setdefault(a.agent, {'last_seq': last, 'at': _now()})
        save_wm(w)
        print('无未读，水位保持 %d' % last)


def cmd_agents(a) -> None:
    reg = load_agents()
    print('记忆中枢 agent 注册表（v%s）' % reg.get('version', '?'))
    for k, v in (reg.get('agents') or {}).items():
        print('  - %-10s %s' % (k, (v or {}).get('display', '')))
        for f in ('projection', 'writes_via'):
            if (v or {}).get(f):
                print('      %-10s %s' % (f + ':', v[f]))
    print('已注册来源: %s' % (', '.join(known_sources()) or '（无）'))


def cmd_verify(a) -> None:
    d = load()
    problems = []
    seen_id, seen_seq = {}, {}
    fix = bool(a.fix)
    for t in TYPES:
        for i, e in enumerate(d.get(t, []) or []):
            if not isinstance(e, dict):
                problems.append('%s[%d] 不是对象' % (t, i))
                continue
            for f in ('id', 'seq', 'ts', 'text', 'source'):
                if not e.get(f):
                    problems.append('%s[%d] 缺字段 %s' % (t, i, f))
            eid = e.get('id')
            if eid:
                if eid in seen_id:
                    problems.append('id 重复: %s' % eid)
                seen_id[eid] = True
            sq = e.get('seq')
            if sq:
                if sq in seen_seq:
                    problems.append('seq 重复: %s' % sq)
                seen_seq[sq] = True
            if not e.get('rev') and e.get('text'):
                if fix:
                    e['rev'] = _rev(e['text'])
    if fix:
        n = _max_seq(d)
        for t in TYPES:
            for e in d.get(t, []) or []:
                if isinstance(e, dict):
                    if not e.get('id'):
                        n += 1
                        e['seq'] = e.get('seq') or n
                        e['id'] = 'm-%s-%04d' % (time.strftime('%Y%m%d%H%M%S'), n % 10000)
                    if not e.get('source'):
                        e['source'] = 'legacy'
                    e.setdefault('sources', [e['source']])
                    e.setdefault('count', 1)
        save(d)
    tot = sum(len(d.get(t, []) or []) for t in TYPES)
    print('校验完成: 共 %d 条 / %d 个问题%s' % (tot, len(problems), '（已尝试修复）' if fix else ''))
    for p in problems[:40]:
        print('  ! ' + p)
    if not problems:
        print('  ✓ 全部条目字段完整、id/seq 无重复')


def cmd_render(a) -> None:
    render_all()


def cmd_migrate(a) -> None:
    d = load()
    n = _max_seq(d)
    added = 0
    for t in TYPES:
        for e in d.get(t, []) or []:
            if not isinstance(e, dict):
                continue
            if e.get('seq') and e.get('id') and e.get('source') and e.get('rev'):
                continue
            if not e.get('rev') and e.get('text'):
                e['rev'] = _rev(e['text'])
            if not e.get('seq'):
                n += 1
                e['seq'] = n
                added += 1
            if not e.get('id'):
                e['id'] = 'm-%s-%04d' % (time.strftime('%Y%m%d%H%M%S'), e['seq'] % 10000)
            if not e.get('source'):
                e['source'] = 'legacy'
            e.setdefault('sources', [e['source']])
            e.setdefault('count', 1)
            e.setdefault('first_ts', e.get('ts', ''))
    # seq 重排，保证单调
    for t in TYPES:
        arr = d.get(t, []) or []
        arr.sort(key=lambda x: (str(x.get('ts', '')), int(x.get('seq') or 0)))
    save(d)
    print('迁移完成: 补齐 %d 条 seq，现有 %d 条' % (added, sum(len(d.get(t, []) or []) for t in TYPES)))
    render_all()


def cmd_distill(a) -> None:
    """蒸馏：把若干散条记忆提炼成 1~3 条结论，用最强模型（默认 gptx_astra）。

    用法:
      python mem.py distill --source workbuddy --since "2026-09-01" --model gptx_astra
    产出：提炼出的结论（不自动写回，打印给你审；加 --commit 才写入中枢）。
    调用模型走 ai-audit/consilium.call_llm（复用成熟通道 + 记账 + 超时 + 空回复重试）。
    """
    import subprocess as _sp
    d = load()
    items = [(t, e) for t, e in all_entries(d)]
    if a.since:
        items = [x for x in items if str(x[1].get('ts', '')) >= a.since]
    if a.source:
        items = [x for x in items if x[1].get('source') == a.source]
    if a.tag:
        items = [x for x in items if x[1].get('tag') == a.tag]
    if not items:
        print('无可蒸馏条目（检查 --since/--source/--tag 过滤）')
        return
    items.sort(key=lambda x: int(x[1].get('seq') or 0))
    print('待蒸馏 %d 条，调用 %s 提炼…' % (len(items), a.model))
    # 拼喂给模型的原始条目（脱敏后）
    raw = []
    for t, e in items:
        raw.append('[%s|%s|%s] %s' % (t, e.get('source', '?'), e.get('ts', ''), e.get('text', '')))
    prompt = (
        '你是一个记忆中枢的「蒸馏器」。下面是一堆 agent 记录的散条（可能含流水账、重复、临时状态）。\n'
        '请把它们提炼成【结论级】记忆。要求：\n'
        '1. 只保留可复用结论/稳定约定/跨 agent 经验，丢弃流水账和一次性事件；\n'
        '2. 合并近义重复，去掉过时信息（同一主题只留最新结论）；\n'
        '3. 按主题分组输出，每个主题 1~3 条结论；务必覆盖所有主题，不要只挑最通用的几条而漏掉具体技术经验；\n'
        '4. 每条结论一句话中文说清「结论 + 关键依据」，不超过 80 字；tag 用短主题词（如 通道/模型/GUI/机制/互通/STM32/通知）；\n'
        '5. 输出纯 JSON，格式 {"conclusions":[{"text":"...","tag":"..."}, ...]}，不要输出别的。\n\n'
        '原始条目：\n' + '\n'.join(raw)
    )
    # 调用 consilium.load_cfg + get_channel（复用成熟通道解析）
    code = (
        "import sys, json; sys.path.insert(0, r'E:\\RUANJIAN\\ai-audit'); "
        "from consilium import call_llm, load_cfg, get_channel; "
        "cfg = load_cfg(); url, key, model = get_channel(cfg, '%s'); "
        "content, usage, dt, err = call_llm('%s', url, key, model, "
        "[{'role':'user','content': sys.argv[1]}], max_tokens=1500, timeout=90, retries=1); "
        "print('__RESULT__'); print(content); print('__USAGE__', json.dumps(usage, ensure_ascii=False)); "
        "print('__ERR__', err)"
    ) % (a.model, a.model)
    try:
        r = _sp.run(
            [sys.executable, '-c', code, prompt],
            capture_output=True, text=True, timeout=180, encoding='utf-8', errors='ignore')
        out = r.stdout or ''
        err_out = r.stderr or ''
    except Exception as e:
        print('蒸馏调用失败: %s' % e)
        return
    marker = '__RESULT__'
    content = ''
    usage = '{}'
    err = ''
    if marker in out:
        seg = out.split(marker, 1)[1]
        if '__USAGE__' in seg:
            content, rest = seg.split('__USAGE__', 1)
            if '__ERR__' in rest:
                usage, err = rest.split('__ERR__', 1)
            else:
                usage = rest
        else:
            content = seg
        content = content.strip()
    else:
        # 兜底：整段输出当 content
        content = out.strip()
    if err and not content:
        print('蒸馏失败（通道错误）: %s' % err.strip())
        if err_out:
            print('[stderr] %s' % err_out.strip()[:500])
        return
    # 解析 JSON
    conclusions = []
    try:
        s, e = content.find('{'), content.rfind('}')
        if s >= 0 and e > s:
            obj = json.loads(content[s:e + 1])
            conclusions = obj.get('conclusions') or []
    except Exception:
        pass
    if not conclusions:
        print('未能解析出结论（模型输出非预期格式）：')
        print(content[:800])
        return
    print('== 蒸馏出 %d 条结论（模型 %s）==' % (len(conclusions), a.model))
    for i, c in enumerate(conclusions, 1):
        print('  %d. [%s] %s' % (i, c.get('tag', ''), c.get('text', '')))
    if getattr(a, 'commit', False):
        n = 0
        for c in conclusions:
            txt = (c.get('text') or '').strip()
            tg = (c.get('tag') or '').strip()
            if not txt:
                continue
            # 进程内直接写入（走锁 + 脱敏 + 去重），避免子进程 spawn 超时
            ok = _add_inproc(txt, a.source or 'workbuddy', tg, force=True)
            if ok:
                n += 1
            else:
                print('  [写入失败] %s' % txt[:60])
        render_all(quiet=True)
        print('已写入 %d 条结论（source=%s）' % (n, a.source or 'workbuddy'))
    else:
        print('（预览模式。加 --commit 才会写回中枢）')


def cmd_stats(a) -> None:
    # 主路：gateway/memory.db 统计（唯一真源）
    try:
        gw = _gw()
        st = gw.stats()
        print('=== memory.db（唯一真源）===')
        print('条目总数: %d' % st.get('facts', 0))
        print('按状态: %s' % st.get('facts_by_status', {}))
        print('工具资产: %d / 审计日志: %d / 候选: %d' % (
            st.get('tool_assets', 0), st.get('audit_log', 0), st.get('candidates', 0)))
        w = load_wm()
        print('读取水位:')
        for k, v in (w or {}).items():
            print('  %-11s %s' % (k, v))
        print('已注册 agent: %s' % (', '.join(known_sources()) or '（无）'))
    except Exception as e:
        print('[warn] gateway 统计失败，显示 sink.json 兜底: %s' % e)
    print()
    print('=== sink.json（投影，仅供参考）===')
    d = load()
    reg = load_agents()
    w = load_wm()
    tot = sum(len(d.get(t, []) or []) for t in TYPES)
    print('条目总数: %d' % tot)
    for t in TYPES:
        print('  %-11s %d' % (t, len(d.get(t, []) or [])))
    agg = {}
    for _, e in all_entries(d):
        s = e.get('source', '?')
        agg[s] = agg.get(s, 0) + 1
    print('按来源:')
    for k in sorted(agg):
        print('  %-11s %d' % (k, agg[k]))
    print('已注册 agent: %s' % (', '.join(known_sources()) or '（无）'))
    print('读取水位:')
    for k, v in (w or {}).items():
        print('  %-11s last_seq=%s (%s)' % (k, v.get('last_seq'), v.get('at', '')))
    print('文件: sink=%d B / experience.md=%d B' % (
        DB.stat().st_size if DB.exists() else 0, MD.stat().st_size if MD.exists() else 0))


def _env_guard() -> None:
    """★2026-09-15 新增：解释器守卫。

    本中枢的检索依赖 chromadb/numpy，它们只装在 `.venv-memory`。
    用默认 python 跑不会报错，只是**静默降级成纯关键词检索**——
    这个坑真实发生过（66 条资产查不到 + 误判「语义检索不可用」，白查一天）。
    与其让别人重踩，不如在入口就明说。
    """
    try:
        sys.path.insert(0, str(HUB))
        import memsearch
        miss = memsearch.check_env()
    except Exception:
        return
    if miss:
        sys.stderr.write(
            '\n[!] 当前解释器缺 %s，检索会降级为纯关键词（资源类查询大概率查不到）。\n'
            '    正确解释器: E:\\RUANJIAN\\memory_hub\\.venv-memory\\Scripts\\python.exe\n'
            '    正确用法:   PYTHONPATH= .venv-memory/Scripts/python.exe mem.py search "<关键词>"\n\n'
            % ', '.join(miss))


def cmd_asof(a) -> None:
    """双时间轴 as-of 查询。

      --kind valid   T 轴：在 at 时刻，**现实世界**哪些事实成立
      --kind known   T'轴：在 at 时刻，**系统认为**哪些事实成立

    ★两者不同正是 bi-temporal 的价值。本库真实例：
      「DeepSeek 官方 API 已充值可用」现实里 09-15 失效、系统当晚 21:07 才发现。
      问 2026-09-15 12:00 → valid 已失效、known 仍认为可用。
      只有两条轴都在，才能解释"当时为什么那样决策"。
    """
    import gateway as _gw
    r = _gw.as_of(a.at, kind=a.kind, ftype=getattr(a, 'ftype', None), limit=a.limit)
    lab = '现实成立(T轴)' if a.kind == 'valid' else "系统当时认为(T'轴)"
    print('as-of %s  [%s]  命中 %d 条   （当前 active 共 %s 条）'
          % (r['at'], lab, r['count'], r['active_now']))
    print('-' * 78)
    shown = 0
    for x in r['rows']:
        if shown >= 30:
            print('… 另有 %d 条未显示（用 --limit 调整）' % (r['count'] - shown))
            break
        print('[%-10s] %s  %s' % (x['status'], x['uid'][:30],
                                  ('vf=%s vt=%s' % (x['valid_from'] or '?',
                                                    x['valid_to'] or '至今'))
                                  if a.kind == 'valid' else
                                  ('rec=%s inv=%s' % (x['recorded_at'] or '?',
                                                      x['invalidated_at'] or '至今'))))
        print('    %s' % (x['content'] or '')[:104].replace('\n', ' '))
        shown += 1


def cmd_timeline(a) -> None:
    """沿替代链还原一条事实的完整演化（支持 uid 前缀）。"""
    import gateway as _gw
    uid = a.uid
    c = sqlite3.connect(str(HUB / 'memory.db'))
    if not c.execute('SELECT 1 FROM facts WHERE uid=?', (uid,)).fetchone():
        rs = c.execute('SELECT uid FROM facts WHERE uid LIKE ?', (uid + '%',)).fetchall()
        c.close()
        if len(rs) == 1:
            uid = rs[0][0]
        elif len(rs) > 1:
            print('前缀命中 %d 条，请给更长的 uid：' % len(rs))
            for r in rs[:12]:
                print('   %s' % r[0])
            return
        else:
            print('未找到: %s' % a.uid)
            return
    else:
        c.close()

    ch = _gw.timeline(uid)
    print('演化链 %d 段（沿 superseded_by 前进）：' % len(ch))
    for i, s in enumerate(ch):
        print()
        print('[%d] %s   status=%s   temporal_source=%s'
              % (i, s['uid'], s['status'], s['temporal_source'] or '-'))
        print("    T  有效窗口: %-19s → %s" % (s['valid_from'] or '?', s['valid_to'] or '至今'))
        print("    T' 摄录窗口: %-19s → %s" % (s['recorded_at'] or '?', s['invalidated_at'] or '至今'))
        print('    %s' % (s['content'] or '')[:190].replace('\n', ' '))


def main() -> None:
    _env_guard()
    ap = argparse.ArgumentParser(description='多智能体共享记忆总线')
    sub = ap.add_subparsers(dest='cmd')

    a = sub.add_parser('add'); a.add_argument('--type', required=True, choices=TYPES)
    a.add_argument('--text', required=True); a.add_argument('--source', required=True)
    a.add_argument('--tag', default=''); a.add_argument('--ref', default='')
    a.add_argument('--force', action='store_true', help='跳过入口质检，强制写入')

    l = sub.add_parser('list'); l.add_argument('--type', choices=TYPES)
    l.add_argument('--tail', type=int, default=0); l.add_argument('--source')
    l.add_argument('--tag', default='')
    l.add_argument('--by-source', action='store_true')

    s = sub.add_parser('search'); s.add_argument('kw', nargs='+',
                                                 help='一个或多个查询（多个时共用一次模型加载）')
    s.add_argument('--limit', type=int, default=20)

    sc = sub.add_parser('since'); sc.add_argument('ts'); sc.add_argument('--source')

    dr = sub.add_parser('drain'); dr.add_argument('--agent', required=True)
    dr.add_argument('--limit', type=int, default=20); dr.add_argument('--peek', action='store_true')

    r = sub.add_parser('recall'); r.add_argument('--agent', required=True)
    r.add_argument('--budget', type=int, default=4000)

    sub.add_parser('agents'); sub.add_parser('agents-registry', help=argparse.SUPPRESS)
    v = sub.add_parser('verify'); v.add_argument('--fix', action='store_true')
    sub.add_parser('render'); sub.add_parser('migrate'); sub.add_parser('stats')

    di = sub.add_parser('distill', help='蒸馏：用最强模型把散条提炼成结论')
    di.add_argument('--source', default='')
    di.add_argument('--since', default='', help='只蒸馏此时间之后的条目，如 2026-09-01')
    di.add_argument('--tag', default='')
    di.add_argument('--model', default='gptx_astra', help='提炼用的通道名，默认 gptx_astra')
    di.add_argument('--commit', action='store_true', help='把结论写回中枢（默认只预览）')

    ao = sub.add_parser('asof', help='双时间轴查询：某时刻什么为真 / 系统当时认为什么为真')
    ao.add_argument('at', help='时间点，如 2026-09-15 或 "2026-09-15 22:00"')
    ao.add_argument('--kind', choices=['valid', 'known'], default='valid',
                    help="valid=现实成立(T轴) / known=系统当时认为(T'轴)")
    ao.add_argument('--type', dest='ftype', choices=TYPES)
    ao.add_argument('--limit', type=int, default=50)

    tl = sub.add_parser('timeline', help='沿替代链还原一条事实的演化过程')
    tl.add_argument('uid')

    args = ap.parse_args()
    fn = {'add': cmd_add, 'list': cmd_list, 'search': cmd_search, 'since': cmd_since,
          'drain': cmd_drain, 'agents': cmd_agents, 'verify': cmd_verify,
          'render': cmd_render, 'migrate': cmd_migrate, 'stats': cmd_stats,
          'distill': cmd_distill, 'asof': cmd_asof, 'timeline': cmd_timeline}.get(args.cmd)
    if not fn:
        ap.print_help()
        return
    if args.cmd == 'recall':
        cmd_recall(args)
    else:
        fn(args)


def cmd_recall(a) -> None:
    """生成会话注入块：画像 + 工具箱摘要 + 最近记忆 + 未读。
    数据源 = memory.db（唯一真源）。不推进水位。"""
    agent = a.agent
    prof = ''
    p = HUB / 'profile.md'
    if p.exists():
        prof = sanitize(p.read_text(encoding='utf-8'))
    try:
        facts = _gw_facts('active')
    except Exception as e:
        sys.stderr.write('[warn] 读 memory.db 失败，退回 sink.json: %s\n' % e)
        facts = []
    if facts:
        w = load_wm()
        last = str((w.get(agent) or {}).get('last_updated') or '')
        fresh = [f for f in facts if last and str(f.get('updated_at') or '') > last]
        recent = facts[:10]
    else:
        d = load()
        w = load_wm()
        last = int((w.get(agent) or {}).get('last_seq') or 0)
        fresh = [(t, e) for t, e in all_entries(d) if int(e.get('seq') or 0) > last]
        fresh.sort(key=lambda x: int(x[1].get('seq') or 0))
        recent = sorted(all_entries(d), key=lambda x: int(x[1].get('seq') or 0), reverse=True)[:10]
        fresh = [{'updated_at': e.get('ts'), 'source': e.get('source'),
                  'content': e.get('text'), 'type': t, 'tags': e.get('tag')} for t, e in fresh]
        recent = [{'updated_at': e.get('ts'), 'source': e.get('source'), 'content': e.get('text'),
                   'type': t, 'tags': e.get('tag')} for t, e in recent]

    blocks = ['# 共享记忆中枢注入（agent=%s, %s）' % (agent, _now()), '',
              '> 唯一真源 = memory.db。写入走 `mem.py add --source %s`（内部委托 gateway），不要直接改 sink.json。' % agent, '']
    blocks += ['## 用户画像（profile.md）', prof.strip(), '']
    if fresh:
        blocks += ['## 你上次之后的新记忆（%d 条，未读）' % len(fresh)]
        for f in fresh:
            blocks.append('- `%s` [%s] %s%s' % (f.get('updated_at'), f.get('source'),
                                                sanitize(str(f.get('content')))[:200],
                                                ('  #' + f['tags']) if f.get('tags') else ''))
        blocks.append('')
    else:
        blocks += ['## 你上次之后的新记忆', '- （无新条目）', '']
    blocks += ['## 中枢最近 10 条记忆']
    for f in recent:
        blocks.append('- `%s` [%s/%s] %s%s' % (f.get('updated_at'), f.get('type'), f.get('source'),
                                              sanitize(str(f.get('content')))[:160],
                                              ('  #' + f['tags']) if f.get('tags') else ''))
    text = '\n'.join(blocks)
    if len(text) > a.budget:
        text = text[:a.budget] + '\n…（已按 budget=%d 截断，完整内容读 %s）' % (a.budget, HUB)
    print(text)


if __name__ == '__main__':
    main()
