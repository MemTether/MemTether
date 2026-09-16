# -*- coding: utf-8 -*-
"""hidden_run.pyw — 计划任务的「无窗口运行器」

为什么需要它：
    计划任务若直接用 `python.exe`（控制台子系统）作为动作，Task Scheduler 会在
    交互会话里创一个控制台窗口 → 每分钟/每5分钟闪一下黑窗。用 `pythonw.exe`
    启动本脚本即可完全无窗口；子进程再显式带上 CREATE_NO_WINDOW 双保险。

用法（任务动作里写）：
    <venv>\\Scripts\\pythonw.exe <HUB>\\hidden_run.pyw gateway.py process_events --limit 10
    <venv>\\Scripts\\pythonw.exe <HUB>\\hidden_run.pyw memory_maintenance.py

输出：追加到 <HUB>\\logs\\<脚本名>.log（含时间戳、参数、退出码），
      这样切了无窗口也**不丢诊断信息**。
"""
import os
import subprocess
import sys
import datetime

BASE = r'<HUB>'
PY = os.path.join(BASE, '.venv-memory', 'Scripts', 'python.exe')
LOG_DIR = os.path.join(BASE, 'logs')
CREATE_NO_WINDOW = 0x08000000          # 子进程不创建控制台窗口
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main():
    if len(sys.argv) < 2:
        return 2
    script = sys.argv[1]
    args = sys.argv[2:]

    os.makedirs(LOG_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(script))[0]
    # 日志按「脚本名_子命令」区分，避免 gateway.py 的多个子命令混进同一文件
    tag = stem + ('_' + args[0] if args and not args[0].startswith('-') else '')
    log = os.path.join(LOG_DIR, tag + '.log')

    cmd = [PY, os.path.join(BASE, script)] + args
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log, 'a', encoding='utf-8', errors='replace') as f:
        f.write('\n===== %s | %s =====\n' % (stamp, ' '.join([script] + args)))
        f.flush()
        try:
            p = subprocess.run(cmd, cwd=BASE, stdout=f, stderr=subprocess.STDOUT,
                               creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, timeout=540)
            f.write('[exit %s]\n' % p.returncode)
            return p.returncode
        except subprocess.TimeoutExpired:
            f.write('[TIMEOUT >540s]\n')
            return 3
        except Exception as e:                       # noqa: BLE001
            f.write('[ERROR %s: %s]\n' % (type(e).__name__, e))
            return 1


if __name__ == '__main__':
    sys.exit(main())
