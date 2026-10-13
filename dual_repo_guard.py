#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dual_repo_guard.py — 双仓同步机械闸（2026-10-13, audit §3）

比对本仓同名核心文件的 def/class/SCHEMA 表集合。差异非白名单即 exit 1。
把「改代码必须双仓同打」从纪律变成可执行判据。

用法：
  python dual_repo_guard.py            # 比对，差异则 exit 1
  python dual_repo_guard.py --report   # 只打印报告，不设退出码
"""
import ast
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
import io
import os
import sys

HUB = r'E:\RUANJIAN\memory_hub'
MT = r'E:\RUANJIAN\memtether'

# SCHEMA 在 gateway.py 里是字符串常量，用正则抓 CREATE TABLE
import re

def _defs(path):
    with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, str(e)
    fns, classes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            fns.add(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            fns.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
    return (fns, classes), None

def _tables(path):
    with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
        src = f.read()
    return set(re.findall(r'CREATE TABLE IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)', src))

WHITELIST = {
    # hub-only / mt-only by design (v30 handover §3 + 2026-10-13 audit §3)
    ('gateway', 'fn', 'hub'): {'_maybe_nrem_trigger', '_index_consistency_gate',
                               '_hg_fact_line', '_count_tagged', '_load_governance',
                               '_detect_interference', '_bg_rebuild', '_cos_sim',
                               '_count_active_facts', '_sensitive_check'},
    ('gateway', 'fn', 'mt'): {'_hg_write', '_init_fts_triggers', '_proj_targets',
                              '_tenant_filter'},
    ('memsearch', 'fn', 'hub'): set(),
    ('memsearch', 'fn', 'mt'): {'_chroma_path', '_scope_filter', '_tenant_filter',
                                'extract_answer_hints'},
    ('governance', 'fn', 'hub'): set(),
    ('governance', 'fn', 'mt'): {'_topic_tokens'},
    ('mcp_server', 'fn', 'hub'): {'_reset_idle_timer'},
    ('mcp_server', 'fn', 'mt'): {'_load_signatures', '_match_signature',
                                 '_proc_cmdline', '_proc_image_path', '_read_mem'},
}

def main():
    report_only = '--report' in sys.argv
    problems = []
    for name in ('gateway.py', 'memsearch.py', 'governance.py', 'mcp_server.py'):
        hub_p = os.path.join(HUB, name)
        mt_p = os.path.join(MT, name)
        hub_r, hub_e = _defs(hub_p)
        mt_r, mt_e = _defs(mt_p)
        if hub_e or mt_e:
            problems.append('%s: syntax error hub=%s mt=%s' % (name, hub_e, mt_e))
            continue
        hub_fns, hub_cls = hub_r
        mt_fns, mt_cls = mt_r
        stem = name.replace('.py', '')
        wl_hub = WHITELIST.get((stem, 'fn', 'hub'), set())
        wl_mt = WHITELIST.get((stem, 'fn', 'mt'), set())
        only_hub = (hub_fns - mt_fns) - wl_hub
        only_mt = (mt_fns - hub_fns) - wl_mt
        if only_hub:
            problems.append('%s: fns only in HUB (not whitelisted): %s'
                            % (name, sorted(only_hub)))
        if only_mt:
            problems.append('%s: fns only in MT (not whitelisted): %s'
                            % (name, sorted(only_mt)))
        # SCHEMA tables (gateway only)
        if stem == 'gateway':
            th = _tables(hub_p)
            tm = _tables(mt_p)
            if th != tm:
                problems.append('gateway SCHEMA tables differ: hub-only=%s mt-only=%s'
                                % (sorted(th - tm), sorted(tm - th)))
    print('=' * 72)
    print('dual_repo_guard 比对结果')
    print('=' * 72)
    if problems:
        for p in problems:
            print('  ✘ %s' % p)
        print()
        print('共 %d 项差异。' % len(problems))
        if not report_only:
            sys.exit(1)
    else:
        print('  ✔ 两仓核心文件 def/class/SCHEMA 集合一致（或差异均在白名单内）')
        sys.exit(0)

if __name__ == '__main__':
    main()
