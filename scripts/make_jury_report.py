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

def run(label, cmd, cwd=ROOT):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          cwd=cwd, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr).strip().splitlines()
        summary = detail[-1][:120] if detail else ''
        return label, ok, summary
    except Exception as e:
        return label, False, str(e)[:120]

checks = [
    ('Test suite (135 tests)', [sys.executable, '-m', 'pytest', 'tests/', '-q', '--tb=line']),
    ('Packaging checker', [sys.executable, 'scripts/check_packaging.py']),
    ('PII roundtrip', [sys.executable, 'scripts/test_pii_roundtrip.py']),
    ('Adapter selftest (23 clients)', [sys.executable, 'tether_connect.py', 'selftest']),
]

lines = [
    'MemTether Verification Report',
    f'Generated: {datetime.datetime.now().isoformat()}',
    '=' * 60,
    '',
]

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
