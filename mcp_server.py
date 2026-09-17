# -*- coding: utf-8 -*-
"""
mcp_server.py — 记忆中枢的 MCP 接口（stdio 传输）

把 gateway.py 的 remember / search 包装成标准 MCP 工具，接口与 OpenMemory 同构：
    add_memories   / search_memory / list_memories / delete_memory(禁用)
这样 Cursor / Claude Desktop / 任何 MCP 客户端都能读写同一份记忆（真源 memory.db）。

设计约束：
  1. stdout **只能**输出 JSON-RPC，gateway 的 print 一律重定向到 stderr。
  2. 删除操作不开放（中枢的更正语义是 supersede，用 add_memories 覆盖，不做物理删除）。
  3. 无第三方依赖，纯标准库，随 Python 直接可跑。

由 MCP 客户端拉起，一般不需要手动运行。手动自测：
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp_server.py
"""
import sys
import os
import io
import json
import time
import threading
import contextlib

# ---- 输出编码加固（必须在任何输出之前执行）--------------------------------
# 客户端（Electron）拉起本进程时不会注入 PYTHONUTF8 / PYTHONIOENCODING，
# Windows 中文环境下 sys.std* 会退回 cp936(GBK)：server 吐 GBK 字节而
# 客户端按 UTF-8 解析 → 工具描述与结果全变乱码；同时 stdin 按 GBK 解码
# 会把客户端发来的 UTF-8 中文参数读坏。这里强制 UTF-8，与 MCP 规范一致。
# ★不要改用 mcp.json 的 env 来修 —— env 参与信任 hash 计算，一改就掉信任。
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    sys.stdout.reconfigure(newline="\n")
except Exception:
    pass

# ---- JSON-RPC 通道与「其它输出」彻底隔离 ------------------------------------
# ★踩坑记录（2026-09-16）：contextlib.redirect_stdout 是**进程级全局**的。
#   预热线程在后台一跑就是 30s+，这期间主线程写响应也会被重定向进 StringIO，
#   客户端一个字都收不到（表现为 initialize / tools/list 无限等待、最终判超时）。
#   所以：RPC 输出只认 _RPC_OUT 这个固定引用，谁 redirect 都影响不到它；
#   gateway / 三方库的 print 一律导去 stderr。
_RPC_OUT = sys.stdout
sys.stdout = sys.stderr

HUB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HUB)

import gateway  # noqa: E402

PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "memory-hub", "version": "1.0.0"}

TOOLS = [
    {
        "name": "add_memories",
        "description": (
            "写入一条记忆到共享记忆中枢（真源 memory.db，两个 WorkBuddy 版本与所有 MCP 客户端共用）。"
            "改记忆只走这里，禁止直接改 MEMORY.md / sink.json。"
            "重要：第一句必须把结论说完，后续 rebuild 生成导航投影时只取首句。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆正文，第一句写结论"},
                "type": {
                    "type": "string",
                    "enum": ["fact", "decision", "incident", "experience", "todo"],
                    "default": "fact",
                    "description": "fact 事实 / decision 决策 / incident 故障 / experience 经验教训",
                },
                "source": {"type": "string", "default": gateway.DEFAULT_SOURCE, "description": "来源 agent 标识"},
                "tags": {"type": "string", "default": "", "description": "逗号分隔标签，便于检索"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "检索记忆中枢。返回结果是摘要片段，取全文请记下 uid 后再用本工具按关键词细查，"
            "或直接在 memory_hub 目录执行：python mem.py search \"<关键词>\"。"
            "下全称否定结论前必须先搜一次。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，需自包含（不要写『那个东西』）"},
                "limit": {"type": "integer", "default": 8, "description": "返回条数"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_memories",
        "description": "列出最近的 active 记忆（默认 20 条，仅首句摘要）。用于快速浏览中枢里有什么。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "type": {
                    "type": "string",
                    "enum": ["fact", "decision", "incident", "experience", "todo"],
                    "description": "可选，按类型过滤",
                },
            },
        },
    },
]


# gateway 的向量栈不是线程安全的，所有经过它的调用串行化。
_GW_LOCK = threading.Lock()


