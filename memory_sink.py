# -*- coding: utf-8 -*-
"""memory_sink.py — 已弃用，改为转发到 mem.py（2026-09-12）

原因：本脚本原本是「裸读-改-写」sink.json（无锁、无归属、无质检），
并发写会丢条目、垃圾会直接入库。现保留命令行兼容（旧调用不报错），
但 add/list 一律转发给带锁 + 质检 + 归属的 mem.py。

不要再直接用本脚本写中枢；新代码请走 `mem.py add`。
"""
import subprocess
import sys
import os

HUB = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HUB, 'mem.py')


def _forward(args):
    r = subprocess.run([sys.executable, MEM] + args)
    sys.exit(r.returncode)


def main():
    a = sys.argv[1:]
    if not a:
        # 打印提示，引导走 mem.py
        subprocess.run([sys.executable, MEM])
        sys.exit(0)
    if a[0] in ('add', 'list'):
        # 映射旧参数到 mem.py
        out = ['mem.py']
        # 简单透传（旧参数 --type/--text/--tag/--tail 与 mem.py 基本一致）
        _forward(a)
    else:
        _forward(a)


if __name__ == '__main__':
    main()
