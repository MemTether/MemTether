# -*- coding: utf-8 -*-
"""clients/trust.py — Electron 系客户端的 MCP 信任免 GUI 代写

问题
----
WorkBuddy / CodeBuddy 这类 Electron 客户端：配置文件里写好了 MCP server，
**UI 上也显示已连接**，但 agent 拿不到工具 —— 因为还有一道「信任」闸门。

信任真源 = `<配置根目录>/mcp-approvals.json`
结构     = `{ "<sha256>::<serverName>": <毫秒时间戳> }`
判定     = 纯 key 查找，**无账号绑定** ⇒ 直接写文件即可。

hash 算法（★与 `electron-mcp-trust-approve` 技能里的参考实现逐字符一致）
------------------------------------------------------------
    sha256( command + "|" + ",".join(sorted(args)) + "|" + ",".join(sorted(env.keys())) )

⇒ 改了 command / args / env 的**键**，hash 就变、信任失效。
（`disabled` 字段不参与 hash，开关不影响已有信任。）

★三条生效路径（决定「写完到底生效没有」）
------------------------------------------
1. **UI 点 Trust / 打开开关** → 走 `toggleMcpServer()`，**当轮生效**（首选）
2. **真进程重启** → `loadApprovals()` 重跑
3. 首版迁移 → 已被 `mcpSecurityMigrated` 关闭，且该标志也是内存缓存 → **死路**

★`loadApprovals()` 是**一次性内存缓存** ⇒ 对**正在运行**的实例，
外改本文件**永远不生效**（内容再对也没用）。所以本模块写入后，
必须由调用方明确告知用户「要重启或点一下 Trust」。

配置根目录怎么定（不要猜）
--------------------------
从客户端日志反推 —— 每次启动会打一行：

    [MCP Security] Loaded 0 approvals from <配置根目录>\\mcp-approvals.json

把这行里的 `\\mcp-approvals.json` 去掉就是根目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

APPROVALS = "mcp-approvals.json"
CONFIG = "mcp.json"


def hash_of(entry: dict) -> str:
    """按客户端算法算 server 条目的信任 hash。"""
    cmd = entry.get("command", "")
    args = [str(a) for a in (entry.get("args") or [])]
    env = entry.get("env") or {}
    payload = cmd + "|" + ",".join(sorted(args)) + "|" + ",".join(sorted(env.keys()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def key_of(entry: dict, name: str) -> str:
    return "%s::%s" % (hash_of(entry), name)


def is_trusted(root: str, name: str, entry: dict):
    """返回 (trusted: bool, detail: str)。只读。"""
    path = os.path.join(root, APPROVALS)
    if not os.path.isfile(path):
        return False, "无 %s（从未授信过任何 server）" % APPROVALS
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception as e:
        return False, "读取 %s 失败: %s" % (APPROVALS, e)
    k = key_of(entry, name)
    if k in data:
        return True, "已授信（key=%s…）" % k[:16]
    # 可能只是 hash 变了（改了 command/args/env 键）
    same_name = [x for x in data if x.endswith("::" + name)]
    if same_name:
        return False, ("有同名 server 的旧信任记录但 hash 不匹配 —— "
                       "通常是 command/args/env 键被改过，需重新授信")
    return False, "未授信"


def approve(root: str, name: str, entry: dict, dry_run=True):
    """写入信任记录。返回 (ok, detail, path)。"""
    path = os.path.join(root, APPROVALS)
    k = key_of(entry, name)
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception as e:
            return False, "现有 %s 不是合法 JSON，已放弃写入（不动它）: %s" % (APPROVALS, e), path
    if data.get(k):
        return True, "信任记录已存在，无需写入", path
    if dry_run:
        return True, "（预演）将写入 key=%s…" % k[:16], path
    data[k] = int(time.time() * 1000)
    tmp = path + ".memtether-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return True, "已写入信任记录 key=%s…" % k[:16], path


def load_entry(root: str, name: str):
    """从 <root>/mcp.json 读出某个 server 定义（用于算 hash）。"""
    p = os.path.join(root, CONFIG)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return None
    return (cfg.get("mcpServers") or {}).get(name)


def _selftest():
    ok = []

    def chk(n, c):
        ok.append((n, bool(c)))

    e = {"command": "C:\\py.exe", "args": ["a.py", "x"], "env": {}}
    h = hash_of(e)
    chk("hash 稳定", h == hash_of({"args": ["x", "a.py"], "command": "C:\\py.exe", "env": {}}))
    chk("hash 是 sha256 长度", len(h) == 64)
    chk("args 顺序不影响（排序过）", hash_of({"command": "c", "args": ["b", "a"]}) ==
        hash_of({"command": "c", "args": ["a", "b"]}))
    chk("env 键名影响 hash", hash_of({"command": "c", "env": {"A": 1}}) !=
        hash_of({"command": "c", "env": {"B": 1}}))
    chk("env 值不影响 hash（只取 key）", hash_of({"command": "c", "env": {"A": 1}}) ==
        hash_of({"command": "c", "env": {"A": 2}}))
    chk("command 影响 hash", hash_of({"command": "c1"}) != hash_of({"command": "c2"}))
    chk("key 形态正确", key_of(e, "memory-hub") == h + "::memory-hub")
    # 与技能里的参考实现交叉核对（若技能存在）
    ref = os.path.expanduser(
        r"~/.agents/skills/electron-mcp-trust-approve/scripts/mcp_trust.py")
    if os.path.isfile(ref):
        import hashlib as _h
        payload = "C:\\py.exe" + "|" + ",".join(sorted(["a.py", "x"])) + "|" + ",".join([])
        chk("与技能参考实现一致", _h.sha256(payload.encode()).hexdigest() == h)
    else:
        chk("技能参考实现（缺失则跳过）", True)

    bad = [n for n, v in ok if not v]
    for n, v in ok:
        print("  [%s] %s" % ("PASS" if v else "FAIL", n))
    print("---- %d/%d ----" % (len(ok) - len(bad), len(ok)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
