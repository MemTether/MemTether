#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interpreter.py —— 子进程该用哪个 Python：统一解析，**绝不静默降级**。

【为什么需要它（2026-09-18 实测踩到）】
  本项目有两种形态，而两者对「解释器在哪」的答案**不一样**：
    ① 源码形态（本机中枢目录 / 开发中的发布库）
       → 有 `.venv-memory/Scripts/python.exe`（chromadb / onnxruntime / tokenizers 装在里面）
    ② pip 安装形态，或**从 git clone 出来的发布库**
       → **没有** `.venv-memory`（`.gitignore` 里 `.venv-*/` 把它排除了）

  而多个脚本原先各自写死
      PY = os.path.join(HERE, '.venv-memory', 'Scripts', 'python.exe')
  再交给 `subprocess.run([PY, ...])`。于是在形态②下直接：
      FileNotFoundError: [WinError 2] 系统找不到指定的文件
  —— 发布库 README 里「卷子可自行复跑」这句，在 clone 出来的仓库上**根本不成立**。
  实证：2026-09-18 在发布库里跑 qvalue_ab.py 即崩于此（hard_bench.py 第 33 行）。

【为什么不能「找不到就退回 sys.executable」了事】
  本项目的检索依赖 chromadb / numpy。用缺依赖的解释器跑**不会报错**，
  只会静默降级成纯关键词检索（历史上「66 条资产查不到、白查一天」的坑）。
  评测脚本若这么退，会产出一份**看着跑通了、其实测的是别的东西**的分数 ——
  这比直接崩掉更坏，因为它会被人当真。所以本模块的契约是：
      **要么给出一个「存在且依赖齐备」的解释器，要么抛错；
        绝不给一个悄悄残废的。**

【优先级】（先命中先赢）
  1. 环境变量 `MEM_PY`          —— 显式指定，最高优先（CI / 多环境 / 排障）
                                  ★fail-closed：设了就必须存在，指错直接报错，
                                    不会"悄悄改用别的解释器"
  2. 本目录 `.venv-memory/...`  —— 源码形态；**行为与改动前完全一致**
  3. 当前解释器 `sys.executable` —— pip 安装形态（跑起来的那个通常依赖齐）
  都不行 → RuntimeError，并把三条出路写进错误里。

【调用方怎么用】
    from interpreter import resolve_python, require_modules
    PY, PY_SRC = resolve_python(HERE, announce=True)   # 非源码形态会打一行醒目提示
    subprocess.run([PY, ...])
    # 需要「依赖不齐就别跑」的评测脚本，再加一句：
    require_modules(PY, 'chromadb', 'numpy')

  说明：本模块只管**选**解释器与**验**依赖，不管调用参数。
  `memsearch._interp_hint()` 是「给用户的提示文案」，与本模块是两个用途，
  但判据同源（那个解释器真的存在才给路径）。
"""
import os
import sys
import subprocess

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))


def venv_python(here=None):
    """源码形态下的约定路径（**不保证存在**，存在性由 resolve_python 判）。"""
    return os.path.join(here or _SELF_DIR, '.venv-memory', 'Scripts', 'python.exe')


def resolve_python(here=None, allow_self=True, announce=False):
    """挑一个真能用的解释器，返回 (绝对路径, 来源说明)。找不到就抛 RuntimeError。

    here     : 以哪个目录为「本目录」去找 .venv-memory（默认本文件所在目录）
    allow_self: 是否允许退回 sys.executable（pip 安装形态需要；CI 里想强制指定就设 False）
    announce : True 时，若最终不是走 .venv-memory，往 stderr 打一行提示。
               ★为什么要有这行：退回 sys.executable 是**唯一**可能悄悄残废的路径，
                 必须让人看得见，否则又变成"安静地跑出不可信的分数"。
    """
    here = here or _SELF_DIR
    env_py = os.environ.get('MEM_PY')

    # ★MEM_PY 是 fail-closed 的：显式设了就必须用它，指错了就报错。
    #   理由与整个模块同源 —— 「以为在用 A、其实在用 B」是最坏的失败形态。
    #   （实测踩到：设了 MEM_PY 指向不存在的路径，函数默默退回 .venv-memory 并返回成功，
    #     调用方完全不知道自己的指定被忽略了。2026-09-18 由 test_interpreter_resolve 抓出。）
    if env_py:
        if os.path.isfile(env_py):
            if announce:
                sys.stderr.write('[interpreter] 按 MEM_PY 指定使用 %s\n' % env_py)
            return env_py, 'MEM_PY 环境变量'
        raise RuntimeError(
            'MEM_PY 指向的解释器不存在：%s\n'
            '  它是**显式指定**，所以这里不替你猜 —— 要么改对，要么清掉这个环境变量\n'
            '  （set MEM_PY= 或从环境里删掉）再让它自己找。\n' % env_py)

    cands = [('本目录 .venv-memory（源码形态）', venv_python(here))]
    if allow_self:
        cands.append(('当前解释器 sys.executable', sys.executable))

    for label, path in cands:
        if path and os.path.isfile(path):
            if announce and 'venv-memory' not in label:
                sys.stderr.write(
                    '[interpreter] 本目录没有 .venv-memory（源码形态），改用当前解释器 %s\n'
                    '              这是**唯一**可能悄悄残废的路径：若它缺 chromadb / numpy，\n'
                    '              检索会静默降级成纯关键词，分数与结果都不可信。\n'
                    '              要确认就设 MEM_PY=<你的 python.exe>，或先建 .venv-memory。\n'
                    % path)
            return path, label

    raise RuntimeError(
        '找不到可用的 Python 解释器 —— 试过：\n'
        + ''.join('    · %s = %s\n' % (l, p) for l, p in cands)
        + '  出路（任选一条）：\n'
          '    · 显式指定：set MEM_PY=<你的 python.exe 绝对路径>\n'
          '    · 建源码环境：python -m venv .venv-memory 后装齐依赖\n'
          '    · pip 安装形态：pip install "memtether[vector]"\n')


def require_modules(py, *modules):
    """探测 py 里这些模块能不能找到；缺了直接抛错（**不静默降级**）。

    为什么值得多这一步：本项目最贵的失败形态不是崩溃，而是
    「缺依赖 → 静默降级 → 结果看着正常其实错」。评测脚本尤其不能容忍 ——
    一份不可信的分数会被人当真，比报错坏得多。

    实现用 importlib.util.find_spec（只查得到性，**不真加载**），
    所以开销是毫秒级，不触发 ONNX / 向量栈的初始化。
    """
    if not modules:
        return
    code = ('import importlib.util, sys\n'
            'miss = [m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]\n'
            'print(",".join(miss))\n')
    try:
        r = subprocess.run([py, '-c', code] + list(modules),
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        raise RuntimeError('探测解释器失败（%s）：%s' % (py, e))
    if r.returncode != 0:
        raise RuntimeError('解释器跑不起来（%s）：\n%s' % (py, (r.stderr or '')[-400:]))
    miss = [m for m in (r.stdout or '').strip().split(',') if m]
    if miss:
        raise RuntimeError(
            '解释器 %s 缺这些模块：%s\n'
            '  本项目的检索依赖它们。缺了**不会报错**，只会静默降级成纯关键词检索，\n'
            '  于是检索结果与评测分数都不可信 —— 所以这里直接停下。\n'
            '  装齐后重跑：pip install "memtether[vector]"\n'
            '  或用源码形态的解释器：%s\n'
            % (py, ', '.join(miss), venv_python()))
