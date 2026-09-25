# -*- coding: utf-8 -*-
"""
test_pii_roundtrip.py — R3 (2026-09-25)
验证：导出快照 → PII 脱敏 → 模拟导入 → 数据库中 0 PII 残留。
用法: python test_pii_roundtrip.py  （exit 0 = PASS）
"""
import sys, os, json, sqlite3, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from memtether_export import sanitize_snapshot
from memtether_pipeline import PII_PATTERNS

def has_pii(text):
    if not isinstance(text, str):
        return False
    return any(p.search(text) for p, _ in PII_PATTERNS)

sample = {
    'schema_version': 1,
    'facts': [
        {'uid': 'f1', 'type': 'fact', 'content': '我的手机号是 13812345678，邮箱 test@example.com',
         'source': 'test', 'tags': '', 'recorded_at': '2026-01-01 00:00:00'},
        {'uid': 'f2', 'type': 'fact', 'content': 'API key: %s%s%s' % ('sk', '-TESTONLY', 'abcdefghijklmnopqrstuvwxyz123456'),
         'source': 'test', 'tags': '', 'recorded_at': '2026-01-01 00:00:00'},
        {'uid': 'f3', 'type': 'fact', 'content': '正常内容，不含敏感信息',
         'source': 'test', 'tags': '', 'recorded_at': '2026-01-01 00:00:00'},
    ],
    'supersessions': [{'old_uid': 'f0', 'new_uid': 'f1', 'reason': 'updated phone 13998765432', 'by_agent': 'codex'}],
    'tool_assets': [],
    'notes': '',
    'exported_at': '2026-01-01T00:00:00',
    'pii_redacted': 0,
    'sha256': '',
}

clean, n_changes = sanitize_snapshot(sample)
print('sanitize changes:', n_changes)

pii_after = sum(1 for s in clean.get('facts', []) if has_pii(s.get('content', '')))
pii_after += sum(1 for s in clean.get('supersessions', []) if has_pii(s.get('reason', '')))
print('PII remaining after sanitize:', pii_after)

blob = json.dumps(clean, ensure_ascii=False)
rt = json.loads(blob)
pii_rt = sum(1 for s in rt.get('facts', []) if has_pii(s.get('content', '')))
pii_rt += sum(1 for s in rt.get('supersessions', []) if has_pii(s.get('reason', '')))
print('PII after round-trip:', pii_rt)

tmp = tempfile.mktemp(suffix='.db')
conn = sqlite3.connect(tmp)
conn.execute('CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, source TEXT)')
for f in clean.get('facts', []):
    conn.execute('INSERT INTO facts VALUES (?,?,?)', (f['uid'], f.get('content'), f.get('source')))
conn.commit()

pii_db = 0
for row in conn.execute('SELECT content FROM facts'):
    if has_pii(row[0]):
        pii_db += 1
conn.close()
os.unlink(tmp)
print('PII in imported db:', pii_db)

ok = (pii_after == 0 and pii_rt == 0 and pii_db == 0)
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
