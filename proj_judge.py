# -*- coding: utf-8 -*-
"""proj_judge.py — MemTether 项目评判执行器（PROJ-Judge）

三层判定：
  L1 硬闸（一票否决）: pytest / make_jury / check_packaging / 泄密扫描
  L2 客观打分:        四维 A30/B30/C25/D15（机器可验证据）
  L3 LLM 评审:        不在本脚本内（文档/体验类主观面另跑，不进总分）

用法:
  python proj_judge.py            # 全量跑，输出 PROJ-JUDGE-REPORT.md
  python proj_judge.py --fast     # 只跑 L1
"""
import os, sys, json, subprocess, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
report = ['# PROJ-JUDGE Report', f'> {datetime.datetime.now():%Y-%m-%d %H:%M:%S}', '']

def run(cmd, timeout=600, cwd=REPO):
    _e = dict(os.environ); _e['PYTHONIOENCODING']='utf-8'
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding='utf-8', errors='replace', env=_e)
        return r.returncode, (r.stdout or ''), (r.stderr or '')[-500:]
    except Exception as e:
        return -1, '', str(e)[:300]

l1 = []
def gate(name, cmd, timeout=600):
    rc, out, err = run(cmd, timeout)
    ok = rc == 0
    l1.append((name, ok, rc, out[-300:] if not ok else ''))
    print(f'[L1] {name}: {"PASS" if ok else "FAIL rc=%d" % rc}')
    return ok

