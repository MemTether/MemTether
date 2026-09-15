# -*- coding: utf-8 -*-
"""
memory_maintenance.py — 记忆中枢每日自动维护（v4，2026-09-13）

已注册为 Windows 计划任务「MemoryHubMaintenance」（每日 03:00，绝对路径调用）。
另有一个「MemoryHubReflector」（每 5 分钟）负责消费 run_events 事件队列，
本脚本不重复处理事件，只做每日重活：

每日执行：
  1. 备份 SQLite memory.db 到 backup/
  2. 检查工具路径是否存在（tool_assets.path）
  3. 发现高度相似的事实（查重兜底；写入时的近重复检测已由 gateway.remember 负责）
  4. 从 SQLite 重建所有投影（sink.json + md + ~/.workbuddy/MEMORY.md）
  5. 输出健康报告 health_report.json

权威真源 = memory.db（v4 起，不再是 sink.json）。

用法：python memory_maintenance.py
"""
import os
import sys
import json
import time
import shutil
import sqlite3

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
import gateway

HUB = r'E:\RUANJIAN\memory_hub'
DB = os.path.join(HUB, 'memory.db')
BACKUP_DIR = os.path.join(HUB, 'backup')


def backup_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, 'memory.db.%s' % time.strftime('%Y%m%d'))
    shutil.copy2(DB, dst)
    return dst


def check_tools():
    """检查工具路径是否存在。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tool_assets WHERE status='active'").fetchall()
    conn.close()
    missing = []
    ok = []
    for r in rows:
        p = r['path'] or ''
        if p and os.path.exists(p):
            ok.append(r['name'])
        elif p:
            missing.append({'name': r['name'], 'path': p})
    return {'ok': ok, 'missing': missing}


def find_duplicates():
    """发现重复事实（内容高度相似）。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT uid, content FROM facts WHERE status='active'").fetchall()
    conn.close()
    seen = {}
    dups = []
    for r in rows:
        key = r['content'][:40]
        if key in seen:
            dups.append({'a': seen[key], 'b': r['uid'], 'content': key})
        else:
            seen[key] = r['uid']
    return dups


def main():
    report = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'backup': None,
        'tools': {},
        'duplicates': [],
        'rebuild': None,
    }
    try:
        report['backup'] = backup_db()
    except Exception as e:
        report['backup_error'] = str(e)[:100]

    try:
        report['tools'] = check_tools()
    except Exception as e:
        report['tools_error'] = str(e)[:100]

    try:
        report['duplicates'] = find_duplicates()
    except Exception as e:
        report['dup_error'] = str(e)[:100]

    try:
        report['rebuild'] = gateway.rebuild()
    except Exception as e:
        report['rebuild_error'] = str(e)[:100]

    out = os.path.join(HUB, 'health_report.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print('=== 记忆中枢维护完成 ===')
    print('备份:', report.get('backup'))
    print('工具检查: 正常 %d 个, 缺失 %d 个' % (
        len(report.get('tools', {}).get('ok', [])),
        len(report.get('tools', {}).get('missing', []))))
    for m in report.get('tools', {}).get('missing', []):
        print('  缺失: %s -> %s' % (m['name'], m['path']))
    print('重复事实: %d 条' % len(report.get('duplicates', [])))
    print('报告:', out)


if __name__ == '__main__':
    main()
