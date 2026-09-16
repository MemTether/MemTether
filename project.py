# -*- coding: utf-8 -*-
"""project.py — 从记忆中枢生成每个 agent 的会话注入投影（唯一真源 → 投影）

改造自 v2（原本只服务两个豆包账号）。现在由 agents.json 驱动：
为注册表里每个 agent 生成 `projections/agent_<name>.md`；
豆包两账号额外保留 `account_a_prompt.md` / `account_b_prompt.md` 旧名，兼容既有会话注入配置。

用法:
  python project.py            # 重生成全部投影 + HUB_INDEX.md
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

HUB = Path(os.path.dirname(os.path.abspath(__file__)))
PROJ = HUB / 'projections'
AGENTS_F = HUB / 'agents.json'
DB_F = HUB / 'memory.db'          # ★唯一权威真源（2026-09-16 起，见 agents.json v4）
TYPES_ORDER = ('experience', 'fact', 'decision', 'incident', 'todo')
RECENT_N = 6                      # 每类投影几条最近条目
ALIAS = {'doubao_a': 'account_a_prompt.md', 'doubao_b': 'account_b_prompt.md'}

_SK_RE = re.compile(r'sk-[A-Za-z0-9_-]{20,}')
_KEY_RE = re.compile(r'[A-Za-z0-9]{32,}\.[A-Za-z0-9_-]{20,}')
_MASK = '<见vault:key>'


def _sanitize(s: str) -> str:
    s = _SK_RE.sub(_MASK, s)
    s = _KEY_RE.sub(_MASK, s)
    return s


def read_text(p: Path) -> str:
    return p.read_text(encoding='utf-8') if p.exists() else ''


def read_json(p: Path, default=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {} if default is None else default


def load_agents() -> dict:
    return read_json(AGENTS_F, {}) or {}


def _toolbox_lines() -> str:
    tb = read_json(HUB / 'toolbox.json')
    out = []
    for k, v in tb.items():
        if isinstance(v, dict):
            out.append('- %s: %s' % (k, '；'.join('%s=%s' % (a, _sanitize(str(b))) for a, b in v.items())))
        else:
            out.append('- %s: %s' % (k, _sanitize(str(v))))
    return '\n'.join(out) or '（空）'


def _entry_lines(n=6) -> str:
    """最近条目 —— **从 memory.db 取**，不再读 sink.json。

    ★2026-09-16 改：agents.json v4 已声明唯一权威真源是 memory.db，
      而本脚本当时仍读 sink.json（v3 时代的镜像），并把
      "机器可读权威是 sink.json" 这句话写进**每一个 agent 的投影**——
      等于给所有 agent 注入了一条过时的架构说明。投影是 agent 的先行认知，
      写错比不写更糟。
    """
    lines = []
    try:
        conn = sqlite3.connect(str(DB_F))
        try:
            for t in TYPES_ORDER:
                rows = conn.execute(
                    "SELECT content, source, created_at FROM facts "
                    "WHERE status='active' AND type=? ORDER BY created_at DESC LIMIT ?",
                    (t, n)).fetchall()
                for content, source, ts in rows:
                    lines.append('`%s` **[%s/%s]** %s' % (
                        (ts or '')[:16], t, source or '?',
                        _sanitize(str(content)).replace('\n', ' ')[:180]))
        finally:
            conn.close()
    except Exception as e:
        return '（读取 memory.db 失败：%s）' % e
    return '\n'.join(lines) or '（暂无沉淀）'


def _db_counts() -> dict:
    """各类型 active 条数（唯一真源 memory.db）。"""
    try:
        conn = sqlite3.connect(str(DB_F))
        try:
            return {t: conn.execute(
                "SELECT COUNT(*) FROM facts WHERE status='active' AND type=?",
                (t,)).fetchone()[0] for t in TYPES_ORDER}
        finally:
            conn.close()
    except Exception:
        return {}


def build_projection(agent: str, display: str = '', peers=()) -> str:
    profile = _sanitize(read_text(HUB / 'profile.md'))
    cnt = _db_counts()
    counts = (' / '.join('%s=%d' % (t, cnt.get(t, 0)) for t in TYPES_ORDER)
              if cnt else '（真源不可读）')
    reg = load_agents().get('agents') or {}
    my = reg.get(agent) or {}
    write_cmd = my.get('writes_via') or ('python %s\\mem.py add --source %s' % (HUB, agent))
    peers_txt = '、'.join(peers) if peers else '—'

    return '''# 会话开场投影 · %s（由 memory_hub 生成）

> 这是一个**多 agent 共用**的记忆中枢，不是某个 agent 私有的。
> 写入方式（不要直接改真源文件）：`%s`
> 其他 agent 也会读写同一份，所以：记事实写结论、带来源、别覆盖别人的条目。
> 当前活跃条目计数：%s

## Shared Toolbox
%s

## Account Profile
%s

## Recent Memory Index（各类最近 %d 条，带来源）
%s

> 本投影由 `E:\\RUANJIAN\\memory_hub\\project.py` 自动生成，时间 %s。
> **唯一权威真源是 `memory.db`（SQLite）**；`sink.json` 仅为兼容期镜像；人读全文 `experience.md`。
> 本文件是轻量注入快照，敏感信息不在此处。
> 未读增量请跑：`python mem.py drain --agent %s`（或 `recall --agent %s` 取完整注入块）。
''' % (display or agent, write_cmd, counts, _toolbox_lines(), profile,
       RECENT_N, _entry_lines(RECENT_N), time.strftime('%Y-%m-%d %H:%M'), agent, agent)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(str(tmp), str(path))


def regenerate_all(quiet: bool = True) -> list:
    PROJ.mkdir(exist_ok=True)
    reg = load_agents()
    agents = reg.get('agents') or {}
    peers = [k for k in agents.keys()]
    written = []
    for name, meta in agents.items():
        if not (meta or {}).get('can_read', True):
            continue
        text = build_projection(name, (meta or {}).get('display', ''), [p for p in peers if p != name])
        targets = [PROJ / ('agent_%s.md' % name)]
        if name in ALIAS:
            targets.append(PROJ / ALIAS[name])
        for tgt in targets:
            atomic_write(tgt, text)
            written.append(str(tgt))
    # 共享索引
    idx = ['# memory_hub 索引（自动生成）', '',
           '| agent | 说明 | 投影 | 写入方式 |', '|---|---|---|---|']
    for name, meta in agents.items():
        meta = meta or {}
        idx.append('| `%s` | %s | `%s` | %s |' % (
            name, meta.get('display', ''), meta.get('projection') or '-', meta.get('writes_via') or '-'))
    idx += ['', '> 生成时间 %s。唯一真源 `memory.db`；人读 `experience.md`。' % time.strftime('%Y-%m-%d %H:%M')]
    atomic_write(HUB / 'HUB_INDEX.md', '\n'.join(idx))
    written.append(str(HUB / 'HUB_INDEX.md'))
    if not quiet:
        for w in written:
            print('wrote %s' % w)
    return written


def main():
    for w in regenerate_all(quiet=False):
        print('wrote %s (%d bytes)' % (w, Path(w).stat().st_size))


if __name__ == '__main__':
    main()
