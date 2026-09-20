# -*- coding: utf-8 -*-
"""clients/jsonc.py — JSONC/JSON 感知的「外科式」配置编辑器

为什么需要它
------------
接入一个客户端 = 往它的配置文件里塞一个 MCP server 定义。
但这类配置文件**不是纯 JSON**：

  - VS Code `settings.json`、Zed `settings.json`、Windsurf 配置 —— 都带 `//` 注释
  - 用户在里面还写着自己的模型、主题、快捷键等几十项

若用「`json.load` → 改 → `json.dump` 回写」这套常规做法：
  1. 带注释的文件**直接解析失败**（或需要先剥注释）
  2. 就算剥了注释能解析，回写时**注释全丢、键序可能重排、缩进被统一**
  ⇒ 用户的配置文件被我们「顺手改丑了」，甚至丢失信息。

所以这里走**外科式编辑**：只在原文里定位到目标位置，插入/替换那一小段，
**其余字节原样不动**。

实现要点
--------
`strip_jsonc(text)` 把注释替换成**等长空格**（保留换行），
⇒ 清洗后的文本与原文**偏移量一一对应**，可以直接用清洗文本上的下标去切原文。
这是整个模块的关键技巧：解析用清洗文本，切割用原文。

能力
----
  loads(text)                        解析（容忍注释 / 尾逗号）
  loads_strict(text)                 解析（不容忍，用于判断"是否本来就是纯 JSON"）
  has_comments(text)                 是否含注释（决定要不要走外科式路径）
  get(data, path)                    按路径取值，缺失返回 None
  set_entry(text, path, name, obj)   把 path 指向的对象里的 name 设为 obj，返回新文本

`set_entry` 三种情形都覆盖：
  ① name 不存在        → 在对象开头插入（保留注释与格式）
  ② name 存在且值相同  → 原样返回（**幂等**，不产生无意义写盘）
  ③ name 存在但值不同  → 只替换该值的字节区间
"""
from __future__ import annotations

import json
import re


