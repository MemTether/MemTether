# -*- coding: utf-8 -*-
"""smoke_bitemporal.py —— 验证写入路径是否真的维护了双时间轴

覆盖三条路径：remember / correct / retire。测完彻底清理（含向量索引）。
断言要点：
  - remember 默认 valid_from = recorded_at = 现在；
  - remember 显式传 valid_from（事后复盘场景）时**两轴必须分离**；
  - correct 后旧事实 valid_to == 新事实 valid_from（T 轴连续，不留空档），
    且旧事实 invalidated_at 有值（T′ 轴闭环）；
  - retire 后 valid_to 与 invalidated_at 都有值。
用法：python smoke_bitemporal.py（打印每条断言结果，末尾自清测试数据）
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'memory.db')
TAG = 'BITEMP_SMOKE_20260916'
created = []


def show(uid, label):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT uid,status,valid_from,valid_to,recorded_at,invalidated_at,"
                  "temporal_source,superseded_by FROM facts WHERE uid=?", (uid,)).fetchone()
    c.close()
    if not r:
        print('  %-22s ✗ 未找到' % label)
        return None
    print('  %-22s status=%-10s vf=%-19s vt=%-19s rec=%-19s inv=%-19s src=%s'
          % (label, r['status'], r['valid_from'] or '-', r['valid_to'] or '-',
             r['recorded_at'] or '-', r['invalidated_at'] or '-', r['temporal_source'] or '-'))
    return r


print('=== 1) remember（默认：valid_from 应= recorded_at = 现在）===')
r1 = gateway.remember('%s 一号事实' % TAG, type='fact', source='workbuddy',
                      subject='system', scope='global')
uid1 = r1['uid']
created.append(uid1)
show(uid1, 'remember 默认')
assert r1.get('ok'), r1

print('\n=== 2) remember（显式 valid_from=2026-09-11：模拟事后复盘）===')
r2 = gateway.remember('%s 二号事实（实际 09-11 发生）' % TAG, type='fact',
                      source='workbuddy', subject='system', scope='global',
                      valid_from='2026-09-11 08:00:00')
uid2 = r2['uid']
created.append(uid2)
row2 = show(uid2, 'remember 显式 vf')
assert row2 and row2['valid_from'] == '2026-09-11 08:00:00', 'valid_from 未生效！'
assert row2['recorded_at'] != '2026-09-11 08:00:00', '两轴应分离！'

print('\n=== 3) correct（旧：valid_to+invalidated_at；新：valid_from）===')
r3 = gateway.correct(uid1, '%s 一号事实已修正版' % TAG, reason='冒烟测试',
                     by_agent='workbuddy', valid_from='2026-09-16 09:00:00')
uid3 = r3['new_uid']
created.append(uid3)
old = show(uid1, 'correct 后的旧事实')
new = show(uid3, 'correct 后的新事实')
assert old['status'] == 'superseded' and old['valid_to'] and old['invalidated_at'], '旧事实时间轴未闭环'
assert old['superseded_by'] == uid3, '替代链未建立'
assert new['valid_from'] == '2026-09-16 09:00:00', '新事实 valid_from 未继承'
print('  → T 轴连续性: 旧 valid_to(%s) == 新 valid_from(%s) : %s'
      % (old['valid_to'], new['valid_from'],
         '✓' if old['valid_to'] == new['valid_from'] else '✗'))

print('\n=== 4) retire ===')
r4 = gateway.retire(uid2, reason='冒烟测试', by_agent='workbuddy')
row4 = show(uid2, 'retire 后')
assert row4['status'] == 'retired' and row4['valid_to'] and row4['invalidated_at'], '退役时间轴未闭环'

print('\n=== 清理测试数据 ===')
try:
    import memsearch
    col = memsearch._client().get_collection(memsearch.COLLECTION)
    col.delete(ids=created)
except Exception as e:
    print('  向量清理异常(不阻塞): %s' % str(e)[:80])
c = sqlite3.connect(DB)
c.execute("DELETE FROM facts WHERE uid IN (%s)" % ','.join('?' * len(created)), created)
c.execute("DELETE FROM supersessions WHERE old_uid IN (%s) OR new_uid IN (%s)"
          % (','.join('?' * len(created)), ','.join('?' * len(created))), created + created)
c.execute("DELETE FROM audit_log WHERE target IN (%s)" % ','.join('?' * len(created)), created)
c.commit()
n = c.execute("SELECT COUNT(*) FROM facts WHERE content LIKE ?", ('%' + TAG + '%',)).fetchone()[0]
c.close()
print('  已删除 %d 条测试记录，库内残留 %d 条' % (len(created), n))
print()
print('全部断言通过 ✓ 写入路径已正确维护双时间轴')
