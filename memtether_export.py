# -*- coding: utf-8 -*-
"""
memtether_export.py — L2 记忆可移植性（2026-09-24）

把 MemTether 的 memory.db 导出为可移植 JSON，或从 JSON 导入到另一个实例。

导出：.venv/Scripts/python.exe memtether_export.py export --out memtether_snapshot.json
导入：.venv/Scripts/python.exe memtether_export.py import --from snapshot.json [--dry-run]

设计约束：
  - 导出只包含 active + superseded 的 facts 和 tool_assets（不含 retired/quarantined，
    避免垃圾跟着走）
  - supersession 链一并导出（目标端可还原关系）
  - 导入时遇到 uid 冲突：跳过（不覆盖目标端已有数据）
  - schema 版本写在 JSON 里，导入时检查兼容性
"""
import sys, io, os, json, sqlite3, argparse, datetime, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ★2026-09-25：改用单点解析（memtether_paths），不再各写一份默认路径。
#   原实现默认落到本目录 64KB 占位库 -> 导出的是空快照（跑起来不报错、但结果错）。
import memtether_paths as _mp
DB = _mp.default_db()

SCHEMA_VERSION = 1  # 每次不兼容的 schema 变更 +1



# N7 (2026-09-25): 统一安全扫描管线——PII 脱敏从 memtether_pipeline 取
from memtether_pipeline import sanitize_text, sanitize_snapshot



def export_data(out_path, include_retired=False):
    """把 memory.db 导出为 JSON 快照。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    status_filter = "('active','superseded')" if not include_retired else "('active','superseded','retired','quarantined')"

    facts = [dict(r) for r in conn.execute(
        f"SELECT * FROM facts WHERE status IN {status_filter}").fetchall()]
    supersessions = [dict(r) for r in conn.execute(
        "SELECT * FROM supersessions").fetchall()]
    try:
        tool_assets = [dict(r) for r in conn.execute(
            f"SELECT * FROM tool_assets WHERE status IN {status_filter}").fetchall()]
    except Exception:
        tool_assets = []
    conn.close()

    data = {
        'schema_version': SCHEMA_VERSION,
        'exported_at': datetime.datetime.now().isoformat(),
        'db_path': DB,
        'counts': {
            'facts': len(facts),
            'supersessions': len(supersessions),
            'tool_assets': len(tool_assets),
        },
        'facts': facts,
        'supersessions': supersessions,
        'tool_assets': tool_assets,
    }
    # PII 脱敏（在哈希之前做，这样哈希也反映脱敏后的内容）
    data, _pii_n = sanitize_snapshot(data)
    data['pii_redacted'] = _pii_n

    # 内容哈希（接收端可校验完整性）
    _blob = json.dumps([data['facts'], data['supersessions'], data['tool_assets']],
                       ensure_ascii=False, sort_keys=True)
    data['sha256'] = hashlib.sha256(_blob.encode('utf-8')).hexdigest()

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({'ok': True, 'out': out_path, 'counts': data['counts'],
                      'sha256': data['sha256'][:16] + '...', 'pii_redacted': _pii_n}, ensure_ascii=False))
    return data


def import_data(from_path, dry_run=False):
    """从 JSON 快照导入到当前 memory.db（uid 冲突跳过）。"""
    with open(from_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    ver = data.get('schema_version', 0)
    if ver > SCHEMA_VERSION:
        return {'ok': False, 'error': f'快照 schema v{ver} > 当前 v{SCHEMA_VERSION}，请先升级本端'}

    # 完整性校验
    _blob = json.dumps([data.get('facts', []), data.get('supersessions', []),
                        data.get('tool_assets', [])],
                       ensure_ascii=False, sort_keys=True)
    _sha = hashlib.sha256(_blob.encode('utf-8')).hexdigest()
    integrity_ok = _sha == data.get('sha256', '')
    if not integrity_ok:
        print('[WARN] sha256 校验不匹配——文件可能被修改过。继续导入但请知悉。')

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    stats = {'facts_inserted': 0, 'facts_skipped': 0,
             'supersessions_inserted': 0, 'supersessions_skipped': 0,
             'tool_assets_inserted': 0, 'tool_assets_skipped': 0,
             'integrity_ok': integrity_ok}

    for f in data.get('facts', []):
        existing = conn.execute("SELECT uid FROM facts WHERE uid=?", (f['uid'],)).fetchone()
        if existing:
            stats['facts_skipped'] += 1
            continue
        if not dry_run:
            conn.execute(
                "INSERT INTO facts (uid, type, content, source, scope, subject, status,"
                " tags, recorded_at, updated_at, q_value, use_count)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f['uid'], f.get('type'), f.get('content'), f.get('source'),
                 f.get('scope', 'shared'), f.get('subject', 'user'),
                 f.get('status', 'active'), f.get('tags'),
                 f.get('recorded_at'), f.get('updated_at'),
                 f.get('q_value', 0.5), f.get('use_count', 0)))
        stats['facts_inserted'] += 1

    for s in data.get('supersessions', []):
        existing = conn.execute(
            "SELECT old_uid FROM supersessions WHERE old_uid=? AND new_uid=?",
            (s['old_uid'], s['new_uid'])).fetchone()
        if existing:
            stats['supersessions_skipped'] += 1
            continue
        if not dry_run:
            conn.execute(
                "INSERT INTO supersessions (old_uid, new_uid, reason, by_agent, ts)"
                " VALUES (?,?,?,?,?)",
                (s['old_uid'], s['new_uid'], s.get('reason'), s.get('by_agent'), s.get('ts')))
        stats['supersessions_inserted'] += 1

    for a in data.get('tool_assets', []):
        existing = conn.execute("SELECT uid FROM tool_assets WHERE uid=?", (a['uid'],)).fetchone()
        if existing:
            stats['tool_assets_skipped'] += 1
            continue
        if not dry_run:
            cols = [k for k in a.keys() if k != 'id']
            placeholders = ','.join('?' * len(cols))
            col_names = ','.join(cols)
            conn.execute(
                f"INSERT INTO tool_assets ({col_names}) VALUES ({placeholders})",
                [a[c] for c in cols])
        stats['tool_assets_inserted'] += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return {'ok': True, 'dry_run': dry_run, **stats}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MemTether memory portability (L2)')
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('--out', default='memtether_snapshot.json', help='导出文件路径')
    parser.add_argument('--from', dest='src', default='memtether_snapshot.json', help='导入文件路径')
    parser.add_argument('--include-retired', action='store_true', help='导出也包含 retired/quarantined')
    parser.add_argument('--dry-run', action='store_true', help='导入预演（不写库）')
    args = parser.parse_args()

    if args.action == 'export':
        export_data(args.out, include_retired=args.include_retired)
    else:
        r = import_data(args.src, dry_run=args.dry_run)
        print(json.dumps(r, ensure_ascii=False, indent=2))