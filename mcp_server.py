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

_T_IMPORT = time.time()
import gateway  # noqa: E402
_IMPORT_SECS = time.time() - _T_IMPORT

PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "memory-hub", "version": "1.0.0"}

# ★默认来源：可被 MEM_DEFAULT_SOURCE 覆盖（2026-09-19）。
#   背景：gateway/mcp_server 原先都把默认来源硬编码成 "workbuddy"，
#   导致**不显式传 source 的写入全被记成国内版**（国际版写的也记成 workbuddy）
#   ⇒ 跨客户端归属不可信，污染一切"谁在用什么"的评测结论。
#   正解：各客户端在自己的 mcp.json 的 env 里设自己的来源，例如
#     "env": {"MEM_DEFAULT_SOURCE": "workbuddy_ai"}
# 兜底来源：识别不出客户端时用哪个名字。默认中性值 `local` —— 开源发布时
# 陌生人装完不会把记忆归到一个他不认识的名字下。本机部署可用环境变量覆盖：
#     export MEM_FALLBACK_SOURCE=workbuddy
_FALLBACK_SOURCE = ((os.environ.get("MEM_FALLBACK_SOURCE") or "local").strip()
                    or "local")
_DEFAULT_SOURCE = ((os.environ.get("MEM_DEFAULT_SOURCE") or _FALLBACK_SOURCE).strip()
                   or _FALLBACK_SOURCE)


# ---- 客户端来源识别（2026-09-20 通用化）------------------------------------
# 目的：让本 server 在**零配置**下知道自己是被哪个客户端拉起的，从而把写入
# 归属记到正确的来源名下（模型不传 source 时也不串号）。
#
# 匹配策略：两级，都是「子串匹配 + 按匹配串长度降序」（长的先试，
# 保证 `workbuddyai` 先于 `workbuddy` 命中）。
#   ① 父进程**映像全路径** —— 覆盖 Electron 系客户端
#      （WorkBuddy / ZCode / Tabbit 等，可执行文件路径里带自己的名字）。
#   ② 父进程**命令行**（读 PEB）—— 覆盖「被通用宿主拉起」的情况：
#      dsh / Claude Code 的父进程就是普通 node.exe，只有命令行里才带
#      `@deepseek-ai/dsh` / `claude-code`。实测 2026-09-20：Tabbit 用的是
#      `<TABBIT>\TabbitDance\node.exe`（路径含 tabbit，①就能命中），
#      而独立 dsh 用的是 `C:\Program Files\nodejs\node.exe`（必须靠 ②）。
#
# 为什么靠父进程而不是 mcp.json 的 env：
#   env 的 key 集合参与信任 hash（sha256(command|sorted(args)|sorted(env keys))），
#   多一个 key 就掉信任，而掉信任后 server 会被客户端**静默跳过**
#   （只有日志里一行 skipping untrusted）。所以能自动就别让用户配。
#
# 想加自己的客户端：在同目录放 client_signatures.json，
#     {"signatures": [["myclient", "mysource"]]}
# 会与内置表合并（同名覆盖）。
#
# ⚠️ 父进程是**直接**父进程（2026-09-19 实测）：国内版 mcp_server ← WorkBuddy.exe，
#    国际版 mcp_server ← WorkBuddyAI.exe，中间没有 CLI 壳。所以本函数只查一层。
_BUILTIN_SIGNATURES = (
    ("workbuddyai", "workbuddy_ai"),      # ★长的必须在前：workbuddyai 含 workbuddy
    ("codebuddyai", "workbuddy_ai"),
    ("workbuddy", "workbuddy"),
    ("codebuddy", "workbuddy"),
    ("zcode", "zcode"),
    ("tabbit", "tabbit"),
    ("openclaw", "openclaw"),
    ("cursor", "cursor"),
    ("windsurf", "windsurf"),
    ("trae", "trae"),
    ("cline", "cline"),
    ("claude", "claude_code"),
    ("codex", "codex"),
    ("deepseek-ai/dsh", "dsh"),
    ("dsh", "dsh"),
)

