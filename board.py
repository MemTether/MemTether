# -*- coding: utf-8 -*-
"""board.py — 多 agent 共享任务板（status-as-claim + lease + fencing token）

为什么用这一套
--------------
"多个 worker 抢同一份工作"不是新问题，分布式系统里有成熟解法，不用自创：
  · Google Cloud —— Distributed locks with fencing tokens
  · Kubernetes  —— Lease 对象
  · AWS         —— DynamoDB Lock Client
核心三件套：**租约(lease) + 隔离令牌(fencing token) + 幂等**。

本机为什么要它
--------------
国内版 / 国际版两个 WorkBuddy 实例**共享同一份记忆**，但**不共享"谁在做哪件事"**。
结果是两边都会从 `MEMORY.md` 里读到同一份待办，可能重复干同一件活、
甚至互相覆盖结果。这里把「任务 + 状态 + 租约」放到两边都读得到的 sqlite 上，
**不引入任何新服务**（沿用 memory_hub 目录，不碰 memory.db）。

三条硬规则（照抄上面那套，别简化）
----------------------------------
1. **抢单 = 一次原子 UPDATE**。状态转移本身就是锁，谁 UPDATE 成功谁拿到；
   失败就退避/换一个，**不要让模型去"协商"**。
2. **租约会过期**。owner 崩了，别人能接管 —— 否则队列变成坟场。
3. **fence 单调递增**。过期后醒来的旧 owner 拿着旧 fence，它的写入必须被拒
   （否则它会覆盖新 owner 的成果）。fence 不校验，前面两条就是装饰品。

用法
----
    python board.py add "标题" [--detail "..."|--detail-file f] [--owner X] [--prio 0-3]
    python board.py list [--all] [--status open] [--as X] [--json]
    python board.py claim <id> --as workbuddy [--lease 900]
    python board.py renew <id> --as X --fence N
    python board.py done  <id> --as X --fence N [--note "..."]
    python board.py fail  <id> --as X --fence N --why "..." [--escalate]
    python board.py release <id> --as X
    python board.py gc            # 回收过期租约（把过期任务放回 open）
    python board.py show <id>     # 含事件流水

`--as` 建议用 agents.json 里已注册的来源名（workbuddy / workbuddy_ai / openclaw / user…），
未注册会**警告但不阻拦**（自造来源名会让归属变乱，报告里也不好归因）。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'board.db')
AGENTS = os.path.join(HERE, 'agents.json')

STATUS = ('open', 'claimed', 'blocked', 'done', 'failed', 'cancelled')
PRIO_CN = {0: '紧急', 1: '高', 2: '普通', 3: '低'}
OPENISH = ('open', 'failed', 'claimed')   # 可被抢的状态（claimed 还要租约过期）

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  detail        TEXT DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'open',
  owner         TEXT,
  lease_until   REAL,
  fence         INTEGER NOT NULL DEFAULT 0,
  prio          INTEGER NOT NULL DEFAULT 2,
  blocked_reason TEXT DEFAULT '',
  result        TEXT DEFAULT '',
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS events(
  seq     INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  ts      REAL NOT NULL,
  actor   TEXT DEFAULT '',
  action  TEXT NOT NULL,
  fence   INTEGER,
  note    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_events_task ON events(task_id);
"""


# ------------------------------------------------------------------ 基础设施
def now():
    return time.time()


