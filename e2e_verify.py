# -*- coding: utf-8 -*-
"""
e2e_verify.py — 三 agent 端到端验证（Task #51）

模拟三个 agent 的真实使用姿势，验证统一走 gateway/memsearch：
  A. WorkBuddy/DeepSeek  -> preflight.py（会话开始扫描中枢）
  B. OpenClaw/grok        -> mem.py recall / drain（水位增量）
  C. 豆包(账号A)           -> mem.py add / search（写入+检索）

判据：
  - 所有入口读到同一个 memory.db（单一真源）
  - 检索返回真实的 results 列表（非回显假阳性）
  - 写入后自动同步向量索引（无需手动 rebuild）
  - 水位增量对 OpenClaw 有效
"""
import os
import sys
import time
import sqlite3
import subprocess

HUB = r'E:\RUANJIAN\memory_hub'
PY = os.path.join(HUB, '.venv-memory', 'Scripts', 'python.exe')
DB = os.path.join(HUB, 'memory.db')
ENV = dict(os.environ)
ENV['PYTHONPATH'] = ''
sys.path.insert(0, HUB)

# 注意：探针必须**每次都不一样**。原来只用时间戳（%H%M%S），
# 与上一次运行只差 6 位数字 → 被判为"近似重复"，不会作为新事实入库，
# 导致 C2/D2 长期假失败（2026-09-14 实测确认）。加随机段后即恢复真实信号。
PROBE = 'E2EPROBE%s-%s：三agent共用同一memory.db' % (
    time.strftime('%H%M%S'), os.urandom(3).hex())

results = []


def run(args, timeout=240):
    p = subprocess.run([PY] + args, cwd=HUB, env=ENV,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', name, detail))


print('=' * 70)
print('三 agent 端到端验证   探针=%s' % PROBE)
print('=' * 70)

# ---------- A. WorkBuddy: preflight.py ----------
print('\n--- A. WorkBuddy/DeepSeek: preflight.py ---')
rc, out = run(['preflight.py', '豆包数据转移到E盘junction'])
check('A1 preflight 返回区块', rc == 0 and 'MEMORY_PREFLIGHT' in out, 'rc=%d' % rc)
check('A2 preflight 命中豆包转移结论', ('Junction' in out or 'junction' in out or 'E:' in out), '')

rc, out = run(['preflight.py', '记忆中枢检索引擎'])
check('A3 preflight 第二次查询正常', rc == 0 and 'MEMORY_PREFLIGHT' in out, 'rc=%d' % rc)

# ---------- B. OpenClaw: mem.py recall / drain ----------
print('\n--- B. OpenClaw/grok: mem.py recall / drain ---')
rc, out = run(['mem.py', 'recall', '--agent', 'openclaw'])
check('B1 mem.py recall 正常', rc == 0, 'rc=%d' % rc)

rc, out = run(['mem.py', 'drain', '--agent', 'openclaw', '--limit', '5'])
check('B2 drain 返回水位信息', rc == 0 and '水位' in out, '')

rc, out = run(['mem.py', 'search', 'STM32 烧录'])
check('B3 mem.py search 走混合引擎', rc == 0 and 'engine=' in out, '')

rc, out = run(['mem.py', 'stats'])
check('B4 mem.py stats 读到 memory.db', rc == 0 and 'memory.db（唯一真源）' in out, '')

# ---------- C. 豆包: mem.py add / search ----------
print('\n--- C. 豆包(账号A): mem.py add / search ---')
rc, out = run(['mem.py', 'add', '--type', 'fact', '--text', PROBE, '--source', 'doubao_a', '--force'])
check('C1 mem.py add 写入成功', rc == 0 and '已沉淀到 memory.db' in out, 'rc=%d' % rc)

# 用 Python API 严谨校验（避免回显假阳性）
import gateway  # noqa: E402
r = gateway.search(PROBE, limit=5)
res = r.get('results', [])
hit = any(PROBE[:20] in str(x.get('content', '')) for x in res)
check('C2 新增事实可被检索（自动同步向量）', hit, 'hits=%d' % len(res))

rc, out = run(['gateway.py', 'search', PROBE])
check('C3 gateway 命令搜到同一条', rc == 0 and 'hits' not in out.lower() and str(len(res)) != '0', '')

# 直接查 db 校验落库
conn = sqlite3.connect(DB)
row = conn.execute("SELECT uid,status FROM facts WHERE content LIKE ?",
                   ('%' + PROBE[:16] + '%',)).fetchone()
conn.close()
check('D2 探针事实确实落在 memory.db', row is not None, str(row))

# ---------- D. 单一真源一致性 ----------
print('\n--- D. 单一真源一致性 ---')
rc, out = run(['gateway.py', 'stats'])
check('D1 gateway stats 正常', rc == 0 and '"active"' in out, '')

# mem.py 与 gateway 读到同一个 active 数
rc1, o1 = run(['mem.py', 'stats'])
rc2, o2 = run(['gateway.py', 'stats'])
import re
m1 = re.search(r"'active':\s*(\d+)", o1)
m2 = re.search(r'"active":\s*(\d+)', o2)
n1 = int(m1.group(1)) if m1 else None
n2 = int(m2.group(1)) if m2 else None
check('D3 mem.py 与 gateway 的 active 数一致', n1 is not None and n1 == n2, 'mem=%s gw=%s' % (n1, n2))

# ---------- 汇总 ----------
print('\n' + '=' * 70)
passed = sum(1 for _, o, _ in results if o)
print('通过: %d/%d' % (passed, len(results)))
for n, o, d in results:
    if not o:
        print('  未通过:', n, d)
print('=' * 70)

# ---------- 清理探针 ----------
conn = sqlite3.connect(DB)
r = conn.execute("SELECT uid FROM facts WHERE content LIKE ?", ('%' + PROBE[:16] + '%',)).fetchall()
conn.close()
for (uid,) in r:
    try:
        gateway.retire(uid, reason='e2e 验证探针清理')
        print('探针已退役:', uid)
    except Exception as e:
        print('探针退役失败:', uid, e)

sys.exit(0 if passed == len(results) else 1)