_SIG_CACHE = {"loaded": False, "table": ()}


def _load_signatures():
    """内置表 + 可选 client_signatures.json（同目录），按匹配串长度降序。"""
    if _SIG_CACHE["loaded"]:
        return _SIG_CACHE["table"]
    table = dict(_BUILTIN_SIGNATURES)
    try:
        f = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "client_signatures.json")
        if os.path.isfile(f):
            with open(f, "r", encoding="utf-8") as fh:
                extra = (json.load(fh) or {}).get("signatures") or []
            for item in extra:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    table[str(item[0]).lower()] = str(item[1])
    except Exception:
        pass
    _SIG_CACHE["table"] = tuple(sorted(table.items(), key=lambda kv: -len(kv[0])))
    _SIG_CACHE["loaded"] = True
    return _SIG_CACHE["table"]


def _match_signature(text):
    """在 text（路径或命令行）里找客户端签名；命中返回来源名，否则 None。"""
    if not text:
        return None
    low = text.lower().replace(" ", "")
    for token, source in _load_signatures():
        if token.replace(" ", "") in low:
            return source
    return None


def _proc_image_path(pid):
    """进程映像全路径。失败返回 None（≠ 进程不存在）。"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(2048)
            n = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return None
            return buf.value
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _read_mem(h, addr, size):
    """读目标进程内存。失败返回 None。"""
    import ctypes
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not ctypes.WinDLL("kernel32").ReadProcessMemory(
            h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return None
    return buf.raw[: got.value]


def _proc_cmdline(pid):
    """进程命令行（读 PEB）。失败返回 None —— 注意「读不到」≠「进程不存在」。"""
    try:
        import ctypes
        import struct
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        class _PBI(ctypes.Structure):
            _fields_ = [("Reserved1", ctypes.c_void_p),
                        ("PebBaseAddress", ctypes.c_void_p),
                        ("Reserved2", ctypes.c_void_p * 2),
                        ("UniqueProcessId", ctypes.c_void_p),
                        ("Reserved3", ctypes.c_void_p)]

        # QUERY_INFORMATION | VM_READ
        h = k32.OpenProcess(0x0400 | 0x0010, False, int(pid))
        if not h:
            return None
        try:
            pbi = _PBI()
            ret = ctypes.c_ulong(0)
            if ntdll.NtQueryInformationProcess(
                    h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi),
                    ctypes.byref(ret)) != 0:
                return None
            if not pbi.PebBaseAddress:
                return None
            raw = _read_mem(h, pbi.PebBaseAddress + 0x20, 8)   # PEB->ProcessParameters
            if not raw:
                return None
            params = struct.unpack("<Q", raw)[0]
            if not params:
                return None
            # RTL_USER_PROCESS_PARAMETERS.CommandLine 在 +0x70（UNICODE_STRING）
            us = _read_mem(h, params + 0x70, 16)
            if not us:
                return None
            length = struct.unpack("<H", us[0:2])[0]
            ptr = struct.unpack("<Q", us[8:16])[0]
            if not ptr or not length or length > 8192:
                return None
            data = _read_mem(h, ptr, length)
            return data.decode("utf-16-le", "replace") if data else None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _detect_client_source(pid=None):
    r"""推断本 server 是被哪个客户端拉起的；识别不出返回 None。

    pid=None 时取 os.getppid()；显式传 pid 只为**可测试性**
    （单测要拿真进程验映射，不能只靠字符串逻辑自证）。

    两级：① 父进程映像路径 -> ② 父进程命令行（覆盖 node.exe 这类通用宿主）。
    """
    if os.name != "nt":
        return None
    try:
        target = os.getppid() if pid is None else int(pid)
        return (_match_signature(_proc_image_path(target))
                or _match_signature(_proc_cmdline(target)))
    except Exception:
        return None


_SRC = {"resolved": None, "env": None, "auto": None, "logged": False}


def _default_source():
    """实际写入用的来源。优先级：MEM_DEFAULT_SOURCE(env) > 父进程识别 > _FALLBACK_SOURCE。

    ★懒解析：进程识别要开句柄，只在本函数首次被调用时做一次（缓存）。
    调用点有两处：① TOOLS 里的 source.default（**模块导入期**，为了让 schema 对国际版
    不撒谎）；② _ensure_started()，即 initialize 应答已 flush 之后。
    ⚠ 因为 ① 在导入期跑，此时 `_log` 尚未定义（它在 TOOLS 之后）⇒ 日志调用必须
    用 globals() 探测，否则 import 直接 NameError（2026-09-19 实测踩到）。
    ⚠ 又因为 ① 已经把缓存填满了，②再进来会走早返回 —— 所以「补日志」必须放在
    早返回**之外**，用 logged 标记保证恰好输出一次；否则那句 resolved 诊断行
    会永远不出现（2026-09-19 实测：G 断言 FAIL 就是这么来的）。
    """
    if _SRC["resolved"] is None:
        env = (os.environ.get("MEM_DEFAULT_SOURCE") or "").strip()
        auto = None if env else _detect_client_source()
        _SRC["resolved"] = env or auto or _FALLBACK_SOURCE
        _SRC["env"] = os.environ.get("MEM_DEFAULT_SOURCE")
        _SRC["auto"] = auto
    if not _SRC["logged"]:
        _lg = globals().get("_log")
        if _lg:
            _lg("resolved default source = %s (env=%r auto=%r)"
                % (_SRC["resolved"], _SRC["env"], _SRC["auto"]))
            _SRC["logged"] = True
    return _SRC["resolved"]

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
                "source": {"type": "string", "default": _default_source(),
                           "description": "来源 agent 标识；不传则由本客户端自动识别"
                                          "（MEM_DEFAULT_SOURCE 环境变量 > 父进程识别 > %s）"
                                          % _FALLBACK_SOURCE},
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
    {
        "name": "feedback",
        "description": "回写 Q-Value 信号：告诉中枢某条记忆是否被采纳。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "记忆条目的 uid"},
                "reward": {"type": "number", "default": 1.0, "description": "1.0=完全采纳 / 0.5=部分有用 / 0.0=没用"},
                "agent": {"type": "string", "default": "", "description": "调用方标识"},
                "detail": {"type": "string", "default": "", "description": "备注"},
            },
            "required": ["uid"],
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


# ---- 来源校验的软着陆（2026-09-19）------------------------------------------
# gateway._guard_source() 对未注册来源是 `raise SystemExit`，而 SystemExit
# **不被** `except Exception` 捕获 ⇒ 一旦模型或配置传了未注册的 source，
# 本长驻进程会**直接退出**（客户端侧表现为 MCP 工具静默消失，且不会自动拉起）。
# 这里注册一个「已注册的兜底来源」：未注册时回退 + 记 warning，服务不死。
# ★只在 fallback 值**本身已注册**时生效；否则 gateway 仍会硬报错（防绕过校验）。
try:
    _reg = gateway._known_sources()
    _fb = os.environ.get("MEM_SOURCE_FALLBACK") or "workbuddy"
    if _reg and _fb not in _reg:
        _fb = sorted(_reg)[0]
    os.environ["MEM_SOURCE_FALLBACK"] = _fb
    _log("source fallback = %s (registered=%d)" % (_fb, len(_reg)))
    _log("default source = %s (MEM_DEFAULT_SOURCE=%r)"
         % (_DEFAULT_SOURCE, os.environ.get("MEM_DEFAULT_SOURCE")))
except Exception as _e:
    os.environ.setdefault("MEM_SOURCE_FALLBACK", "workbuddy")
    _log("source fallback setup failed: %s" % _e)


# ---- 预热：把模型冷启动的代价挪到「客户端还没来调用」的空档里 ---------------
# 实测首次检索要加载 bge-m3 int8（热态 ~2.2s，磁盘冷读更久）。
# ★判据更正（2026-09-19 取证）：客户端对 tools/call **没有 5s 超时** ——
#   McpUtils.getToolTimeout() 读 MCP_TOOL_TIMEOUT，缺省 `let eE=1e8`（≈27.8 小时）。
#   原先写的「5s 超时即杀进程」是臆断，已证伪；此处保留预热是为了**让 Agent 少等**，
#   不是因为会被杀。真正的硬窗口是**首个会话的 MCP prewait = 2000ms**
#   （resolveFirstRunMcpPrewaitTimeoutMs，rT=2e3）：超时只 abort 等待、不报错，
#   后果是**该会话第一轮看不到 memory-hub 工具**。见 main() 的延迟启动设计。
#
# ★★★ 2026-09-19 修复：原实现让 MCP 通道「从第一天起就是坏的」★★★
# 缺陷：本进程被客户端 spawn 后，主线程阻塞在 `for line in sys.stdin`（管道读）。
#   此时若由**后台预热线程**首次 import numpy / chromadb（底层
#   <frozen importlib._bootstrap_external>:1317 in create_module 加载 C 扩展 .pyd），
#   会永久死锁。faulthandler 实测 25s / 50s 两次 dump 栈完全一致、零进展，
#   `_WARM["secs"]` 永远是 None。后果连锁：
#     · add_memories 被**永久拒绝** → 写入通道等于不存在
#     · search_memory 被**永久降级**成纯 SQLite LIKE（中文整串匹配，实测 count:0）
#   ⇒ Agent 试一次「MCP 不能用」就永久退回 CLI，MCP 通道形同虚设。
#   最小复现（与 import 哪个模块无关，只与「主线程阻塞 stdin」有关）：
#       主线程 time.sleep   → import numpy 0.07s / import chromadb 0.69s  ✅
#       主线程读 stdin 管道 → 两者均 HANG > 60s                        ❌
# 修复两件事：
#   ① 重库 import 搬回主线程（见 _preload_heavy）—— 消掉死锁的触发组合；
#   ② 预热加硬超时（见 _warm_state）—— 万一将来又卡住，也只会降级，不再永久拒绝。
_WARM = {"done": False, "secs": None, "err": None, "t0": None, "lite": False}
_WARM_THREAD = None
_WARM_HARD_TIMEOUT = 25.0   # 秒；超过则不再等预热，直接放行（宁降级、不永久拒绝）


def _preload_heavy():
    """★主线程预加载重库的 C 扩展 —— 必须在起预热线程之前、进 stdin 循环之前执行。

    见上方 2026-09-19 修复说明：把 `.pyd` 的 create_dynamic 从
    「后台线程 + 主线程阻塞 stdin」这个必死组合里挪出来。实测 0.74s；
    任一 import 失败也不致命（走降级路径）。

    ★判据更正（2026-09-19 从客户端产物取证，推翻早前臆断）：
      · tools/call **没有 5s 超时** —— codebuddy-headless.js 的
        `McpUtils.getToolTimeout()` 默认 `1e8` ms（≈27.8 小时）。
      · 真正紧的窗口是**首个会话的 MCP prewait = 2000ms**
        （`resolveFirstRunMcpPrewaitTimeoutMs()` → `rT=2e3`）；
        越线只放弃等待、不报错，但**第一轮看不到本 server 的工具**
        ⇒ 正是「Agent 不知道有这个工具」的场景。
      ⇒ 所以本函数不再在进程启动时抢跑，改为 `initialize` **应答发出之后**
        由 `_ensure_started()` 触发（见 main）。
    """
    t0 = time.time()
    for _m in ("numpy", "chromadb", "onnxruntime"):
        try:
            __import__(_m)
        except Exception as e:
            _log("preload %s failed: %s: %s" % (_m, type(e).__name__, e))
    _log("preload heavy libs in %.2fs" % (time.time() - t0))


# ★一次性启动闸门（2026-09-19）：预加载 + 预热线程从「进程启动时」推迟到
#   「initialize 应答之后」。原因见 _preload_heavy 的判据更正：
#   客户端首个会话只给 2000ms prewait，而进程启动到 initialize 应答原本要 ~1.5s
#   （其中 ~0.8s 是这里的重库 import）—— 余量仅 0.45s，机器一忙就掉工具。
#   ★红线：仍然必须在**主线程**执行（不得丢给后台线程），否则复现 import 死锁。
_STARTED = False


def _ensure_started():
    """主线程一次性启动：预加载重库 → 起预热线程。幂等。"""
    global _WARM_THREAD, _STARTED
    if _STARTED:
        return
    _STARTED = True
    _preload_heavy()
    # 来源识别放在这里：此时 initialize 应答已 flush，开句柄的代价不影响握手
    try:
        _default_source()
    except Exception as _e:
        _log("default source resolve failed: %s" % _e)
    _WARM_THREAD = threading.Thread(target=_warmup, name="warmup", daemon=True)
    _WARM_THREAD.start()


def _warmup():
    _WARM["t0"] = time.time()
    t0 = _WARM["t0"]
    # ★阶段 1「轻量预热」：只建一次连接（毫秒级）就置位 _WARM["lite"] ——
    #   写入路径只等这个，不等下面的重模型加载。
    #   否则冷启动首次 add 会被"预热中"拒回，而 Agent 通常不会重试。
    try:
        _c = gateway.get_conn()
        _c.close()
    except Exception as e:
        _log("lite warmup failed: %s: %s" % (type(e).__name__, e))
    _WARM["lite"] = True
    _log("lite warmup done in %.2fs" % (time.time() - t0))
    # ★阶段 2「重模型预热」：真正加载 embedding 模型（检索路径需要）。
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


def _warm_state():
    """'done'（预热完成）| 'pending'（仍在预热，值得等）| 'timeout'（等太久，放行）。"""
    if _WARM["secs"] is not None:
        return "done"
    t0 = _WARM["t0"]
    if t0 is not None and (time.time() - t0) > _WARM_HARD_TIMEOUT:
        return "timeout"
    return "pending"


def _warm_pending():
    """预热是否仍在进行中**且仍值得等**（超时后返回 False，不再永久卡住调用方）。"""
    return _warm_state() == "pending"


def _wait_warm(deadline_s):
    """阻塞等预热结束，最多 deadline_s 秒；返回等待后的状态。

    ★为什么值得等：修好死锁后实测预热只要 1.84s，而客户端是
    `initialize` → 立刻 `tools/call` 的节奏，硬拒会白白丢掉一次写入。
    轮询而非 join：本模块可能被 `import` 后手动驱动（自检脚本），此时没有线程对象。
    """
    t0 = time.time()
    while _WARM["secs"] is None and (time.time() - t0) < deadline_s:
        time.sleep(0.05)
    return _warm_state()


def tool_add(args):
    # ★2026-09-19：写入**不依赖 embedding 模型**（remember 只写 SQLite；向量入库失败
    #   有 _vec_upsert 的 try/except 兜底，下次 rebuild 会补齐）。所以这里**只等轻量预热**
    #   （建连接，毫秒级），不再等重模型加载 —— 否则冷启动首次 add 会被"预热中"拒回，
    #   而 Agent 看到"请 30 秒后重试"通常不会重试 ⇒ 写入通道在真实使用中仍不可靠。
    if not _WARM["lite"]:
        t0 = time.time()
        while not _WARM["lite"] and (time.time() - t0) < 2.0:
            time.sleep(0.02)
    st = _warm_state()
    # ★预热未完成时改用 _quiet_nolock：预热线程可能正持着 _GW_LOCK 做重模型加载，
    #   去取锁会把本次调用拖过客户端超时 —— 比"降级写入"更糟。
    #   remember 的跨进程安全由 gateway 内的 hubguard 锁保证，此处不取 MCP 进程内锁是安全的。
    call = _quiet if st == "done" else _quiet_nolock
    if st != "done":
        _log("add -> proceeding without full warmup (lite=%s, state=%s)"
             % (_WARM["lite"], st))
    r = call(
        gateway.remember,
        args.get("content", ""),
        type=args.get("type", "fact"),
        source=(args.get("source") or _default_source()),
        tags=args.get("tags", ""),
        scope="shared",
    )
    if not r or not r.get("ok"):
        return _err("写入失败: %s" % (r or "unknown"))
    # 写入后自动重建投影（实测 rebuild 仅 ~0.05s，不必留给调用方手动触发）
    refreshed = False
    try:
        rb = call(gateway.rebuild)
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
    st = _warm_state()
    if st == "pending":
        # 等 2s 拿完整混合检索，比立刻降级成中文召回很差的关键词路径更划算
        # （预热实测 1.84s）。★tools/call 实际无超时（MCP_TOOL_TIMEOUT 默认 1e8 ms），
        # 这里限 2s 纯粹是为了「让 Agent 少等」，不是因为会被杀。
        st = _wait_warm(2.0)
    if st != "done":
        # 向量索引还在预热（冷启动实测 30s+）→ 先用纯 SQLite
        # 关键词路径给结果，保证这次调用不超时被杀；结果里明确标注 degraded，
        # 调用方稍后重试同一查询即可拿到完整混合检索。
        # ★timeout 分支同样降级（而非放行去走向量路）：预热若已卡死，走 gateway.search
        #   会再次卡在同一个 C 扩展加载点上，必然超时被杀 —— 降级才是唯一安全解。
        _log("search -> keyword fallback (warmup %s)" % st)
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




def tool_feedback(args):
    uid = (args.get("uid") or "").strip()
    if not uid:
        return _err("缺少 uid 参数")
    reward = args.get("reward", 1.0)
    agent = (args.get("agent") or "").strip() or _default_source()
    detail = (args.get("detail") or "").strip()
    try:
        r = gateway.bump_qvalue(uid=uid, reward=reward, agent=agent, detail=detail)
        return _text(r)
    except Exception as e:
        return _err("feedback 失败: %s: %s" % (type(e).__name__, e))
HANDLERS = {
    "add_memories": tool_add,
    "search_memory": tool_search,
    "list_memories": tool_list,
    "feedback": tool_feedback,
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
    _log("started pid=%d rpc_encoding=%s gateway_import=%.2fs" % (
        os.getpid(), getattr(_RPC_OUT, "encoding", "?"), _IMPORT_SECS))
    # ★★ 启动时机（2026-09-19 二次修订）：
    #    `_preload_heavy()` + 预热线程不再在进程启动时抢跑，改由 _ensure_started()
    #    在 **initialize 应答发出之后**触发 —— 客户端首个会话只给 2000ms prewait，
    #    启动时做重活会把 initialize 应答推到 ~1.5s，余量太薄。
    #    ★两条不可动的约束：
    #      ① 预加载必须在主线程（不得丢后台线程）——否则复现 import 死锁；
    #      ② tools/call 之前必须已 _ensure_started()——否则预热没起，检索必降级。
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _log("bad json: %s" % e)
            continue
        method = req.get("method")
        # 防御：客户端若跳过 initialize 直接 tools/call（或 initialize 那条丢了），
        # 也要在调用前把预加载+预热补起来。
        if method == "tools/call":
            _ensure_started()
        t0 = time.time()
        resp = handle(req)
        dt = time.time() - t0
        if method == "tools/call":
            _log("tools/call %s -> %.2fs (warm=%s)" % (
                (req.get("params") or {}).get("name"), dt, _warm_state()))
        if resp is None:
            continue
        _RPC_OUT.write(json.dumps(resp, ensure_ascii=False) + "\n")
        _RPC_OUT.flush()
        # ★应答已落盘，再做重活：这一步把 initialize 的可见延迟从 ~1.5s 压到 ~0.7s
        if method == "initialize":
            _ensure_started()
    _log("stdin closed, exiting")


if __name__ == "__main__":
    main()
