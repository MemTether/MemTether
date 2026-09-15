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
import contextlib

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
                "source": {"type": "string", "default": "workbuddy", "description": "来源 agent 标识"},
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


def _quiet(fn, *a, **kw):
    """调用 gateway，吞掉它的 stdout，保证 MCP 通道干净。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return fn(*a, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _text(payload):
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _err(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def tool_add(args):
    r = _quiet(
        gateway.remember,
        args.get("content", ""),
        type=args.get("type", "fact"),
        source=args.get("source", "workbuddy"),
        tags=args.get("tags", ""),
        scope="shared",
    )
    if not r or not r.get("ok"):
        return _err("写入失败: %s" % (r or "unknown"))
    return _text({"ok": True, "uid": r.get("uid"), "op": r.get("op"),
                  "hint": "投影不会自动刷新，需要时跑 gateway.rebuild()"})


def tool_search(args):
    r = _quiet(gateway.search, args.get("query", ""), limit=int(args.get("limit", 8)))
    if not r or not r.get("ok"):
        return _err("检索失败: %s" % (r or "unknown"))
    out = []
    for it in (r.get("results") or [])[: int(args.get("limit", 8))]:
        if isinstance(it, dict):
            out.append({
                "uid": it.get("uid", ""),
                "type": it.get("type", ""),
                "score": round(float(it.get("score", 0) or 0), 3),
                "text": (it.get("content") or it.get("text") or "")[:400],
            })
        else:
            out.append({"text": str(it)[:400]})
    return _text({"engine": r.get("engine"), "count": len(out), "results": out})


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
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
