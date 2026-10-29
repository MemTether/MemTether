# -*- coding: utf-8 -*-
"""tests/test_docs_claims.py -- lock documentation numbers to measured reality.

Inspired by neotoma's `tests/unit/llms_txt_claims.test.ts` (found during the
2026-10-20 field survey): they use text guards so a README claim that the build
contradicts fails CI instead of shipping.

Why this exists: on 2026-10-20 a full sweep found the docs claimed "9 tools /
12 endpoints" while the code had 10 tools / 14 endpoints, the endpoint list
named 11 routes while claiming 12 (missing both /court routes), and the PyPI
landing image was a repo-relative path that 404s there. Every one of those was
found by hand. This file makes them mechanical.

What it locks (all values measured from the code, never hand-copied):
  1. MCP tool count and the exact tool list, in mcp-server.json
  2. REST endpoint count and the full endpoint list, in README + API.md
  3. CLI subcommand coverage in the README's Memory Operations block
  4. PyPI renders repo-relative images as broken -> the demo image must carry
     an absolute-URL fallback
  5. server.json (MCP Registry source) version must match pyproject
"""
import io
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# FastAPI's own docs routes are not product endpoints
_FASTAPI_AUTO = {'/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc'}


def _read(rel):
    path = os.path.join(REPO, rel)
    if not os.path.exists(path):
        pytest.skip(f'{rel} not present (gitignored local file)')
    return io.open(path, encoding='utf-8').read()


def _probe(code):
    """Run a snippet in a fresh subprocess, return its stdout.

    Deliberately NOT an in-process import (2026-10-20): importing mcp_server
    or api_server here mutates global state for every test that runs after
    this file - mcp_server reassigns sys.stdout at import time and starts a
    warmup thread, which made tests/test_poisoning_defense.py fail when the
    whole suite ran together while passing alone. A subprocess keeps the
    measurement honest and the side effects contained.
    """
    import subprocess
    # stderr must be merged into stdout: mcp_server reassigns sys.stdout to
    # sys.stderr at import time, so with separate pipes the payload lands in
    # the stderr pipe and stdout comes back empty.
    r = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, 'probe failed: %s' % (r.stdout or '')[-400:]
    return r.stdout


def _real_tools():
    code = "import mcp_server, json; print(json.dumps([t['name'] for t in mcp_server.TOOLS]))"
    out = _probe(code)
    # the child may print diagnostics to stdout before the payload; take the
    # last line that parses as JSON
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith('['):
            return json.loads(line)
    raise AssertionError('no JSON list in probe output: %r' % out[-300:])


def _real_endpoints():
    auto = "{'/openapi.json','/docs','/docs/oauth2-redirect','/redoc'}"
    code = ("import api_server;"
            "AUTO=" + auto + ";"
            "print(sorted(r.path for r in api_server.app.routes "
            "if hasattr(r,'methods') and r.path not in AUTO))")
    out = _probe(code)
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith('['):
            return sorted(eval(line))
    raise AssertionError('no JSON list in probe output: %r' % out[-300:])


