#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_onboard.py —— 部署即接入：把"指令/扩展层"注入全部自动化，测试者零手工命令。

三层边界（与 install.ps1 对应，本脚本只做第三层）
------------------------------------------------
  · MCP 配置层（mcp.json / 信任代写 / 来源注册）  → tether_connect.py
  · 投影层（MEMORY.md / sink.json）               → hub 的 gateway.py rebuild
  · **指令/扩展层**（本脚本）：
      - Codex 桌面 app：官方插件机制（marketplace 注册 + `codex plugin add`
        免 GUI 安装 + ~/.codex/skills symlink + 插件 .mcp.json 实例化）
        ——桌面 app 不读 config.toml 的 [mcp_servers]
      - OpenClaw：workspace/AGENTS.md（会话开场注入源）
      - dsh：~/.dsh/AGENTS.md（用户级指令，所有 profile 生效）
      - 豆包：projections/*.md 腐蚀路径修复（ rebuild 不生成这些文件，是手工快照）
为什么必须自动化：这些注入此前全是手工一次性动作——手工 = 未来每个测试者
都要重做一遍 = 违反"部署即接入"。本脚本把它们全部自动化且**幂等**
（语义一致一个字节都不写），供 install.ps1 第 3.5 步调用，也可独立执行。

设计纪律（与 tether_connect 四硬约束同源）
------------------------------------------
1. **不猜**：客户端没装（根目录/指令文件不存在）就 MISS 跳过并明示。
2. **语义一致不写盘**：每项注入前先比对，一致则 SKIP。
3. **锚点唯一化**：注入块用 `# >>> memtether-auto-onboard` 标记；
   替换前断言标记唯一，不唯一即整体中止，一个字节都不写。
4. **零硬编码本机路径**：hub 从 --hub-dir → $MEM_HUB → 仓库同级 memory_hub 推导；
   venv 解释器按平台推导；config.toml 先备份再改（原子替换防半写）。
5. **不覆盖高质量人工块**：AGENTS.md 里已有手工注入的 MemTether 指引
   （本机 openclaw/dsh 就是）→ SKIP 并明示，不用通用模板覆盖它。
6. **遗留迁移**：本脚本上线前手工加的无标记块（如 config.toml 的
   [marketplaces.memtether-local]）→ 识别并替换为带标记块，防双注册。

用法
----
    python auto_onboard.py detect                 # 只读：谁装了、缺哪些注入
    python auto_onboard.py apply [--hub-dir DIR]  # 注入（幂等，可重复跑）
    python auto_onboard.py apply --dry-run        # 只看会改什么，不动盘

退出码：0=全部处理完（含 SKIP/MISS/WARN）· 1=hub 找不到/参数错 · 2=某项写入失败
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                      # integrations/ 的上级 = 仓库根
MARK_BEGIN = '# >>> memtether-auto-onboard'
MARK_END = '# <<< memtether-auto-onboard'
MARKETPLACE_NAME = 'memtether-local'
PLUGIN_NAME = 'memtether-memory'
# 插件规范位置 = marketplace 根内（官方布局：<marketplace>/plugins/<name>）。
# ⚠️ 不能放 marketplace 外：CLI 会静默拒绝根外路径（实测 ../ 与绝对路径都列不出来）。
PLUGIN_DIR = os.path.join(REPO, 'integrations', 'codex-marketplace', 'plugins',
                          PLUGIN_NAME)
MCP_TEMPLATE = os.path.join(PLUGIN_DIR, '.mcp.json.template')
MCP_INSTANCE = os.path.join(PLUGIN_DIR, '.mcp.json')

_DRY = False          # --dry-run / detect 时为 True：一切写盘动作被拦截


def _read(p):
    return io.open(p, encoding='utf-8').read()


def _write(p, t):
    """备份 + 原子替换。dry-run 时只记录不动盘。"""
    if _DRY:
        return
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    if os.path.exists(p):
        bak = p + '.bak-' + time.strftime('%Y%m%d')
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='',
                                     dir=d or '.', delete=False) as f:
        f.write(t)
        tmp = f.name
    os.replace(tmp, p)                            # 原子替换（防半写）


# ---------------------------------------------------------------- 文本块
def _venv_py(hub):
    """hub venv 解释器（跑本中枢必须用它，系统 python 会静默降级成纯关键词）。"""
    if os.name == 'nt':
        c = os.path.join(hub, '.venv-memory', 'Scripts', 'python.exe')
    else:
        c = os.path.join(hub, '.venv-memory', 'bin', 'python')
    return c if os.path.isfile(c) else 'python'


def _guide(hub):
    """注入到 AGENTS.md 的指引块。文档路径按本机实况发现（没有的省掉），
    同一机器上多次运行字节一致 ⇒ 幂等。"""
    docs = []
    manual = os.path.join(os.path.dirname(REPO), 'ai-audit', '完全理解手册',
                          '00-总览与使用法.md')
    if os.path.isfile(manual):
        docs.append('`%s`（项目全景+使用规则）' % manual)
    handover = os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'),
                            '.agents', 'HANDOVER-CURRENT.md')
    if os.path.isfile(handover):
        docs.append('当前状态 `%s`' % handover)
    doc_line = ('- **开工先读**：' + '；'.join(docs) + '。') if docs else ''
    vpy = _venv_py(hub).replace('\\', '/')
    hub_f = hub.replace('\\', '/')
    return '''{begin}
## MemTether 记忆中枢（auto-onboard 注入；删掉这对标记行之间的内容即移除）

你与 WorkBuddy（国际版/国内版）/ CodeBuddy / ZCode / Codex / Tabbit / OpenClaw /
dsh / 豆包**共享同一份物理长期记忆**：`{hub}\\memory.db`（SQLite，唯一真源）。

{doc}- **检索/写入首选 MCP**：`memory-hub`（search_memory / add_memories / list_memories，
  工具默认隐藏，先 ToolSearch 载入）。兜底 CLI（**必须用 venv 解释器**，
  用系统 python 会静默降级成纯关键词）：
  `{vpy} {hub}/mem.py search "<关键词>"`；
  写入：`{vpy} {hub}/gateway.py remember "<结论>" --source <你的来源名>`（**source 必带**）。
- **判据（可自检）**：读后能说出项目一句话定义（同机多客户端共享同一份物理
  memory.db）与三个"它不是"（不是云服务/不是跨机器同步/不是 mem0 复刻）。
- **造轮子前必搜**手册卷 12（编号化事故档案）；没搜过不许说"没有"。
{end}
'''.format(begin=MARK_BEGIN, end=MARK_END, hub=hub.rstrip('\\/'),
           doc=doc_line, vpy=vpy, hub_f=hub_f)


TOML_BLOCK = '''{begin}
# MemTether 本地 marketplace（auto-onboard 注入）：让 Codex 桌面 app 的插件页
# 能一键安装 memory-hub 插件（官方插件机制，app/CLI/IDE 三端通用）
[marketplaces.memtether-local]
source_type = "local"
source = '\\\\?\\{mp}'
{end}
'''


def _toml_block(mp):
    # 注意：TOML 单引号串是字面串不转义 ⇒ 生成 `\\?\` + 单反斜杠路径，
    # 与 config.toml 里既有两个 marketplace 的字节形态严格一致。
    return TOML_BLOCK.format(begin=MARK_BEGIN, end=MARK_END, mp=mp)


# ---------------------------------------------------------------- 通用注入
def _marked_span(t):
    """返回 (begin_idx, end_idx_exclusive)。标记不唯一时抛异常（一个字节都不写）。"""
    n = t.count(MARK_BEGIN)
    if n != 1:
        raise RuntimeError('标记行 %s 出现 %d 次（应为 1），已中止' % (MARK_BEGIN, n))
    i = t.index(MARK_BEGIN)
    j = t.index(MARK_END, i) + len(MARK_END)
    return i, j


def inject_marked(path, block, label, report, covered_test=None):
    """幂等注入标记块到任意文本文件。

    covered_test: 可选 callable(text)->bool，为真表示文件已有等效人工内容 → SKIP。
    """
    if not os.path.isfile(path):
        report.append(('MISS', label, '文件不存在（客户端未装？）：' + path))
        return
    t = _read(path)
    if MARK_BEGIN in t:
        i, j = _marked_span(t)
        if t[i:j].strip() == block.strip():
            report.append(('SKIP', label, '已注入且一致'))
        else:
            _write(path, t[:i] + block.strip() + t[j:])
            report.append(('OK', label, '标记块内容已更新'))
        return
    if covered_test and covered_test(t):
        report.append(('SKIP', label, '已有等效人工注入（质量高于通用模板），未动'))
        return
    _write(path, t.rstrip() + '\n\n' + block.strip() + '\n')
    report.append(('OK', label, '已追加注入块'))


def _covered_by_memtether(t):
    """AGENTS.md 是否已含等效 MemTether 指引（人工写的，不动它）。"""
    return ('memory.db' in t
            and ('memory-hub' in t or 'mem.py' in t or 'MemTether' in t))


# ---------------------------------------------------------------- Codex 三件套
def _toml_section_span(t, header):
    """定位 [header] section：向上吞掉紧邻的、含 memtether 字样的注释行，
    向下到下一个行首 [ 或文件尾。返回 (i, j) 或 None。"""
    lines = t.split('\n')
    hi = None
    for k, l in enumerate(lines):
        if l.strip() == header:
            hi = k
            break
    if hi is None:
        return None
    i = hi
    while i > 0 and lines[i - 1].strip().startswith('#') \
            and 'memtether' in lines[i - 1].lower():
        i -= 1
    j = hi + 1
    while j < len(lines) and not lines[j].startswith('['):
        j += 1
    return ('\n'.join(lines[:i]), '\n'.join(lines[i:j]), '\n'.join(lines[j:]))


def inject_codex_marketplace(hub, report):
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    cfg = os.path.join(home, '.codex', 'config.toml')
    if not os.path.isfile(cfg):
        report.append(('MISS', 'codex/config.toml', '未装 Codex CLI，跳过'))
        return
    t = _read(cfg)
    block = _toml_block(os.path.join(REPO, 'integrations', 'codex-marketplace'))
    if MARK_BEGIN in t:
        i, j = _marked_span(t)
        if t[i:j].strip() == block.strip():
            report.append(('SKIP', 'codex/marketplace', '已注册且一致'))
        else:
            _write(cfg, t[:i] + block.strip() + t[j:])
            report.append(('OK', 'codex/marketplace', '注册块已更新'))
        return
    span = _toml_section_span(t, '[marketplaces.memtether-local]')
    if span:                                     # 遗留无标记手工块 → 迁移
        pre, old, post = span
        _write(cfg, pre.rstrip('\n') + '\n' + block.strip() + '\n' + post.lstrip('\n'))
        report.append(('OK', 'codex/marketplace', '遗留无标记注册块已迁移为标记块（防双注册）'))
        return
    _write(cfg, t.rstrip() + '\n\n' + block.strip() + '\n')
    report.append(('OK', 'codex/marketplace', '已注册 memtether-local'))


def inject_codex_skills(report):
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    sk_dir = os.path.join(home, '.codex', 'skills')
    src = os.path.join(home, '.agents', 'skills', 'memory-hub-usage')
    dst = os.path.join(sk_dir, 'memory-hub-usage')
    if os.path.islink(dst):
        if os.path.realpath(dst) == os.path.realpath(src):
            report.append(('SKIP', 'codex/skills', 'symlink 已存在且指向正确'))
        else:
            report.append(('WARN', 'codex/skills',
                           'symlink 指向非预期目标，未动：%s → %s' % (dst, os.readlink(dst))))
        return
    if os.path.exists(dst):
        report.append(('WARN', 'codex/skills', '目标已存在且非 symlink，未动：' + dst))
        return
    if not os.path.isdir(src):
        report.append(('MISS', 'codex/skills',
                       '技能源不存在（未部署 ~/.agents/skills/memory-hub-usage？）：' + src))
        return
    if not _DRY:
        os.makedirs(sk_dir, exist_ok=True)
        try:
            os.symlink(src, dst)
        except OSError:
            shutil.copytree(src, dst)
            report.append(('OK', 'codex/skills', 'symlink 失败已降级复制'))
            return
    report.append(('OK', 'codex/skills', 'symlink 已建（~/.codex/skills 是用户级技能目录）'))


def instantiate_plugin_mcp(hub, report):
    """把插件模板 .mcp.json.template 实例化为 .mcp.json（占位符换成本机路径）。

    模板放仓库（干净可分发），实例化文件 .gitignore（本机状态）。
    解释器用 hub venv 的 python——用系统 python 会静默降级成纯关键词检索。
    """
    label = 'codex/plugin-mcp.json'
    if not os.path.isfile(MCP_TEMPLATE):
        report.append(('MISS', label, '模板不存在：' + MCP_TEMPLATE))
        return
    vpy = _venv_py(hub)
    if vpy == 'python':
        report.append(('WARN', label,
                       '未找到 hub venv 解释器，退回 "python"（有静默降级风险）'))
    inst = _read(MCP_TEMPLATE) \
        .replace('<HUB_VENV_PY>', vpy.replace('\\', '/')) \
        .replace('<HUB>', hub.rstrip('\\/').replace('\\', '/'))
    # 合法性预检：模板渲染后必须是合法 JSON 且不含占位符残留
    try:
        json.loads(inst)
    except Exception as e:
        report.append(('FAIL', label, '渲染结果不是合法 JSON：%s' % e))
        return
    if '<HUB' in inst:
        report.append(('FAIL', label, '占位符未替换完'))
        return
    if os.path.isfile(MCP_INSTANCE) and _read(MCP_INSTANCE) == inst:
        report.append(('SKIP', label, '已实例化且一致'))
        return
    _write(MCP_INSTANCE, inst)
    report.append(('OK', label, '已按本机 hub 路径实例化（venv 解释器）'))


def install_codex_plugin(report):
    """免 GUI 安装插件：codex plugin add（官方 CLI 子命令，2026-09-21 实测可用）。

    没有这一步，用户必须手动进桌面 app 插件页点 Install——违反"部署即接入"。
    幂等：已安装则 SKIP（先查状态再装，不依赖 add 的报错文案）。
    """
    import subprocess
    label = 'codex/plugin-install'
    codex = shutil.which('codex')
    if not codex:
        report.append(('MISS', label,
                       'codex CLI 不在 PATH（未装 Codex？跳过；桌面 app 可仍在插件页手动装）'))
        return
    try:
        r = subprocess.run([codex, 'plugin', 'list', '--marketplace', MARKETPLACE_NAME],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
        out = (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        report.append(('WARN', label, '查询安装状态失败：%s' % e))
        return
    if ('installed' in out) and (PLUGIN_NAME in out):
        report.append(('SKIP', label, '插件已安装（codex plugin list 确认）'))
        return
    if 'No plugins found' in out or PLUGIN_NAME not in out:
        report.append(('FAIL', label,
                       'marketplace 里找不到插件——检查 marketplace.json 的 path 是否在'
                       ' marketplace 根内，以及插件 .codex-plugin/plugin.json 是否合法'))
        return
    if _DRY:
        report.append(('OK', label, '将执行 codex plugin add（免 GUI 安装并启用）'))
        return
    try:
        r = subprocess.run([codex, 'plugin', 'add', PLUGIN_NAME,
                            '--marketplace', MARKETPLACE_NAME],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=120)
        out = (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        report.append(('FAIL', label, 'codex plugin add 执行失败：%s' % e))
        return
    if r.returncode == 0 and 'Added plugin' in out:
        report.append(('OK', label, '已免 GUI 安装并启用（codex plugin add）'))
    else:
        report.append(('FAIL', label, 'codex plugin add 未成功：%s' % out.strip()[:200]))


# ---------------------------------------------------------------- AGENTS.md 系
def inject_openclaw(hub, report):
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    root = os.path.join(home, '.openclaw')
    p = os.path.join(root, 'workspace', 'AGENTS.md')
    if not os.path.isdir(root):
        report.append(('MISS', 'openclaw/AGENTS.md', '未装 OpenClaw（~/.openclaw 不存在）'))
        return
    if not os.path.isfile(p):
        _write(p, _guide(hub).strip() + '\n')
        report.append(('OK', 'openclaw/AGENTS.md', '已新建并注入（全新部署）'))
        return
    inject_marked(p, _guide(hub), 'openclaw/AGENTS.md', report, _covered_by_memtether)


def inject_dsh(hub, report):
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    root = os.path.join(home, '.dsh')
    p = os.path.join(root, 'AGENTS.md')
    if not os.path.isdir(root):
        report.append(('MISS', 'dsh/AGENTS.md', '未装 dsh（~/.dsh 不存在）'))
        return
    if not os.path.isfile(p):
        _write(p, _guide(hub).strip() + '\n')
        report.append(('OK', 'dsh/AGENTS.md', '已新建并注入（全新部署）'))
        return
    inject_marked(p, _guide(hub), 'dsh/AGENTS.md', report, _covered_by_memtether)


# ---------------------------------------------------------------- 豆包投影修复
_SLASH_RUN = re.compile(r'/{2,}')


def _collapse_line(line):
    """把 Windows 盘符路径里的重复斜杠（E://RUANJIAN//x）塌成单斜杠。
    含 URL 的行整体不动（防误伤 https://）。"""
    if 'http://' in line or 'https://' in line:
        return line
    if not re.search(r'[A-Za-z]:[\\/]', line):
        return line
    return _SLASH_RUN.sub('/', line)


def normalize_doubao(hub, report):
    """projections/*.md 是手工维护的会话开场快照（rebuild 不生成它们），
    早年经 bash  heredoc 注入时路径被腐蚀成双斜杠。本步幂等修复。"""
    proj = os.path.join(hub, 'projections')
    if not os.path.isdir(proj):
        report.append(('MISS', 'doubao/projections', '目录不存在，跳过'))
        return
    fixed = 0
    for name in sorted(os.listdir(proj)):
        if not name.endswith('.md'):
            continue
        p = os.path.join(proj, name)
        if not os.path.isfile(p):
            continue
        t = _read(p)
        t2 = '\n'.join(_collapse_line(l) for l in t.split('\n'))
        if t2 != t:
            _write(p, t2)
            fixed += 1
    if fixed:
        report.append(('OK', 'doubao/projections', '%d 个文件的腐蚀路径（双斜杠）已修复' % fixed))
    else:
        report.append(('SKIP', 'doubao/projections', '无腐蚀路径（幂等）'))


# ---------------------------------------------------------------- 主流程
def resolve_hub(arg):
    for cand in (arg, os.environ.get('MEM_HUB'),
                 os.path.join(os.path.dirname(REPO), 'memory_hub')):
        if cand and os.path.isfile(os.path.join(cand, 'memory.db')):
            return os.path.abspath(cand)
    print('★ 找不到记忆中枢（memory.db）。用 --hub-dir 指定，或设环境变量 MEM_HUB。')
    sys.exit(1)


_STEPS = (
    ('Codex 桌面 app 插件机制（官方正道）', lambda hub, r: (
        inject_codex_marketplace(hub, r),
        install_codex_plugin(r),                 # 免 GUI：codex plugin add
        inject_codex_skills(r),
        instantiate_plugin_mcp(hub, r))),
    ('OpenClaw workspace/AGENTS.md', inject_openclaw),
    ('dsh 用户级 AGENTS.md', inject_dsh),
    ('豆包投影腐蚀路径修复', normalize_doubao),
)


def collect(hub):
    report = []
    for _name, fn in _STEPS:
        try:
            fn(hub, report)
        except Exception as e:
            report.append(('FAIL', _name, '%s: %s' % (type(e).__name__, e)))
    return report


def main():
    global _DRY
    ap = argparse.ArgumentParser(description='MemTether 指令/扩展层自动接入（幂等）')
    ap.add_argument('cmd', choices=['detect', 'apply'])
    ap.add_argument('--hub-dir')
    ap.add_argument('--dry-run', action='store_true', help='只计划不动盘（仅 apply 有效）')
    a = ap.parse_args()
    hub = resolve_hub(a.hub_dir)

    _DRY = a.dry_run or a.cmd == 'detect'
    report = collect(hub)

    title = 'auto_onboard %s（hub=%s）' % (
        '预演·不动盘' if _DRY else '结果', hub)
    print('=' * 78)
    print(title)
    print('=' * 78)
    if a.cmd == 'detect':
        print('（detect 为只读盘点；apply 才落盘）')
    counts = {}
    for status, label, msg in report:
        counts[status] = counts.get(status, 0) + 1
        shown = {'OK': '待注入' if a.cmd == 'detect' else '已注入',
                 'SKIP': '已就绪', 'MISS': '未装/无', 'WARN': '注意',
                 'FAIL': '失败'}.get(status, status)
        print('  [%-4s] %-26s %s' % (shown, label, msg))
    print('-' * 78)
    print('  注入 %d · 已就绪 %d · 未装 %d · 注意 %d · 失败 %d'
          % (counts.get('OK', 0), counts.get('SKIP', 0), counts.get('MISS', 0),
             counts.get('WARN', 0), counts.get('FAIL', 0)))
    print('  MCP 配置层由 tether_connect 负责，投影层由 hub rebuild 负责，本脚本不重复。')
    return 2 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
