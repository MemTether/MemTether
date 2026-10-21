#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_jury_report.py — one-command verification for reviewers (P2-9).

Runs all checks and produces VERIFICATION_REPORT.txt so judges can verify
every claim without installing anything. Usage:
    python scripts/make_jury_report.py
"""
import subprocess, sys, os, datetime, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'VERIFICATION_REPORT.txt')

def run(label, cmd, cwd=ROOT, timeout=600):
    """Run one check.

    timeout defaults to 600s: the suite is ~650 tests and takes ~5 min, so the
    previous 120s cap made the headline check report a bogus timeout.
    stdout/stderr are guarded because capture_output can hand back None on
    some paths, and None + str crashed this reporter itself -- on 2026-10-21
    two checks reported FAIL that were actually reporter bugs.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, encoding='utf-8', errors='replace',
                          env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        ok = r.returncode == 0
        detail = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
        # 优先抓 pytest 的汇总行（"N passed, M skipped"），最后一行常是 warning
        summary = ''
        for ln in reversed(detail):
            if 'passed' in ln or 'failed' in ln or 'error' in ln.lower():
                summary = ln.strip()[:120]
                break
        if not summary:
            summary = detail[-1][:120] if detail else ''
        return label, ok, summary
    except subprocess.TimeoutExpired:
        return label, False, 'timed out after %ds' % timeout
    except Exception as e:
        return label, False, '%s: %s' % (type(e).__name__, str(e)[:100])

checks = [
    # 不要在标签里写死测试数：它会漂移（曾写 135，实际 642），而评委
    # 会拿这个数字当项目规模。改由 pytest 自己报数。
    ('Test suite', [sys.executable, '-m', 'pytest', 'tests/', '-q', '--tb=line']),
    ('Packaging checker', [sys.executable, 'scripts/check_packaging.py']),
    ('PII roundtrip', [sys.executable, 'scripts/test_pii_roundtrip.py']),
    # 标签必须诚实：selftest 校验全部 23 个适配器的元数据，但只对
    # workbuddy-cn / zcode 两个做深度行为测试（写盘/幂等/回读/坏JSON）。
    # 原来写 '(23 clients)' 会让人以为 23 个都深度测过。
    ('Adapter selftest (23 metadata + 2 deep)', [sys.executable, 'tether_connect.py', 'selftest']),
]

lines = [
    'MemTether Verification Report',
    f'Generated: {datetime.datetime.now().isoformat()}',
    '=' * 60,
    '',
]

def main():
    """Run every check and write VERIFICATION_REPORT.txt.

    Importing this module must be free. Before 2026-10-21 the whole
    body ran at import time, so `import scripts.make_jury_report`
    executed the full suite (~4 min) - the same defect proj_judge.py
    had. Wrapped into main() for both.
    """
    all_ok = True
    for label, cmd in checks:
        name, ok, summary = run(label, cmd)
        status = 'PASS' if ok else 'FAIL'
        if not ok:
            all_ok = False
        lines.append(f'[{status}] {label}')
        lines.append(f'       {summary}')
        lines.append('')

    # EAF artifact check
    eaf = os.path.join(ROOT, 'releases', 'eaf_artifact_bundle', 'eaf_500q_checkpoint.json')
    if os.path.exists(eaf):
        data = json.load(open(eaf, encoding='utf-8'))
        results = data.get('results', [])
        ok_count = sum(1 for r in results if r.get('strict') == 1)
        total = sum(1 for r in results if r.get('strict') in (0, 1))
        lines.append(f'[PASS] EAF 500Q artifact: {ok_count}/{total} = {100*ok_count/total:.1f}% strict')
    else:
        lines.append('[INFO] EAF artifact: available on GitHub Release v0.1.0a43')

    lines.append('')
    lines.append('=' * 60)
    lines.append(f'Overall: {"ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"}')
    lines.append(f'Repro commands: see docs/site/en/reproduce.html')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'Report: {OUT}')
    print(f'Overall: {"ALL PASSED" if all_ok else "SOME FAILED"}')
    sys.exit(0 if all_ok else 1)

if __name__ == '__main__':
    main()
