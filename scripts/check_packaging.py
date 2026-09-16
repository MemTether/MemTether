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

用法
----
    python scripts/check_packaging.py

退出码：0 = 一致；1 = 有差异；2 = 读不到配置 / 不在 git 仓库
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYPROJECT = os.path.join(ROOT, 'pyproject.toml')


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
                       capture_output=True, text=True)
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

    if problems:
        print('★打包清单与仓库不一致：')
        for p in problems:
            print('   - %s' % p)
        print('\n   改 %s 后重跑本脚本。' % PYPROJECT)
        return 1

    print('✓ 打包清单与仓库一致：%d 个根模块 + packages=%s + 入口 memtether:main + 版本 %s'
          % (len(declared), packages, ver_pj))
    return 0


if __name__ == '__main__':
    sys.exit(main())