def _real_cli_subcommands():
    import subprocess
    r = subprocess.run([sys.executable, 'memtether.py', '--help'],
                       cwd=REPO, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    m = re.search(r'\{([^}]+)\}', r.stdout or '')
    assert m, 'could not parse memtether --help'
    return sorted(x.strip() for x in m.group(1).split(','))



class TestMcpServerJson:
    def test_tool_list_matches_code_exactly(self):
        declared = [t['name'] for t in json.loads(_read('mcp-server.json'))['tools']]
        assert declared == _real_tools(), (
            'mcp-server.json tool list drifted from mcp_server.TOOLS\n'
            '  declared: %s\n  real    : %s' % (declared, _real_tools()))

    def test_version_matches_pyproject(self):
        declared = json.loads(_read('mcp-server.json'))['version']
        real = re.search(r'version\s*=\s*"([^"]+)"', _read('pyproject.toml')).group(1)
        assert declared == real, 'mcp-server.json %s != pyproject %s' % (declared, real)


class TestServerJson:
    def test_version_matches_pyproject(self):
        d = json.loads(_read('server.json'))
        real = re.search(r'version\s*=\s*"([^"]+)"', _read('pyproject.toml')).group(1)
        assert d['version'] == real, 'server.json version %s != %s' % (d['version'], real)

    def test_package_version_matches_top_level(self):
        d = json.loads(_read('server.json'))
        pkg = d['packages'][0]['version']
        assert pkg == d['version'], (
            'server.json packages[0].version=%s but top-level=%s '
            '(this exact mismatch shipped 9 releases behind on 2026-10-20)'
            % (pkg, d['version']))


class TestReadmeNumbers:
    def test_tool_count_claim(self):
        n = len(_real_tools())
        readme = _read('README.md')
        assert '(%d tools)' % n in readme or '(%d tools' % n in readme, (
            'README does not state the real tool count (%d)' % n)

    def test_endpoint_count_claim(self):
        n = len(_real_endpoints())
        readme = _read('README.md')
        assert '%d endpoints' % n in readme, (
            'README does not state the real endpoint count (%d)' % n)

    def test_endpoint_list_is_complete(self):
        """The list must name every real endpoint - the 2026-10-20 sweep found
        it naming 11 routes while claiming 12, silently dropping both /court
        routes."""
        readme = _read('README.md')
        m = re.search(r'(\d+) endpoints:\s*(.+)', readme)
        assert m, 'README has no "N endpoints: ..." list'
        listed = set(re.findall(r'`(/[^`]*)`', m.group(2)))  # * not +: the bare `/` route
        missing = set(_real_endpoints()) - listed
        assert not missing, 'README endpoint list is missing: %s' % sorted(missing)

    def test_no_stale_tool_count(self):
        readme = _read('README.md')
        for stale in ('9 tools', '8 tools'):
            assert stale not in readme, 'README still claims %r' % stale

    def test_no_stale_endpoint_count(self):
        readme = _read('README.md')
        for stale in ('12 endpoints', '11 endpoints', '9 endpoints'):
            assert stale not in readme, 'README still claims %r' % stale


class TestReadmeCliCoverage:
    def test_every_subcommand_is_documented_somewhere(self):
        """Every real subcommand must appear in the README - the 2026-10-20
        sweep found 8 of 20 documented nowhere at all.

        Scope note: they do not all have to live in the Memory Operations
        block. Client wiring (init/connect/setup) belongs in "Connect Your
        Clients", demo/download-models in Quick Start. What is not acceptable
        is a subcommand existing in --help but nowhere in the docs.
        """
        readme = _read('README.md')
        used = set(re.findall(r'memtether ([a-z][a-z0-9-]*)', readme))
        missing = set(_real_cli_subcommands()) - used
        assert not missing, (
            'These subcommands exist in --help but appear nowhere in README: %s'
            % sorted(missing))

    def test_memory_ops_block_covers_core_operations(self):
        """The Memory Operations block itself must cover the memory-lifecycle
        commands (write/read/correct/retire/inspect), not just search."""
        readme = _read('README.md')
        m = re.search(r'## Memory Operations\s*\n```bash\n(.*?)```', readme, re.S)
        assert m, 'README has no Memory Operations block'
        block = m.group(1)
        used = set(re.findall(r'memtether ([a-z][a-z0-9-]*)', block))
        required = {'remember', 'search', 'correct', 'retire', 'stats',
                    'export-md', 'backup', 'profile'}
        missing = required - used
        assert not missing, 'Memory Operations block omits: %s' % sorted(missing)


class TestPyImageFallback:
    def test_demo_image_has_absolute_fallback(self):
        """PyPI does not resolve repo-relative image paths, so the landing GIF
        404s there unless an absolute-URL fallback is present. Verified against
        the live pypi.org JSON API on 2026-10-20 (relative src, 0 onerror)."""
        for rel in ('README.md', 'README.zh-CN.md'):
            body = _read(rel)
            imgs = re.findall(r'<img src="([^"]+)"', body)
            assert imgs, '%s has no <img>' % rel
            for src in imgs:
                if src.startswith(('http://', 'https://')):
                    continue
                # relative src -> must have a fallback to an absolute URL
                assert 'onerror=' in body, (
                    '%s uses relative image %r with no onerror fallback '
                    '(broken on PyPI)' % (rel, src))
                assert 'raw.githubusercontent.com' in body, (
                    '%s relative image has no raw-GitHub fallback' % rel)


class TestDocsApiLayerParam:
    def test_api_doc_documents_layer(self):
        body = _read('docs/API.md')
        assert 'layer' in body, 'docs/API.md does not document /search layer param'

    def test_api_doc_documents_court_routes(self):
        body = _read('docs/API.md')
        for route in ('/court/verify', '/court/{uid}'):
            assert route in body, 'docs/API.md missing %s' % route


class TestReproducePathsResolve:
    """Every path the README tells a reader to run must actually exist in the
    published tree.

    Why: the 2026-10-21 audit found the README pointing LoCoMo reproduction at
    `scripts/run_full_benchmark.py` (does not exist) and
    `_dev/memeval/.../memtether.py` (exists locally but `_dev/` is gitignored,
    so nobody who clones the repo has it). The benchmark numbers may well be
    real, but a reproduce path nobody can walk is a decoration, not evidence.
    """
    def _readme_backtick_paths(self):
        """Only paths the README presents as something to RUN or REPRODUCE.

        Scoped deliberately: a blanket "every backticked filename must exist"
        also flags third-party config names mentioned in prose (e.g. DSH's
        cordis.patch.yml, which lives on the user's machine, not here). What
        matters is the reproduce contract, so that is what is checked.
        """
        body = _read('README.md')
        out = []
        for m in re.finditer(r'`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|sh))`', body):
            start = max(0, m.start() - 160)
            ctx = body[start:m.start()].lower()
            if any(w in ctx for w in ('reproduce', 'run ', 'python ', 'scripts/',
                                      'adapter', 'benchmark', 'see ')):
                out.append(m.group(1))
        return [c for c in out if not c.startswith(('http', '/'))]

    def test_every_referenced_script_exists(self):
        missing = [c for c in self._readme_backtick_paths()
                   if not os.path.exists(os.path.join(REPO, c))]
        assert not missing, (
            'README references files that do not exist in the repo: %s' % missing)

    def test_no_readme_path_points_into_gitignored_dirs(self):
        """A reproduce path inside _dev/ or another ignored dir is unreachable
        for anyone who clones."""
        ignored = ('_dev/', 'demo/', 'dist/', '_archive/', 'dev/')
        bad = [c for c in self._readme_backtick_paths()
               if c.startswith(ignored)]
        assert not bad, (
            'README points into gitignored dirs (unreachable after clone): %s' % bad)

    def test_adapter_has_no_machine_specific_paths(self):
        """The published benchmark adapter must import the package, not inject
        a local absolute path."""
        ad = os.path.join(REPO, 'scripts', 'bench_memtether_adapter.py')
        assert os.path.exists(ad), 'bench_memtether_adapter.py missing'
        body = io.open(ad, encoding='utf-8').read()
        # allow the historical note in the comment, but no live path injection
        # ★2026-10-21 修（变异测试发现的真漏洞）：第一版只查字面
        #   'sys.path.insert'，于是 `import sys as _s; _s.path.insert(...)`
        #   这种别名写法完全绕过守卫。改为查语义：任何 path 注入 + 任何
        #   盘符绝对路径，不管模块叫什么名字。
        live = []
        for l in body.split('\n'):
            code = l.split('#', 1)[0]          # 去掉注释，历史说明不算
            if '.path.insert' not in code and 'sys.path' not in code:
                continue
            if re.search(r'[A-Za-z]:' + chr(92) + chr(92) + r'|/home/|/Users/|E:/|D:/', code):
                live.append(l)
        assert not live, 'adapter still injects a machine-specific path: %s' % live
