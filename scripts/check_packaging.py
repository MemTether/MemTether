"""check_packaging.py —— 校验 pyproject.toml 的模块清单与仓库实际内容一致。

为什么需要它
------------
2026-09-16 实测缺陷：`packaging/` 里的 pyproject 声称打包 `memtether` 包，
但真实模块全在仓库根 —— `pip install` **一路成功**，装完却什么都干不了。

修好之后的新风险是**清单漂移**：以后新增一个根模块、忘了补进 `py-modules`，
wheel 里就没有它。而这个错误在 `pip install` 时**不会有任何提示**，
只会在用户运行到那条路径时才炸 —— 属"跑起来不报错、但结果全错"。

判据（唯一真源）
----------------
    wheel 里该有的根模块 == 仓库里 **git 跟踪的**、不以 `_` 开头的根 .py

  · **只算 git 跟踪的**：干净 clone 里不存在未跟踪文件；列了它会构建失败
    （反例：`cleanup_tmp.py` 在 .gitignore 里，就是未跟踪的）；
  · **排除 `_` 开头**：这是本仓库既有约定（.gitignore 用 `_[!_]*.py` 忽略临时脚本），
    所以"临时脚本必须以 `_` 开头"这条约定现在被强制了；
  · `memtether.py`（门面）也在其中，**不需要特例** —— 规则越少越不会忘。
  · **`.pyw` 单独一条规则**（2026-09-17 补）：往 `py-modules` 里写 `.pyw` 是**假动作**。
    实测 setuptools 84.0.0 的 `build_py.find_modules()` 只认 `.py` 源码，`.pyw` 会被
    **静默跳过**（不报错、不警告），于是清单看着像打包了、wheel 里其实没有。
    所以本脚本对 `.pyw` 另立两条：
      ① 任何被 git 跟踪的根 `.pyw` 必须在 NON_PACKAGED_PYW 里**显式登记**
         —— 它是"刻意不随包分发"的一份**决定**，不是扫描的盲区；
      ② 任何 `.pyw` 都不允许出现在 `py-modules` 里（会制造"已打包"的错觉）。

用法
----
    python scripts/check_packaging.py

退出码：0 = 一致；1 = 有差异；2 = 读不到配置 / 不在 git 仓库
"""
import os
import re
import subprocess
import sys

def _fix_stdio():
    """★2026-09-23 补：stdout/stderr 按 UTF-8 重配（errors=replace）。

    根因：Windows 控制台默认 GBK，而本脚本结尾用 print('✓ ...') 输出成功结论。
    实测 python scripts/check_packaging.py 在**全部校验通过之后**于最后一行
    抛 UnicodeEncodeError: 'gbk' codec can't encode character '\u2713' ——
    exit=1，回看像"打包清单不一致"，真相是"检查全过、报告崩了"。
    这是本项目"跑完但结果错/不可用"家族的第 7 例。
    必须在任何 print 之前调用。
    """
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        try:
            if _s is not None and hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_stdio()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYPROJECT = os.path.join(ROOT, 'pyproject.toml')
NOTICE_PATH = os.path.join(ROOT, 'NOTICE')

# ★2026-09-17：`.pyw` 是 Windows 专用启动器（由 pythonw.exe 执行、不建控制台窗口），
#   不能作为 py-modules 进 wheel —— 实测 setuptools 84.0.0 的 build_py.find_modules()
#   只认 `.py` 源，`.pyw` 会被**静默跳过**。所以它们**刻意不随包分发**。
#   但"刻意不打包"必须是一份**显式决定**，不是扫描盲区 —— 新增 .pyw 而不登记，
#   本脚本就报错（否则又是一个"跑起来不报错、但结果全错"）。
NON_PACKAGED_PYW = {
    'hidden_run': '计划任务的无窗口运行器（Windows 专用；用户按需从仓库取用）',
}


def load_toml(path):
    """读 TOML。Python 3.11+ 用内置 tomllib，3.10 需 tomli。"""
    try:
        import tomllib
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli
        with open(path, 'rb') as f:
            return tomli.load(f)
    except ImportError:
        # ★退出码必须是 2（环境不满足），不能是 1（清单不一致）——
        #   SystemExit('字符串') 的退出码是 1，会被调用方误读成"清单有差异"。
        print('★读 TOML 需要 Python 3.11+（内置 tomllib），或先 pip install tomli')
        print('  当前解释器：%s' % sys.executable)
        raise SystemExit(2)


