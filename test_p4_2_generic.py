# -*- coding: utf-8 -*-
"""test_p4_2_generic.py — P4-2 泛化冲突检测验证"""
import os, sys, sqlite3, tempfile, json, time, importlib.util

tmpdir = tempfile.mkdtemp(prefix="p42gen-")
tmpdb = os.path.join(tmpdir, "memory.db")
conn = sqlite3.connect(tmpdb)
conn.executescript("""
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
    type TEXT DEFAULT 'fact', subject TEXT DEFAULT 'user',
    content TEXT NOT NULL, status TEXT DEFAULT 'active',
    superseded_by TEXT, valid_from TEXT, valid_to TEXT,
    source TEXT DEFAULT 'unknown', scope TEXT DEFAULT 'shared',
    confidence REAL DEFAULT 0.8, tags TEXT,
    created_at TEXT, updated_at TEXT, recorded_at TEXT, invalidated_at TEXT,
    temporal_source TEXT, q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0
);
""")
conn.commit()
now = time.strftime('%Y-%m-%d %H:%M')
# 通用层测试：硬编码正则不覆盖的实体
test_data = [
    ("Redis服务端口是6379，连接正常", "codex"),          # pos
    ("Redis服务端口是6380，连接失败不可用", "workbuddy"), # neg
    ("ComfyUI已成功部署在E盘", "codex"),                   # pos
    ("ComfyUI部署失败，不知道为什么挂了", "workbuddy"),     # neg
    ("普通日志：今天是晴天，出去跑步", "codex"),           # 无极性
]
for i, (content, source) in enumerate(test_data):
    conn.execute(
        "INSERT INTO facts(uid, content, source, status, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("gen-%03d" % i, content, source, 'active', now, now))
conn.commit()
conn.close()

spec = importlib.util.spec_from_file_location("governance", r"E:\RUANJIAN\memtether\governance.py")
gov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gov)
gov.DB = tmpdb

results = gov.detect_explicit_conflicts(days_window=90)
print(json.dumps({"total": len(results)}, ensure_ascii=False))
for r in results:
    print("  entity=%s detection=%s keep=%s retire=%s newer=%s" % (
        r.get('entity','')[:30], r.get('detection','specific'),
        r.get('keep','')[:30], r.get('suggest_retire','')[:30], r.get('newer','')))

entities = {r['entity'] for r in results}
# Redis 相关和 ComfyUI 相关应该被检出
found = {'redis' in e or 'comfyui' in e or 'comfy' in e for e in entities}
ok = any(found) and len(results) >= 2
print("PASS: generic detection found %d conflicts" % len(results) if ok else "FAIL")
for r in results:
    print("  %s: %s -> %s" % (r['entity'][:25], r['suggest_retire'], r['keep']))