def main():
    """All gates and scoring. Importing this module must be free.

    Before 2026-10-21 the whole script ran at import time, so a plain
    `import proj_judge` executed the full CI (pytest + make_jury +
    check_packaging + ruff, ~5 minutes). That makes the module unusable
    as a library and turns any accidental import into a silent 5-minute
    hang.
    """
    gate('pytest', [PY, '-m', 'pytest', 'tests/', '-q'])
    gate('make_jury', [PY, 'make_jury.py'])
    gate('check_packaging', [PY, 'scripts/check_packaging.py'])
    gate('ruff', [PY, '-m', 'ruff', 'check', '.', '--select', 'E9,F63,F7,F82', '--ignore', 'E501'])

    # leak scan (repo working tree) — must point at the real wordlist, else silent false-green
    _env = dict(os.environ)
    _env['MEM_SCAN_TERMS'] = r'E:\RUANJIAN\memory_hub\scripts\leak_terms.local.json'
    _env['PYTHONIOENCODING'] = 'utf-8'
    import subprocess as _sp
    _r = _sp.run([PY, 'scripts/scan_leaks.py'], cwd=REPO, capture_output=True, timeout=180,
                 env=_env, encoding='utf-8', errors='replace')
    leak_rc = _r.returncode
    leak_out = _r.stdout or ''
    l1.append(('scan_leaks', leak_rc in (0,), leak_rc, leak_out[-200:] if leak_rc else ''))
    print(f'[L1] scan_leaks: {"PASS" if leak_rc==0 else "rc=%d"%leak_rc}')

    hard_fail = [n for n, ok, _, _ in l1 if not ok]
    report.append('## L1 硬闸（一票否决）')
    for name, ok, rc, detail in l1:
        report.append(f'- {"✅" if ok else "❌"} {name} (rc={rc})' + (f'\n  - {detail}' if detail else ''))
    report.append('')
    if hard_fail:
        report.append(f'**L1 FAILED: {hard_fail} — 一票否决，不再计分**')
        out = '\n'.join(report)
        open(os.path.join(REPO, 'PROJ-JUDGE-REPORT.md'), 'w', encoding='utf-8').write(out)
        print(out)
        sys.exit(2)

    # ---- L2 scoring ----
    A = B = C = D = 0
    detail_a, detail_b, detail_c, detail_d = [], [], [], []

    # A 正确性 30: pytest count (use 'collected N items' line)
    import re as _re
    rc, out, _ = run([PY, '-m', 'pytest', 'tests/', '--co', '-q'])
    _m = _re.search(r'collected (\d+) items', out or '')
    ntests = int(_m.group(1)) if _m else 0
    A = 30 if ntests >= 500 else (25 if ntests >= 300 else (20 if ntests >= 100 else 10))
    detail_a.append(f'tests collected = {ntests} → A={A}')

    # B 治理 30: court verify + poison tests
    rc_court, _, _ = run([PY, '-c', 'import sys; sys.path.insert(0,"."); import sqlite3, tempfile, os; '
        'from memory_court import verify_chain; c=sqlite3.connect(":memory:"); '
        'c.execute("CREATE TABLE audit_log(id INTEGER PRIMARY KEY, agent TEXT, op TEXT, target TEXT, detail TEXT, ts TEXT)")'
    ])
    # simpler: count court/poisoning tests
    rc, out, _ = run([PY, '-m', 'pytest', 'tests/test_poisoning_defense.py', 'tests/test_court_e2e_integration.py', '--co', '-q'])
    _m2 = _re.search(r'collected (\d+) items', out or '')
    ncourt = int(_m2.group(1)) if _m2 else 0
    B = min(30, ncourt * 1)
    detail_b.append(f'court+poisoning tests = {ncourt} → B={B}')

    # C 评测诚信 25: bench result files exist + are referenced
    benchmarks = ['_dev/benchmarks/results/locomo_full_results.json',
                  '_dev/benchmarks/results/temporal_accuracy_results.json',
                  '_dev/benchmarks/results/downstream_harm_results.json']
    present = sum(1 for b in benchmarks if os.path.exists(os.path.join(REPO, b)))
    C = present * 8  # 24 max + 1 for README benchmarks section
    rc, out, _ = run([PY, '-c', 'print(open("README.md",encoding="utf-8").read().count("strict"))'])
    C += 1 if 'strict' in (out or '') else 0
    C = min(25, C)
    detail_c.append(f'bench files present = {present}/3 → C={C}')

    # D 工程健康 15: CI workflows + docs
    wf_ok = os.path.exists(os.path.join(REPO, '.github/workflows/ci.yml'))
    sc_ok = os.path.exists(os.path.join(REPO, '.github/workflows/scorecard.yml'))
    docs_ok = all(os.path.exists(os.path.join(REPO, f)) for f in ('README.md','ARCHITECTURE.md','SECURITY.md','INSTALL.md'))
    D = (5 if wf_ok else 0) + (3 if sc_ok else 0) + (5 if docs_ok else 0) + (2 if os.path.exists(os.path.join(REPO,'CHANGELOG.md')) else 0)
    # GitHub Community Standards health (official API, live)
    try:
        import urllib.request as _u, json as _j
        _req = _u.Request('https://api.github.com/repos/MemTether/MemTether/community/profile',
                          headers={'User-Agent':'proj-judge','Accept':'application/vnd.github+json'})
        _d = _j.loads(_u.urlopen(_req, timeout=30).read())
        _hp = int(_d.get('health_percentage', 0))
        D += (5 if _hp >= 90 else 3 if _hp >= 70 else 1 if _hp >= 50 else 0)
        detail_d.append(f'community_health={_hp}%')
    except Exception:
        detail_d.append('community_health=API failed')
    D = min(15, D)
    detail_d.append(f'ci={wf_ok} scorecard={sc_ok} docs={docs_ok} → D={D}')

    total = A + B + C + D
    report.append('## L2 客观打分（0-100）')
    report.append(f'- A 正确性 {A}/30: ' + '; '.join(detail_a))
    report.append(f'- B 治理 {B}/30: ' + '; '.join(detail_b))
    report.append(f'- C 评测诚信 {C}/25: ' + '; '.join(detail_c))
    report.append(f'- D 工程健康 {D}/15: ' + '; '.join(detail_d))
    report.append(f'')
    report.append(f'### TOTAL: {total}/100')
    report.append('')
    report.append('## 门槛')
    report.append(f'- Release 门槛（L1 全过 + ≥75）: {"✅ 达标" if total >= 75 else "❌ 未达标"}')
    report.append(f'- 一等奖叙事门槛（≥85 + 外部用户）: {"✅ 分数达标" if total >= 85 else "❌ 未达标"}（外部用户另计）')

    out = '\n'.join(report)
    open(os.path.join(REPO, 'PROJ-JUDGE-REPORT.md'), 'w', encoding='utf-8').write(out)
    print('\n' + out)

if __name__ == '__main__':
    main()
