# -*- coding: utf-8 -*-
"""stress_board.py — P2-3 board.py lease 压测（2026-09-27）

四场景（对位 concurrent_stress v2 的 S1-S4 风格）：
  S1  并发抢单原子性   8 进程同时 claim 同一任务 → 恰好 1 个赢
  S2  fencing token    旧租约持有者 done 用旧 fence → 必须被拒
  S3  租约过期 + GC    lease 过期后 gc 回收 → 回到 open，别人可抢
  S4  并发 done 竞争   错误 owner / 错误 fence 的 done → 全部拒绝

口径：CLI 子进程真实路径（不走函数直调），每场景独立任务，跑完统计 PASS/FAIL。
用法：python stress_board.py
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BOARD = os.path.join(HERE, 'board.py')
PROCS = ['codex', 'workbuddy', 'workbuddy_ai', 'openclaw',
         'openclaw_astra', 'doubao_a', 'doubao_b', 'claude_code']


def cli(args, timeout=30):
    r = subprocess.run([PY, BOARD] + args, capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=os.environ.get('BOARD_TIMEOUT', timeout))
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def add_task(title, lease=900):
    tid = 'stress-%s' % str(time.time_ns())[-9:]
    rc, out = cli(['add', title, '--id', tid, '--lease', str(lease)])
    assert rc == 0, 'add failed: %s' % out
    return tid


def claim(tid, actor, lease=900):
    rc, out = cli(['claim', tid, '--as', actor, '--lease', str(lease)])
    m = re.search(r'CLAIMED fence=(\d+)', out)
    return rc, out, (int(m.group(1)) if m else None)


def scenario_s1():
    """8 进程并发 claim → 恰好 1 个 CLAIMED"""
    tid = add_task('S1 并发抢单原子性 stress_board')
    procs = PROCS[:8]
    # 用 Barrier 式 true-parallel：一个 python -c 起多进程同时 claim
    code = (
        "import concurrent.futures as cf, subprocess, sys\n"
        "args = [%r] + ['claim', %r, '--as', '__ACTOR__', '--lease', '900']\n"
        "def go(a):\n"
        "    r = subprocess.run([sys.executable] + [x.replace('__ACTOR__', a) for x in args],\n"
        "                       capture_output=True, text=True)\n"
        "    return a, r.returncode, (r.stdout or '') + (r.stderr or '')\n"
        "with cf.ThreadPoolExecutor(8) as ex:\n"
        "    for a, rc, out in ex.map(go, %r):\n"
        "        print(a, rc, 'CLAIMED' if 'CLAIMED' in out else 'REJECTED')\n"
    ) % (BOARD, tid, procs)
    r = subprocess.run([PY, '-c', code], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    wins = sum(1 for l in lines if ' CLAIMED' in l)
    ok = (len(lines) == 8 and wins == 1)
    return ok, ('S1 wins=%d/8 (%s)' % (wins, 'PASS' if ok else 'FAIL')), lines


def scenario_s2():
    """旧 fence 的 done 必须被拒（fencing token 生效）"""
    tid = add_task('S2 fencing token stress_board', lease=1)
    rc, out, f1 = claim(tid, 'codex', lease=2)
    assert f1 is not None, 'claim failed: %s' % out
    # 模拟租约被抢：等过期 → 别人 claim 拿新 fence
    time.sleep(3.0)
    rc2, out2, f2 = claim(tid, 'workbuddy', lease=30)
    assert f2 is not None and f2 != f1, 'second claim failed: %s' % out2
    # 旧持有者用旧 fence done → 必须失败
    rc3, out3 = cli(['done', tid, '--as', 'codex', '--fence', str(f1),
                     '--note', 'stale fence attempt'])
    ok = rc3 != 0 and ('fence' in out3 or '过期' in out3 or '拒绝' in out3)
    # 新持有者用新 fence done → 必须成功
    rc4, out4 = cli(['done', tid, '--as', 'workbuddy', '--fence', str(f2),
                     '--note', 'correct fence'])
    ok = ok and rc4 == 0
    return ok, 'S2 stale_fence_rejected=%s new_fence_done=%s (%s)' % (
        rc3 != 0, rc4 == 0, 'PASS' if ok else 'FAIL')


def scenario_s3():
    """租约过期 → gc 回收 → 回到 open"""
    tid = add_task('S3 租约过期 GC stress_board', lease=1)
    rc, out, f = claim(tid, 'codex', lease=1)
    assert f is not None
    time.sleep(1.3)
    rc_gc, out_gc = cli(['gc'])
    rc_st, out_st = cli(['show', tid])
    status = json.loads(out_st.split('---')[0])['status'] if out_st else '?'
    ok = (status == 'open') and ('GC 回收' in out_gc)
    return ok, 'S3 status_after_gc=%s gc_msg=%s (%s)' % (
        status, 'yes' if 'GC 回收' in out_gc else 'no', 'PASS' if ok else 'FAIL')


def scenario_s4():
    """错误 owner 的 done 必须被拒（即使 fence 对）"""
    tid = add_task('S4 错误 owner 竞争 stress_board')
    rc, out, f = claim(tid, 'codex')
    assert f is not None
    # workbuddy 冒充 codex 的 fence → 拒
    rc_w, out_w = cli(['done', tid, '--as', 'workbuddy', '--fence', str(f)])
    # codex 自己 release 后 → open，任何人可抢
    rc_r, out_r = cli(['release', tid, '--as', 'codex'])
    rc_c, out_c, f2 = claim(tid, 'openclaw')
    ok = (rc_w != 0 and rc_r == 0 and f2 is not None)
    return ok, 'S4 wrong_owner_rejected=%s release=%s reclaim=%s (%s)' % (
        rc_w != 0, rc_r == 0, f2 is not None, 'PASS' if ok else 'FAIL')


def main():
    print('=== stress_board: P2-3 board.py lease 压测 ===')
    results = []
    for name, fn in (('S1', scenario_s1), ('S2', scenario_s2),
                     ('S3', scenario_s3), ('S4', scenario_s4)):
        t0 = time.time()
        try:
            ok, msg, *extra = fn()
        except Exception as e:
            ok, msg = False, '%s EXCEPTION %r' % (name, e)
        results.append((name, ok, msg, time.time() - t0))
        print('%s  %s  [%.1fs]' % ('PASS' if ok else 'FAIL', msg, time.time() - t0))
        if not ok and extra and extra[0]:
            for l in extra[0][:8]:
                print('   ', l)
    n_pass = sum(1 for _, ok, _, _ in results if ok)
    print('=== stress_board: %d/%d PASS ===' % (n_pass, len(results)))
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == '__main__':
    main()