# --------------------------------------------------------------------------
# 清洗：注释 -> 等长空格（偏移量不变）
# --------------------------------------------------------------------------
def strip_jsonc(text: str) -> str:
    """把 // 与 /* */ 注释替换成等长空白，字符串字面量内部不动。

    保证：返回串与输入串**长度相同、换行位置相同**。
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue

        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue

        # 行注释
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                out.append(" ")
                i += 1
            continue

        # 块注释
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            out.append("  ")
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] in "\r\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue

        out.append(c)
        i += 1
    return "".join(out)


def _strip_trailing_commas(clean: str) -> str:
    """把 ,} 与 ,] 之间的逗号换成空格（同样保持长度）。"""
    out = list(clean)
    n = len(out)
    i = 0
    while i < n:
        if out[i] == ",":
            j = i + 1
            while j < n and out[j] in " \t\r\n":
                j += 1
            if j < n and out[j] in "}]":
                out[i] = " "
        i += 1
    return "".join(out)


def has_comments(text: str) -> bool:
    """原文是否含注释（清洗后长度不变，但注释位置变成了空格）。"""
    clean = strip_jsonc(text)
    # 若清洗后与原文不同，说明确有注释（或被我们动过）
    return clean != text


def loads(text: str):
    """宽容解析：允许注释与尾逗号。失败抛 json.JSONDecodeError。"""
    clean = _strip_trailing_commas(strip_jsonc(text))
    return json.loads(clean)


def loads_strict(text: str):
    """严格解析：只允许纯 JSON。用于判断能否安全走 json 往返。"""
    return json.loads(text)


# --------------------------------------------------------------------------
# 定位：在清洗文本上找结构位置
# --------------------------------------------------------------------------
def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t\r\n":
        i += 1
    return i


def _read_string(s: str, i: int):
    """i 指向开引号。返回 (字符串内容, 闭引号下标+1)。"""
    assert s[i] == '"'
    j = i + 1
    n = len(s)
    buf = []
    while j < n:
        c = s[j]
        if c == "\\":
            buf.append(s[j:j + 2])
            j += 2
            continue
        if c == '"':
            return "".join(buf), j + 1
        buf.append(c)
        j += 1
    raise ValueError("字符串未闭合（下标 %d）" % i)


def _value_end(s: str, i: int) -> int:
    """i 指向值的第一个字符。返回值的结束下标（不含）。"""
    n = len(s)
    i = _skip_ws(s, i)
    if i >= n:
        raise ValueError("值缺失")
    c = s[i]
    if c in "{[":
        depth = 0
        j = i
        while j < n:
            ch = s[j]
            if ch == '"':
                _, j = _read_string(s, j)
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ValueError("对象/数组未闭合（下标 %d）" % i)
    if c == '"':
        _, j = _read_string(s, i)
        return j
    j = i
    while j < n and s[j] not in ",}] \t\r\n":
        j += 1
    return j


def _iter_entries(clean: str, brace: int):
    """遍历对象（clean[brace] == '{'）的直接子项。

    yield (key, key_start, colon, value_start, value_end)
    """
    n = len(clean)
    i = brace + 1
    while i < n:
        i = _skip_ws(clean, i)
        if i >= n or clean[i] == "}":
            return
        if clean[i] != '"':
            # 结构异常，放弃遍历
            return
        key_start = i
        key, i = _read_string(clean, i)
        i = _skip_ws(clean, i)
        if i >= n or clean[i] != ":":
            return
        colon = i
        vs = _skip_ws(clean, i + 1)
        ve = _value_end(clean, vs)
        yield key, key_start, colon, vs, ve
        i = _skip_ws(clean, ve)
        if i < n and clean[i] == ",":
            i += 1
            continue
        if i < n and clean[i] == "}":
            return
        # 既不是逗号也不是 } -> 异常
        return


def find_object(clean: str, path):
    """按 key 路径找到对象的 `{` 下标。路径不存在返回 None。"""
    i = _skip_ws(clean, 0)
    if i >= len(clean) or clean[i] != "{":
        return None
    cur = i
    for key in path:
        found = None
        for k, _ks, _c, vs, ve in _iter_entries(clean, cur):
            if k == key:
                if vs < len(clean) and clean[vs] == "{":
                    found = vs
                break
        if found is None:
            return None
        cur = found
    return cur


def get(data, path, default=None):
    """按路径取值。"""
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _detect_indent(text: str) -> str:
    """从原文猜缩进（只认首个缩进的键所在行的前导空白）。"""
    m = re.search(r"\n([ \t]+)\"", text)
    if m:
        return m.group(1)
    return "  "


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


# --------------------------------------------------------------------------
# 外科式写入
# --------------------------------------------------------------------------
def set_entry(text: str, path, name: str, obj, indent: str | None = None):
    """把 text 里 path 指向对象的 `name` 键设为 obj。

    返回 (新文本, 动作)。动作 ∈ {'noop', 'insert', 'replace'}
    幂等：值相同则返回原文 + 'noop'。
    """
    clean = _strip_trailing_commas(strip_jsonc(text))
    brace = find_object(clean, path)
    if brace is None:
        raise KeyError("配置里找不到路径 %s" % ".".join(path))

    nl = _newline_of(text)
    ind = indent or _detect_indent(text)
    # 目标对象自身的层级 = len(path)；它的**子项**在再下一层
    depth = len(path) + 1
    entry_ind = ind * depth

    target = None
    for k, ks, _c, vs, ve in _iter_entries(clean, brace):
        if k == name:
            target = (ks, vs, ve)
            break

    if target is not None:
        ks, vs, ve = target
        try:
            old = json.loads(clean[vs:ve])
        except Exception:
            old = object()
        if old == obj:
            return text, "noop"
        new_val = _dump_value(obj, entry_ind, nl, ind)
        return text[:vs] + new_val + text[ve:], "replace"

    # ---- 插入 ----
    new_val = _dump_value(obj, entry_ind, nl, ind)
    entries = list(_iter_entries(clean, brace))
    if entries:
        first_ks = entries[0][1]
        # text[:first_ks] 末尾已带「换行 + 缩进」，那份缩进现在归插入项用；
        # 所以插入项后面要自己补一份「换行 + 缩进」还给原来的第一个键。
        ins = '"%s": %s,%s%s' % (name, new_val, nl, entry_ind)
        return text[:first_ks] + ins + text[first_ks:], "insert"

    # 空对象：自己补换行与缩进（注意别把 `{\n}` 变成 `{\n\n}`）
    after = _skip_ws(clean, brace + 1)
    gap = text[brace + 1:after]
    if "\n" in gap:
        lead = ""
        indent_lead = "" if gap.endswith(entry_ind) else entry_ind
    else:
        lead = nl
        indent_lead = entry_ind
    body = lead + indent_lead + '"%s": %s' % (name, new_val) + nl + ind * (depth - 1)
    return text[:after] + body + text[after:], "insert"


def _dump_value(obj, base_ind: str, nl: str, ind: str) -> str:
    """把一个 Python 值序列化成带缩进的 JSON 片段。

    ★关键：`json.dumps(indent=ind)` 内部已经按 ind 逐层缩进，
    所以这里**只需**给首行之后的所有行统一加上 `base_ind`（= 键所在层级的缩进），
    绝不能再加一层 —— 否则每层都会多缩进一次（踩过）。

    以 key 在 4 空格为例：
        "key": {
            "a": 1        <- json 给 2，base_ind 给 4，共 6 ✓
        }                 <- json 给 0，base_ind 给 4，共 4 ✓
    """
    raw = json.dumps(obj, ensure_ascii=False, indent=ind)
    if nl != "\n":
        raw = raw.replace("\n", nl)
    if nl in raw:
        lines = raw.split(nl)
        raw = nl.join([lines[0]] + [base_ind + ln for ln in lines[1:]])
    return raw


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _selftest():
    ok = []

    def chk(name, cond):
        ok.append((name, bool(cond)))

    # 1) 带注释 + 尾逗号的解析
    src = '{\n  // 我的设置\n  "a": 1,\n  "b": { "c": 2, },\n}\n'
    d = loads(src)
    chk("loads 容忍注释与尾逗号", d["b"]["c"] == 2)
    chk("has_comments 识别注释", has_comments(src))
    chk("strip_jsonc 长度不变", len(strip_jsonc(src)) == len(src))

    # 2) 插入到含注释的嵌套对象，注释必须存活
    src2 = ('{\n'
            '  // 顶层注释\n'
            '  "servers": {\n'
            '    // 已有服务\n'
            '    "old": { "command": "x" }\n'
            '  },\n'
            '  "other": 7\n'
            '}\n')
    out, act = set_entry(src2, ["servers"], "new", {"command": "y"})
    chk("插入动作正确", act == "insert")
    chk("插入后注释存活", "// 顶层注释" in out and "// 已有服务" in out)
    chk("插入后 other 未动", '"other": 7' in out)
    chk("插入后可解析", loads(out)["servers"]["new"]["command"] == "y")
    chk("插入后旧项仍在", loads(out)["servers"]["old"]["command"] == "x")
    # ★格式：插入项缩进必须与同级键一致，且原有键的缩进不得被改动
    chk("插入项缩进对齐同级", '\n    "new": {' in out)
    chk("插入项闭合括号对齐键", '\n    },\n' in out)
    chk("插入项内部比键深一层", '\n      "command": "y"' in out)
    chk("原有键缩进未被改动", '\n    "old": { "command": "x" }' in out)
    chk("插入项与旧项之间无粘连", '"y" },\n    "old"' in out or '},\n    "old"' in out)

    # 3) 幂等
    out2, act2 = set_entry(out, ["servers"], "new", {"command": "y"})
    chk("幂等：值相同返回 noop", act2 == "noop" and out2 == out)

    # 4) 替换
    out3, act3 = set_entry(out, ["servers"], "old", {"command": "z", "env": {}})
    chk("替换动作正确", act3 == "replace")
    chk("替换后值更新", loads(out3)["servers"]["old"]["command"] == "z")
    chk("替换后新项仍在", loads(out3)["servers"]["new"]["command"] == "y")
    chk("替换后注释存活", "// 已有服务" in out3)

    # 5) 深层路径（ZCode 的 mcp.servers）
    src4 = '{\n  "mcp": {\n    "servers": {}\n  }\n}\n'
    out4, act4 = set_entry(src4, ["mcp", "servers"], "memory-hub", {"type": "stdio"})
    chk("空对象插入", act4 == "insert" and loads(out4)["mcp"]["servers"]["memory-hub"]["type"] == "stdio")
    chk("空对象插入不产生空行", "\n\n" not in out4)
    chk("空对象插入缩进正确", '\n      "memory-hub": {' in out4)
    # 5b) 真正的 `{}`（无换行）
    out4b, _ = set_entry('{\n  "s": {}\n}\n', ["s"], "k", {"a": 1})
    chk("紧凑空对象插入可解析", loads(out4b)["s"]["k"]["a"] == 1)
    chk("紧凑空对象插入无空行", "\n\n" not in out4b)

    # 6) CRLF 保留（判据：不存在「前面不是 CR 的 LF」）
    src5 = '{\r\n  "s": {}\r\n}\r\n'
    out5, _ = set_entry(src5, ["s"], "k", {"a": 1})
    chk("CRLF 保留（无裸 LF）", re.search(r"(?<!\r)\n", out5) is None)
    chk("CRLF 保留（可解析）", loads(out5)["s"]["k"]["a"] == 1)
    chk("CRLF 保留（闭合括号与键对齐）", '\r\n  }\r\n' in out5)

    # 7) 字符串里的 // 不能被当注释
    src6 = '{\n  "url": "http://x//y",\n  "s": {}\n}\n'
    chk("URL 里的双斜杠不误判", loads(src6)["url"] == "http://x//y")
    chk("URL 含 // 时 has_comments 不误报", not has_comments(src6))

    bad = [n for n, v in ok if not v]
    for n, v in ok:
        print("  [%s] %s" % ("PASS" if v else "FAIL", n))
    print("---- %d/%d ----" % (len(ok) - len(bad), len(ok)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