def tracked_root_modules():
    r = subprocess.run(['git', 'ls-files', '*.py'], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print('★%s 不是 git 仓库（或 git 不可用）：%s'
              % (ROOT, (r.stderr or '').strip()))
        raise SystemExit(2)
    mods = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or '/' in line:
            continue                      # 只看仓库根，子目录模块由 packages 管
        name = line[:-3] if line.endswith('.py') else line
        if name.startswith('_'):
            continue                      # 本仓库约定：临时脚本以 _ 开头，不进包
        mods.add(name)
    return mods


def tracked_root_pyw():
    """被 git 跟踪的、仓库根的 .pyw（去掉扩展名）。子目录的不算。"""
    r = subprocess.run(['git', 'ls-files', '*.pyw'], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print('★%s 不是 git 仓库（或 git 不可用）：%s'
              % (ROOT, (r.stderr or '').strip()))
        raise SystemExit(2)
    names = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or '/' in line:
            continue
        names.add(line[:-4] if line.lower().endswith('.pyw') else line)
    return names


def check_pyw(declared, problems):
    """`.pyw` 三条规则：必须登记 / 登记不许过期 / 不许进 py-modules。"""
    pyw = tracked_root_pyw()
    unregistered = sorted(n for n in pyw if n not in NON_PACKAGED_PYW)
    if unregistered:
        problems.append(
            '仓库新增了根 .pyw 但未登记：%s —— 若它应随包分发请改 pyproject，'
            '若刻意不打包请加进 check_packaging.NON_PACKAGED_PYW'
            % ', '.join(unregistered))
    stale = sorted(n for n in NON_PACKAGED_PYW if n not in pyw)
    if stale:
        problems.append('NON_PACKAGED_PYW 里登记的 .pyw 在仓库里已不存在：%s'
                        % ', '.join(stale))
    conflict = sorted(n for n in pyw if n in declared)
    if conflict:
        problems.append(
            '这些 .pyw 同时出现在 py-modules 里：%s —— 实测 setuptools 只认 .py 源，'
            '会**静默漏掉**（看着像打包了其实没有），请从 py-modules 移除'
            % ', '.join(conflict))
    return pyw


def check_notice(cfg, problems):
    """NOTICE 一致性：声明的依赖 / 用到的模型，都必须有归属声明。

    ★为什么查这个：NOTICE 的组件清单与 pyproject 的依赖清单是**两份手工维护的
      清单**，必然漂移（同 py-modules 那次的形态）。而 NOTICE 写漏了、写错了，
      构建与安装**全程没有任何提示** —— 又是"跑起来不报错、但结果全错"。
      且这是合规问题：Apache-2.0 §4(d) 要求分发时保留归属声明。

    三条规则（前两条跨机器有效，第三条依赖本机模型目录）：

      A. pyproject 声明的每个依赖名，必须能在 NOTICE 里找到；
      B. 模型目录存在时，其每个子目录名必须能在 NOTICE 里找到（漏记 = 漏归属）；
      C. NOTICE 里以 `models/<名字>` 形式出现的目录，必须真实存在（写错目录名）。

    ★找不到模型目录时**跳过 B/C 并打印 skipped**，不是报错 ——
      模型权重不在仓库里，别人的 clone 上本来就没有。
    """
    notice_path = NOTICE_PATH
    if not os.path.exists(notice_path):
        problems.append('NOTICE 不存在（Apache-2.0 分发需保留第三方归属声明）')
        return None
    with open(notice_path, encoding='utf-8') as f:
        notice = f.read()

    # --- A：依赖必须有归属 ---
    names = []
    for d in (cfg.get('project') or {}).get('dependencies') or []:
        names.append(d)
    for extra in ((cfg.get('project') or {}).get('optional-dependencies') or {}).values():
        names.extend(extra)
    for spec in names:
        name = re.split(r'[<>=!\[; ]', spec)[0].strip()
        if name and not re.search(r'\b%s\b' % re.escape(name), notice, re.I):
            problems.append('依赖 %r 未在 NOTICE 中声明归属' % name)

    # --- B / C：模型目录 ---
    # 依次探测：显式环境变量 → <ROOT>/models → 本机布局 <ROOT>/../memory_hub/models
    candidates = [os.environ.get('MEMTETHER_MODELS_DIR'),
                  os.path.join(ROOT, 'models'),
                  os.path.join(os.path.dirname(ROOT), 'memory_hub', 'models')]
    models_dir = next((c for c in candidates if c and os.path.isdir(c)), None)
    if models_dir is None:
        return '模型目录不存在，B/C 已跳过'

    local = sorted(n for n in os.listdir(models_dir)
                   if os.path.isdir(os.path.join(models_dir, n)))
    for d in local:
        if not re.search(r'\b%s\b' % re.escape(d), notice):
            problems.append('本机模型目录 %r 未在 NOTICE 中声明归属' % d)
    for d in sorted(set(re.findall(r'models/([A-Za-z0-9_.\-]+)', notice))):
        if d not in local:
            problems.append('NOTICE 写了 models/%s，但该目录不存在（目录名写错了？）' % d)
    return '模型目录 %s：%d 个已全部登记' % (models_dir, len(local))


def main():
    if not os.path.exists(PYPROJECT):
        print('★找不到 %s' % PYPROJECT)
        return 2
    cfg = load_toml(PYPROJECT)
    ts = (cfg.get('tool') or {}).get('setuptools') or {}
    declared = set(ts.get('py-modules') or [])
    packages = list(ts.get('packages') or [])
    entry = ((cfg.get('project') or {}).get('scripts') or {})

    actual = tracked_root_modules()
    problems = []

    missing = sorted(actual - declared)   # 仓库有、清单没有 → wheel 里会缺
    extra = sorted(declared - actual)     # 清单有、仓库没有 → 构建失败/装出空壳
    if missing:
        problems.append('清单缺这些模块（wheel 里会没有它们）：%s' % ', '.join(missing))
    if extra:
        problems.append('清单里有仓库不存在的模块（构建会失败或装出空壳）：%s'
                        % ', '.join(extra))

    if 'scripts' not in packages:
        problems.append('packages 未包含 "scripts"（演示库生成器不在 wheel 里）')
    elif not os.path.exists(os.path.join(HERE, '__init__.py')):
        problems.append('scripts/__init__.py 缺失 → "scripts" 不是合法包，装不进 wheel')

    if entry.get('memtether') != 'memtether:main':
        problems.append('[project.scripts] memtether 应指向 "memtether:main"，实际是 %r'
                        % entry.get('memtether'))

    if 'memtether' not in declared:
        problems.append('清单缺 memtether（门面 / CLI 入口）')

    pyw = check_pyw(declared, problems)

    # ★版本漂移：pyproject 的 version 与 memtether.py 的 __version__ 必须一致。
    #   2026-09-16 实测踩到：pyproject 写 "0.1.0-pre"（不是合法 PEP 440），
    #   setuptools 静默归一化成 "0.1.0rc0" —— 于是 `memtether --version`
    #   与包元数据**报两个版本号**，而构建全程零警告。属"不报错但结果错"。
    ver_pj = str((cfg.get('project') or {}).get('version') or '')
    ver_py = ''
    facade = os.path.join(ROOT, 'memtether.py')
    if os.path.exists(facade):
        with open(facade, encoding='utf-8') as f:
            m = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]',
                          f.read(), re.M)
        ver_py = m.group(1) if m else ''
    if not ver_py:
        problems.append('读不到 memtether.py 的 __version__')
    elif ver_pj != ver_py:
        problems.append('版本漂移：pyproject=%r 与 memtether.py __version__=%r 不一致'
                        % (ver_pj, ver_py))
    elif not re.fullmatch(r'\d+(\.\d+)*([abc]|rc|\.post|\.dev)?\d*', ver_pj):
        problems.append('版本串 %r 不是合法 PEP 440（setuptools 会静默归一化，'
                        '导致与 __version__ 报两个号）' % ver_pj)

    notice_note = check_notice(cfg, problems)

    if problems:
        print('★打包清单与仓库不一致：')
        for p in problems:
            print('   - %s' % p)
        print('\n   改 %s 或 %s 后重跑本脚本。'
              % (os.path.basename(PYPROJECT), os.path.basename(NOTICE_PATH)))
        return 1

    print('✓ 打包清单与仓库一致：%d 个根模块 + packages=%s + 入口 memtether:main + 版本 %s'
          % (len(declared), packages, ver_pj))
    print('✓ .pyw 已显式登记（刻意不打包 %d 个：%s）'
          % (len(pyw), ', '.join(sorted(pyw)) or '无'))
    print('✓ NOTICE 归属声明完整（%s）' % (notice_note or '已登记'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