def conn():
    """打开连接并保证表结构存在。WAL 让「一边读一边写」不互相卡。"""
    os.makedirs(HERE, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=8000')
    c.executescript(SCHEMA)
    return c


def registered_agents():
    try:
        j = json.load(open(AGENTS, encoding='utf-8'))
        return set((j.get('agents') or {}).keys())
    except Exception:
        return set()


def check_actor(actor):
    if not actor:
        return
    reg = registered_agents()
    if reg and actor not in reg:
        log('[warn] 来源名 %r 未在 agents.json 注册（已注册：%s）。'
            '自造来源名会让归属变乱 —— 建议先注册。'
            % (actor, ', '.join(sorted(reg))))


def log(*a):
    print(*a, file=sys.stderr)


def ev(c, task_id, actor, action, fence=None, note=''):
    c.execute('INSERT INTO events(task_id,ts,actor,action,fence,note) '
              'VALUES(?,?,?,?,?,?)', (task_id, now(), actor or '', action, fence, note or ''))


def fmt_ts(t):
    if not t:
        return '-'
    return time.strftime('%m-%d %H:%M', time.localtime(t))


def lease_left(row):
    if not row['lease_until']:
        return None
    return int(row['lease_until'] - now())


def human_lease(row):
    s = lease_left(row)
    if s is None:
        return '-'
    if s <= 0:
        return '已过期'
    return '%dm%02ds' % (s // 60, s % 60)


def get_task(c, tid):
    r = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not r:
        log('[err] 没有这个任务: %s' % tid)
        sys.exit(2)
    return r


# ------------------------------------------------------------------ 命令
def cmd_add(a):
    c = conn()
    tid = a.id or ('t-' + time.strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex[:4])
    detail = a.detail or ''
    if a.detail_file:
        detail = open(a.detail_file, encoding='utf-8').read().strip()
    owner = a.owner
    status = 'claimed' if owner else 'open'
    lease = now() + a.lease if owner else None
    fence = 1 if owner else 0
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO tasks(id,title,detail,status,owner,lease_until,fence,prio,'
                  'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (tid, a.title, detail, status, owner, lease, fence, a.prio, now(), now()))
        ev(c, tid, owner or '', 'add', fence, 'prio=%s' % PRIO_CN.get(a.prio, a.prio))
        c.execute('COMMIT')
    except sqlite3.IntegrityError:
        c.execute('ROLLBACK')
        log('[err] 任务 id 已存在: %s' % tid)
        sys.exit(2)
    print(tid)


def cmd_list(a):
    c = conn()
    q = 'SELECT * FROM tasks'
    args = []
    if not a.all:
        if a.status:
            q += ' WHERE status=?'
            args.append(a.status)
        else:
            q += " WHERE status IN ('open','claimed','blocked','failed')"
    q += ' ORDER BY prio ASC, created_at ASC'
    rows = c.execute(q, args).fetchall()
    if a.as_:
        rows = [r for r in rows
                if r['owner'] == a.as_                                   # 我持有的
                or r['status'] in ('open', 'failed')                     # 谁都能抢的
                or (r['status'] == 'claimed' and (lease_left(r) or 0) <= 0)]  # 租约已过期
    if a.json:
        print(json.dumps({'ts': now(), 'items': [dict(r) for r in rows]},
                         ensure_ascii=False))
        return
    if not rows:
        print('（没有任务）')
        return
    print('%-34s %-4s %-9s %-13s %-8s %s' % ('ID', '优先级', '状态', '负责人', '租约', '标题'))
    print('-' * 108)
    for r in rows:
        own = r['owner'] or '-'
        if r['status'] == 'claimed' and lease_left(r) is not None and lease_left(r) <= 0:
            own += '(过期)'
        print('%-34s %-4s %-9s %-13s %-8s %s' % (
            r['id'], PRIO_CN.get(r['prio'], r['prio']), r['status'], own,
            human_lease(r), r['title']))


def cmd_claim(a):
    """核心：一次原子 UPDATE 完成抢单。affected=0 就是被别人拿走了。"""
    c = conn()
    check_actor(a.as_)
    t = now()
    lease_until = t + a.lease
    c.execute('BEGIN IMMEDIATE')
    cur = c.execute(
        "UPDATE tasks SET status='claimed', owner=?, lease_until=?, fence=fence+1, "
        "blocked_reason='', updated_at=? "
        "WHERE id=? AND status IN ('open','failed','claimed') "
        "AND (lease_until IS NULL OR lease_until < ?)",
        (a.as_, lease_until, t, a.id, t))
    n = cur.rowcount
    if n:
        row = c.execute('SELECT * FROM tasks WHERE id=?', (a.id,)).fetchone()
        ev(c, a.id, a.as_, 'claim', row['fence'], 'lease=%ss' % a.lease)
        c.execute('COMMIT')
        print('CLAIMED fence=%d lease=%ds until=%s' % (
            row['fence'], a.lease, fmt_ts(lease_until)))
        return
    c.execute('ROLLBACK')
    row = c.execute('SELECT * FROM tasks WHERE id=?', (a.id,)).fetchone()
    if not row:
        log('[err] 没有这个任务: %s' % a.id)
        sys.exit(2)
    if row['status'] in ('done', 'cancelled'):
        print('CLOSED %s (已是 %s，别再动手)' % (a.id, row['status']))
    elif row['status'] == 'blocked':
        print('BLOCKED %s 卡住了：%s' % (a.id, row['blocked_reason'] or '（无说明）'))
    else:
        print('TAKEN 已被 %s 持有（fence=%d，租约剩 %s）—— 退避或换别的任务'
              % (row['owner'], row['fence'], human_lease(row)))


def _mutate(a, new_status, extra_sql='', extra_args=(), action='',
            need_fence=True, clease=None):
    c = conn()
    check_actor(a.as_)
    t = now()
    sets = ['status=?', 'owner=?', 'lease_until=?', 'updated_at=?']
    args = [new_status, a.as_ if new_status == 'claimed' else None,
            clease, t]
    sets += [s for s in extra_sql.split(',') if s.strip()]
    args += list(extra_args)
    where = ' WHERE id=? AND owner=?'
    wargs = [a.id, a.as_]
    if need_fence:
        where += ' AND fence=?'
        wargs.append(a.fence)
        where += ' AND (lease_until IS NULL OR lease_until >= ?)'
        wargs.append(t)
    c.execute('BEGIN IMMEDIATE')
    cur = c.execute('UPDATE tasks SET ' + ','.join(sets) + where, args + wargs)
    n = cur.rowcount
    if n:
        row = c.execute('SELECT * FROM tasks WHERE id=?', (a.id,)).fetchone()
        ev(c, a.id, a.as_, action, row['fence'], getattr(a, 'note', '') or
           getattr(a, 'why', '') or '')
        c.execute('COMMIT')
        print('OK %s -> %s (fence=%d)' % (a.id, new_status, row['fence']))
        return
    c.execute('ROLLBACK')
    row = c.execute('SELECT * FROM tasks WHERE id=?', (a.id,)).fetchone()
    if not row:
        log('[err] 没有这个任务: %s' % a.id)
        sys.exit(2)
    if row['owner'] != a.as_:
        log('[拒绝] %s 现在归 %s，不是你。别覆盖别人的结果。' % (a.id, row['owner'] or '无主'))
    elif need_fence and row['fence'] != a.fence:
        log('[拒绝] fence 不匹配：你拿的是 %s，当前是 %d。'
            '说明你的租约在这期间被别人接管过 —— 你手里的结果是过期的，必须丢弃。'
            % (a.fence, row['fence']))
    else:
        log('[拒绝] 你的租约已过期（租约是"只管一段时间"，不是永久锁）。'
            '先 claim 一次拿新 fence 再干活。')
    sys.exit(3)


def cmd_renew(a):
    c = conn()
    t = now()
    cur = c.execute('UPDATE tasks SET lease_until=?, updated_at=? '
                    'WHERE id=? AND owner=? AND fence=?',
                    (t + a.lease, t, a.id, a.as_, a.fence))
    if cur.rowcount:
        ev(c, a.id, a.as_, 'renew', a.fence, 'lease=%ss' % a.lease)
        print('OK 续租 %ds (fence=%d)' % (a.lease, a.fence))
    else:
        log('[拒绝] 续租失败：owner 或 fence 不匹配（可能已被接管）。立即停止手上的活。')
        sys.exit(3)


def cmd_done(a):
    _mutate(a, 'done', extra_sql='result=?,blocked_reason=?',
            extra_args=(a.note or '', ''), action='done', clease=None)


def cmd_fail(a):
    st = 'blocked' if a.escalate else 'failed'
    _mutate(a, st, extra_sql='blocked_reason=?', extra_args=(a.why or '',),
            action='fail', clease=None)


def cmd_release(a):
    _mutate(a, 'open', action='release', need_fence=False, clease=None)


def cmd_cancel(a):
    """作废：终态，不需要 owner/fence（谁都可以叫停，但要留痕）。"""
    c = conn()
    t = now()
    c.execute('BEGIN IMMEDIATE')
    cur = c.execute("UPDATE tasks SET status='cancelled', owner=NULL, lease_until=NULL, "
                    "updated_at=? WHERE id=? AND status NOT IN ('done','cancelled')",
                    (t, a.id))
    if cur.rowcount:
        ev(c, a.id, a.as_, 'cancel', None, a.why or '')
        c.execute('COMMIT')
        print('OK %s -> cancelled' % a.id)
        return
    c.execute('ROLLBACK')
    row = c.execute('SELECT status FROM tasks WHERE id=?', (a.id,)).fetchone()
    log('[err] 没有这个任务' if not row else '[跳过] 已是 %s' % row['status'])
    sys.exit(2)


def cmd_gc(a):
    """回收过期租约：把"owner 已失联"的任务放回 open，并记一条事件。"""
    c = conn()
    t = now()
    c.execute('BEGIN IMMEDIATE')
    rows = c.execute("SELECT id,owner,fence FROM tasks WHERE status='claimed' "
                     "AND lease_until IS NOT NULL AND lease_until < ?", (t,)).fetchall()
    for r in rows:
        c.execute("UPDATE tasks SET status='open', owner=NULL, lease_until=NULL, updated_at=? "
                  "WHERE id=? AND fence=?", (t, r['id'], r['fence']))
        ev(c, r['id'], '', 'lease_expired', r['fence'], '原 owner=%s' % r['owner'])
    c.execute('COMMIT')
    print('GC 回收 %d 个过期租约' % len(rows))


def cmd_show(a):
    c = conn()
    r = get_task(c, a.id)
    print(json.dumps(dict(r), ensure_ascii=False, indent=2))
    print('--- 事件流水 ---')
    for e in c.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (a.id,)):
        print('%s  %-12s %-14s fence=%-4s %s' % (
            fmt_ts(e['ts']), e['actor'] or '-', e['action'],
            e['fence'] if e['fence'] is not None else '-', e['note'] or ''))


def cmd_stats(a):
    c = conn()
    rows = c.execute('SELECT status, COUNT(*) n FROM tasks GROUP BY status').fetchall()
    d = {r['status']: r['n'] for r in rows}
    tot = sum(d.values())
    print(json.dumps({'total': tot, 'by_status': d, 'ts': now()}, ensure_ascii=False))


# ------------------------------------------------------------------ 入口
def main():
    ap = argparse.ArgumentParser(description='多 agent 共享任务板（status-as-claim + lease + fence）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('add', help='新建任务')
    p.add_argument('title')
    p.add_argument('--detail', default='')
    p.add_argument('--detail-file')
    p.add_argument('--owner', default='', help='直接指派给某人（会立即进入 claimed）')
    p.add_argument('--prio', type=int, default=2, choices=[0, 1, 2, 3])
    p.add_argument('--lease', type=int, default=900)
    p.add_argument('--id')
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser('list', help='列出任务')
    p.add_argument('--all', action='store_true', help='含 done/cancelled')
    p.add_argument('--status')
    p.add_argument('--as', dest='as_', default='')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_list)

    for name, fn, helptext in (('claim', cmd_claim, '抢单（原子，失败=被别人拿了）'),
                               ('renew', cmd_renew, '续租（心跳）')):
        p = sub.add_parser(name, help=helptext)
        p.add_argument('id')
        p.add_argument('--as', dest='as_', required=True)
        p.add_argument('--lease', type=int, default=900)
        if name == 'claim':
            p.add_argument('--fence', type=int)
        else:
            p.add_argument('--fence', type=int, required=True)
        p.set_defaults(fn=fn)

    p = sub.add_parser('done', help='完成（需持有当前 fence）')
    p.add_argument('id')
    p.add_argument('--as', dest='as_', required=True)
    p.add_argument('--fence', type=int, required=True)
    p.add_argument('--note', default='')
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser('fail', help='失败/阻塞')
    p.add_argument('id')
    p.add_argument('--as', dest='as_', required=True)
    p.add_argument('--fence', type=int, required=True)
    p.add_argument('--why', default='')
    p.add_argument('--escalate', action='store_true', help='升级为 blocked（等人）而不是放回队列')
    p.set_defaults(fn=cmd_fail)

    p = sub.add_parser('release', help='放弃（放回 open，不需要 fence）')
    p.add_argument('id')
    p.add_argument('--as', dest='as_', required=True)
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser('cancel', help='作废（终态，不需要 fence）')
    p.add_argument('id')
    p.add_argument('--as', dest='as_', default='')
    p.add_argument('--why', default='')
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser('gc', help='回收过期租约')
    p.set_defaults(fn=cmd_gc)

    p = sub.add_parser('show', help='看单个任务 + 事件流水')
    p.add_argument('id')
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser('stats', help='统计（JSON）')
    p.set_defaults(fn=cmd_stats)

    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
