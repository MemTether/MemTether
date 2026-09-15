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
import time
from pathlib import Path

HUB = Path(os.path.dirname(os.path.abspath(__file__)))
PROJ = HUB / 'projections'
AGENTS_F = HUB / 'agents.json'
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


def _entry_lines(sink: dict, n=6) -> str:
    lines = []
    for t in ('experience', 'fact', 'todo'):
        for e in (sink.get(t, []) or [])[-n:]:
            lines.append('`%s` **[%s/%s]** %s%s' % (
                e.get('ts', ''), t, e.get('source', '?'),
                _sanitize(str(e.get('text', '')))[:180],
                ('  #' + e['tag']) if e.get('tag') else ''))
    return '\n'.join(lines) or '（暂无沉淀）'


def build_projection(agent: str, display: str = '', peers=()) -> str:
    profile = _sanitize(read_text(HUB / 'profile.md'))
    sink = read_json(HUB / 'sink.json')
    counts = ' / '.join('%s=%d' % (t, len(sink.get(t, []) or [])) for t in ('experience', 'fact', 'todo'))
    reg = load_agents().get('agents') or {}
    my = reg.get(agent) or {}
    write_cmd = my.get('writes_via') or ('python %s\\mem.py add --source %s' % (HUB, agent))
    peers_txt = '、'.join(peers) if peers else '—'

    return '''# 会话开场投影 · %s（由 memory_hub 生成）

> 这是一个**三 agent 共用**的记忆中枢，不是某个 agent 私有的。
> 写入方式（不要直接改 sink.json）：`%s`
> 其他 agent 也会读写同一份，所以：记事实写结论、带来源、别覆盖别人的条目。
> 当前条目计数：%s

## Shared Toolbox
%s

## Account Profile
%s

## Recent Memory Index（最近各 6 条，带来源）
%s

> 本投影由 `E:\\RUANJIAN\\memory_hub\\project.py` 自动生成，时间 %s。
> 机器可读权威是 `sink.json`；人读全文是 `experience.md`；本文件为轻量注入快照，敏感信息不在此处。
> 未读增量请跑：`python mem.py drain --agent %s`（或 `recall --agent %s` 取完整注入块）。
''' % (display or agent, write_cmd, counts, _toolbox_lines(), profile,
       _entry_lines(sink), time.strftime('%Y-%m-%d %H:%M'), agent, agent)


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
    idx += ['', '> 生成时间 %s。真源 sink.json；人读 experience.md。' % time.strftime('%Y-%m-%d %H:%M')]
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