def _quiet_nolock(fn, *a, **kw):
    """不取锁版本 —— 只给纯 SQLite 的降级路径用，免得被长时间预热堵死。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return fn(*a, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _quiet(fn, *a, **kw):
    """调用 gateway：吞掉它的 stdout 保证 MCP 通道干净，并串行化以免并发踩坏向量库。"""
    with _GW_LOCK:
        return _quiet_nolock(fn, *a, **kw)


def _text(payload):
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _err(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _log(msg):
    """诊断日志一律走 stderr —— 客户端会把它收进主线程日志；stdout 只能放 JSON-RPC。"""
    try:
        sys.stderr.write("[mcp_server] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


# ---- 预热：把模型冷启动的代价挪到「客户端还没来调用」的空档里 ---------------
# 实测首次检索要加载 bge-m3 int8（热态 ~2.2s，磁盘冷读更久），而客户端对
# tools/call 的超时是 5s —— 一超时就判 "Connection closed" 并把进程杀掉，
# 且不会再自动拉起。server 通常比第一次调用早 10s 以上被 spawn，
# 所以「启动即后台预热」就能把首次调用压回毫秒级。
_WARM = {"done": False, "secs": None, "err": None}
_WARM_THREAD = None


def _warmup():
    t0 = time.time()
    try:
        r = _quiet(gateway.search, "预热", limit=1)
        _WARM["done"] = bool(isinstance(r, dict) and r.get("ok"))
        if not _WARM["done"]:
            _WARM["err"] = str(r)[:200]
    except Exception as e:
        _WARM["err"] = "%s: %s" % (type(e).__name__, e)
    finally:
        _WARM["secs"] = round(time.time() - t0, 2)
        _log("warmup %s in %.2fs %s" % ("ok" if _WARM["done"] else "FAILED",
                                        _WARM["secs"], _WARM["err"] or ""))


def _warm_pending():
    """预热是否仍在进行中（尚未结束）。"""
    return _WARM["secs"] is None


def tool_add(args):
    if _warm_pending():
        return _err(
            "记忆中枢的向量索引正在预热（冷启动实测 30s 以上，远超客户端 5s 超时上限），"
            "此刻写入会被打断。请约 30 秒后重试同一次写入；"
            "检索类调用不受影响（此时自动走关键词降级，返回结果里带 degraded 标记）。")
    r = _quiet(
        gateway.remember,
        args.get("content", ""),
        type=args.get("type", "fact"),
        source=args.get("source", gateway.DEFAULT_SOURCE),
        tags=args.get("tags", ""),
        scope="shared",
    )
    if not r or not r.get("ok"):
        return _err("写入失败: %s" % (r or "unknown"))
    # 写入后自动重建投影（实测 rebuild 仅 ~0.05s，不必留给调用方手动触发）
    refreshed = False
    try:
        rb = _quiet(gateway.rebuild)
        refreshed = bool(rb is None or rb.get("ok", True))
    except Exception:
        refreshed = False
    return _text({"ok": True, "uid": r.get("uid"), "op": r.get("op"),
                  "projection_refreshed": refreshed,
                  "hint": "" if refreshed else "投影未刷新，需手动跑 gateway.rebuild()"})


def tool_search(args):
    q = args.get("query", "")
    lim = int(args.get("limit", 8))
    degraded = False
    if _warm_pending():
        # 向量索引还在预热（冷启动实测 30s+，远超客户端 5s 超时）→ 先用纯 SQLite
        # 关键词路径给结果，保证这次调用不超时被杀；结果里明确标注 degraded，
        # 调用方稍后重试同一查询即可拿到完整混合检索。
        _log("search -> keyword fallback (warmup pending)")
        r = _quiet_nolock(gateway._search_like_legacy, q, lim)
        degraded = True
    else:
        r = _quiet(gateway.search, q, limit=lim)
    if not r or not r.get("ok"):
        return _err("检索失败: %s" % (r or "unknown"))
    out = []
    for it in (r.get("results") or [])[:lim]:
        if isinstance(it, dict):
            out.append({
                "uid": it.get("uid", ""),
                "type": it.get("type", ""),
                "score": round(float(it.get("score", 0) or 0), 3),
                "text": (it.get("content") or it.get("text") or "")[:400],
            })
        else:
            out.append({"text": str(it)[:400]})
    payload = {"engine": r.get("engine"), "count": len(out), "results": out}
    if degraded:
        payload["degraded"] = True
        payload["note"] = ("向量索引正在预热（冷启动约 30 秒），本次为关键词检索、可能漏召回；"
                           "稍后重试同一查询即可获得完整混合检索结果。")
    return _text(payload)


def tool_list(args):
    limit = int(args.get("limit", 20))
    tf = args.get("type")
    conn = gateway.get_conn()
    try:
        if tf:
            rows = conn.execute(
                "SELECT uid,type,source,substr(content,1,120) AS c,updated_at "
                "FROM facts WHERE status='active' AND type=? "
                "ORDER BY updated_at DESC LIMIT ?", (tf, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT uid,type,source,substr(content,1,120) AS c,updated_at "
                "FROM facts WHERE status='active' ORDER BY updated_at DESC LIMIT ?",
                (limit,)).fetchall()
        items = [{"uid": r["uid"], "type": r["type"], "source": r["source"],
                  "text": r["c"], "updated": (r["updated_at"] or "")[:16]} for r in rows]
        return _text({"count": len(items), "items": items})
    except Exception as e:
        return _err("列出失败: %s: %s" % (type(e).__name__, e))
    finally:
        conn.close()


HANDLERS = {
    "add_memories": tool_add,
    "search_memory": tool_search,
    "list_memories": tool_list,
}


def handle(req):
    mid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"protocolVersion": PROTOCOL,
                            "capabilities": {"tools": {}},
                            "serverInfo": SERVER_INFO}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = (req.get("params") or {}).get("name")
        args = (req.get("params") or {}).get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "result": _err("未知工具: %s" % name)}
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": fn(args)}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err("%s: %s" % (type(e).__name__, e))}
    if method in ("notifications/initialized", "initialized") or method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def main():
    global _WARM_THREAD
    _log("started pid=%d rpc_encoding=%s" % (os.getpid(), getattr(_RPC_OUT, "encoding", "?")))
    _WARM_THREAD = threading.Thread(target=_warmup, name="warmup", daemon=True)
    _WARM_THREAD.start()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _log("bad json: %s" % e)
            continue
        t0 = time.time()
        resp = handle(req)
        dt = time.time() - t0
        if req.get("method") == "tools/call":
            _log("tools/call %s -> %.2fs (warm=%s)" % (
                (req.get("params") or {}).get("name"), dt,
                "pending" if _warm_pending() else _WARM["done"]))
        if resp is None:
            continue
        _RPC_OUT.write(json.dumps(resp, ensure_ascii=False) + "\n")
        _RPC_OUT.flush()
    _log("stdin closed, exiting")


if __name__ == "__main__":
    main()
