# -*- coding: utf-8 -*-
"""clients/base.py — 客户端适配器基类与公共设施

设计原则
--------
1. **只读优先**：`detect()` 绝不写盘；`plan()` 只算不写；只有 `apply()` 才落盘。
2. **幂等**：已经装好且配置一致 → 动作 `noop`，不产生任何写盘。
3. **可回滚**：任何写盘前先备份到 `~/.memtether/backups/<时间戳>/`，并记 manifest。
4. **失败关闭**：解析不了 / 路径不存在 / schema 不认识 → 报错并跳过，**绝不猜着写**。
5. **适配器只描述「这个客户端长什么样」**，不含业务逻辑（来源名注册、信任代写等由上层编排）。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict

from . import jsonc

HOME = os.path.expanduser("~")
BACKUP_ROOT = os.path.join(HOME, ".memtether", "backups")

# 规范化的 MCP server 描述（各适配器负责翻译成自家 schema）
@dataclass
class ServerSpec:
    name: str = "memory-hub"
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    transport: str = "stdio"
    url: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Target:
    """一个被探测到的、可写入的配置落点。"""
    client_id: str
    display: str
    scope: str          # 'user' | 'project' | 'global' ...
    path: str
    exists: bool
    installed: bool     # 客户端本身是否装了（≠ 配置文件在不在）
    reason: str = ""    # 为什么认为它装了 / 没装

    def key(self):
        return "%s::%s::%s" % (self.client_id, self.scope, os.path.normcase(self.path))


@dataclass
class Change:
    target: Target
    action: str         # 'noop' | 'insert' | 'replace' | 'create' | 'skip' | 'error'
    detail: str = ""
    before: str = ""    # 预览用
    after: str = ""
    backup: str = ""

    @property
    def wrote(self):
        return self.action in ("insert", "replace", "create")


# --------------------------------------------------------------------------
# 文件工具
# --------------------------------------------------------------------------
def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def atomic_write(path: str, text: str) -> None:
    """原子写：临时文件 + os.replace。保留目标原有的换行风格（由调用方保证 text 已正确）。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".memtether-tmp-%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def backup(path: str, stamp: str, client_id: str) -> str:
    """把 path 备份到 ~/.memtether/backups/<stamp>/<client_id>/<文件名>。返回备份路径。"""
    if not os.path.exists(path):
        return ""
    flat = os.path.abspath(path).replace(":", "").replace("\\", "_").replace("/", "_")
    dst_dir = os.path.join(BACKUP_ROOT, stamp, client_id)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, flat)
    shutil.copy2(path, dst)
    return dst


def write_manifest(stamp: str, changes) -> str:
    d = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "manifest.json")
    data = {
        "stamp": stamp,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "changes": [
            {"client": c.target.client_id, "scope": c.target.scope, "path": c.target.path,
             "action": c.action, "detail": c.detail, "backup": c.backup}
            for c in changes
        ],
    }
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


# --------------------------------------------------------------------------
# 适配器基类
# --------------------------------------------------------------------------
class Adapter:
    """一个客户端的适配器。

    子类至少要覆盖：id / display / source / schema 渲染与写入。
    """

    id = ""
    display = ""
    #: 该客户端写记忆时应该用的 --source 名（要注册进 agents.json）
    source = ""
    #: 文档 / 参考链接
    docs = ""
    #: 需要重启客户端才生效吗
    needs_restart = True
    #: 安装判据：任一存在即视为「装了」。空 = 退回宽松的「父目录存在」判据。
    #: ★只在「配置文件落在用户主目录或共享目录」时才需要设，否则会误判为已安装。
    install_markers: tuple = ()
    #: 已知坑（会被打印到报告里）
    gotchas: list = []
    #: Electron 系客户端的「信任配置根目录」（含 mcp.json / mcp-approvals.json）。
    #: 非空 ⇒ 上层会走 clients.trust 代写信任记录。空 ⇒ 该客户端不需要单独授信。
    trust_root = ""

    def trust(self, spec: ServerSpec, dry_run=True):
        """授信钩子。默认不做事；Electron 系客户端覆盖 trust_root 即可自动生效。

        返回 (ok, detail) 或 None（表示该客户端不需要授信）。
        """
        if not self.trust_root:
            return None
        from . import trust as _trust
        ok, detail, _path = _trust.approve(
            self.trust_root, spec.name, self.render(spec), dry_run=dry_run)
        return ok, detail

    def trust_state(self, spec: ServerSpec):
        """只读：当前信任状态。返回 (trusted, detail) 或 None。"""
        if not self.trust_root:
            return None
        from . import trust as _trust
        return _trust.is_trusted(self.trust_root, spec.name, self.render(spec))

    def candidates(self):
        """返回 [(scope, path), ...]：可能的配置落点（按优先级）。

        允许返回「文件还不存在」的路径 —— 那是 `create` 的场景。
        """
        raise NotImplementedError

    def probe(self, scope, path):
        """判断该落点是否可用。返回 (installed: bool, reason: str)。

        ★判据分两档（2026-09-20 实测踩到）：
        · 有 install_markers → **必须**命中其一才算装了。
          为什么需要：`~/.claude.json` 的父目录是用户主目录，而主目录**必然存在**，
          用「父目录在不在」判会得出「装了 Claude Code」，于是工具会**替没装的软件
          造出配置文件**。宁可漏（提示用户手动指定），不可错（往系统里撒垃圾文件）。
        · 没有 markers → 退回「父目录存在」的宽松判据（适用于配置就在客户端专属目录下的情况）。
        """
        if self.install_markers:
            tried = []
            for m in self.install_markers:
                mp = os.path.expanduser(m)
                tried.append(mp)
                if any(ch in mp for ch in "*?["):
                    import glob as _glob
                    if _glob.glob(mp):
                        return True, "已安装（命中 %s）" % mp
                elif os.path.exists(mp):
                    return True, "已安装（命中 %s）" % mp
            return False, "未发现安装痕迹，跳过（不替没装的软件造配置）｜找过: %s" % " ; ".join(tried)
        parent = os.path.dirname(os.path.abspath(path))
        if os.path.isdir(parent):
            return True, "配置目录存在: %s" % parent
        return False, "配置目录不存在: %s" % parent

    # ---- schema 翻译（子类必须实现）----
    def render(self, spec: ServerSpec):
        """把规范化 spec 翻译成这个客户端认的 dict。"""
        raise NotImplementedError

    # ---- 写入 ----
    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        """返回 Change。dry_run=True 时只算不写。"""
        raise NotImplementedError

    def read_back(self, target: Target, spec: ServerSpec):
        """回读校验：返回 (ok, detail)。用于 verify。"""
        try:
            data = jsonc.loads(read_text(target.path))
        except Exception as e:
            return False, "解析失败: %s" % e
        cur = jsonc.get(data, self.root_path())
        if not isinstance(cur, dict) or spec.name not in cur:
            return False, "回读未找到 %s" % spec.name
        got = cur[spec.name]
        want = self.render(spec)
        if not isinstance(got, dict):
            return False, "回读 %s 不是对象（实际是 %s）" % (spec.name, type(got).__name__)
        ng, nw = norm_for_compare(got), norm_for_compare(want)
        if ng != nw:
            return False, "回读内容不一致%s" % _diff_hint(nw, ng)
        return True, "回读一致"

    def root_path(self):
        return ["mcpServers"]


# --------------------------------------------------------------------------
# 最常见的形态：JSON / JSONC，根键下是一个 {name: spec} 映射
# --------------------------------------------------------------------------
class JsonAdapter(Adapter):
    """root_path 指向那个「{名字: server定义}」的对象。"""

    #: 配置根路径（列表），例如 ["mcpServers"] 或 ["mcp", "servers"]
    root = ["mcpServers"]
    #: 额外注入的固定字段（例如 VS Code 的 "type": "stdio"）
    extra: dict = {}
    #: 需要剔除的字段（例如某些客户端不认 timeoutMs）
    strip: tuple = ()
    #: 是否允许创建不存在的文件
    allow_create = True
    #: 新建文件时的初始内容
    seed = "{\n}\n"

    def root_path(self):
        return list(self.root)

    def render(self, spec: ServerSpec):
        d = {"command": spec.command}
        if spec.args:
            d["args"] = list(spec.args)
        if spec.env:
            d["env"] = dict(spec.env)
        d.update(self.extra)
        for k in self.strip:
            d.pop(k, None)
        return d

    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        want = self.render(spec)
        created = not target.exists
        if created:
            if not self.allow_create:
                return Change(target, "skip", "配置文件不存在且该客户端不允许自动创建")
            text = self.seed
        else:
            try:
                text = read_text(target.path)
            except Exception as e:
                return Change(target, "error", "读取失败: %s" % e)

        # 先确认能解析（失败关闭：绝不覆盖一个我们读不懂的文件）
        try:
            jsonc.loads(text)
        except Exception as e:
            return Change(target, "error", "解析失败（不写，避免毁配置）: %s" % e)

        if created or jsonc.find_object(jsonc.strip_jsonc(text), self.root) is None:
            if not created:
                return Change(target, "error",
                              "配置里找不到根路径 `%s` —— 该客户端 schema 可能变了，已跳过（不猜着写）"
                              % ".".join(self.root))
            # 新建文件：在内存里逐层把根路径建出来（从外往里），最后只落盘一次
            try:
                for depth in range(1, len(self.root) + 1):
                    text, _ = jsonc.set_entry(text, self.root[:depth - 1], self.root[depth - 1], {})
            except Exception as e:
                return Change(target, "error", "自动创建根路径失败: %s" % e)
            if jsonc.find_object(jsonc.strip_jsonc(text), self.root) is None:
                return Change(target, "error", "自动创建根路径后仍找不到 %s" % ".".join(self.root))

        try:
            new_text, act = jsonc.set_entry(text, self.root, spec.name, want)
        except Exception as e:
            return Change(target, "error", "写入失败: %s" % e)

        if act == "noop" and not created:
            ch = Change(target, "noop", "已是最新，无需写入")
            return ch

        # ★语义等价就不再写盘（2026-09-20 实测踩到，很重要）：
        #   文本可能因为「渲染省略了空 env」「路径分隔符风格」这类无关差异而不等，
        #   裸比文本会让每次 apply 都重写一遍**本来正确**的配置 ——
        #   既吵（报告里全是"更新"，看不出真正的变化）又危险（每次重写都是一次覆写机会）。
        if not created:
            try:
                before_root = jsonc.get(jsonc.loads(text), self.root)
                after_root = jsonc.get(jsonc.loads(new_text), self.root)
                if norm_for_compare(before_root) == norm_for_compare(after_root):
                    ch = Change(target, "noop",
                                "%s.%s" % (".".join(self.root), spec.name))
                    ch.detail = "已是最新（语义一致，仅字面差异，未写盘）"
                    return ch
            except Exception:
                pass          # 比不出来就退回正常流程（宁可多写一次，不可漏写）

        # 文件原本不存在时，对外统一报 create（用户关心的是「新建了一个配置文件」）
        act_final = "create" if created else act
        ch = Change(target, act_final, "%s.%s" % (".".join(self.root), spec.name))
        if act == "noop":
            ch.detail = "已是最新，无需写入"
            return ch
        ch.before, ch.after = text, new_text
        if dry_run:
            return ch
        ch.backup = backup(target.path, _stamp(), target.client_id)
        try:
            atomic_write(target.path, new_text)
        except Exception as e:
            return Change(target, "error", "落盘失败: %s" % e)
        return ch


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def norm_for_compare(obj):
    r"""把配置片段归一化成「可比较」的形态。

    ★为什么需要它（2026-09-20 实测踩到）：
    同一条配置，手写的和程序生成的在**字面上**可能不同，但语义完全一样 ——
      · 路径分隔符：`E:\\RUANJIAN\\x.py` vs `E:/RUANJIAN/x.py`（JSON 里两者等价）
      · 重复斜杠：`E://RUANJIAN//x.py`
      · 空 env：省略 `env` 与写 `"env": {}` 等价
    如果 read_back 用 `got != want` 裸比，就会把这些**误判成「不一致」**，
    于是 verify 永远失败、apply 还会把本来正确的配置反复改写。
    归一化只用于**比较**，不参与写盘。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            nv = norm_for_compare(v)
            if nv in ({}, []) and k in ("env", "args"):
                continue                      # 空 env / 空 args 视同不存在
            out[k] = nv
        return out
    if isinstance(obj, list):
        return [norm_for_compare(x) for x in obj]
    if isinstance(obj, str):
        if "://" in obj:
            return obj                        # URL 别动，`https://` 里的双斜杠有意义
        s = obj.replace("\\", "/")
        while "//" in s:
            s = s.replace("//", "/")
        return s
    return obj


def _diff_hint(want, got):
    """给「不一致」补一句人话：到底哪个字段不对。

    只报差异，不报整坨 JSON —— 否则报告里全是噪声，真正的差异被淹掉。
    """
    parts = []
    for k in sorted(set(want) | set(got)):
        w, g = want.get(k, "<缺失>"), got.get(k, "<缺失>")
        if w != g:
            parts.append("    · %s\n        期望: %s\n        实际: %s" % (
                k, json.dumps(w, ensure_ascii=False), json.dumps(g, ensure_ascii=False)))
    if not parts:
        return ""
    return "\n" + "\n".join(parts[:6]) + ("\n    …（还有更多）" if len(parts) > 6 else "")


# --------------------------------------------------------------------------
# 文本补丁形态（例如 dsh 的 cordis.patch.yml：一个 YAML 数组，靠 id 匹配）
# --------------------------------------------------------------------------
class TextPatchAdapter(Adapter):
    """不适合 JSON 编辑的客户端：用「锚点块」方式做幂等文本插入。

    子类实现 `build_block(spec)` 返回要插入的文本，以及 `begin/end` 锚点标记。
    """

    begin_marker = "# >>> memtether:begin"
    end_marker = "# <<< memtether:end"

    def build_block(self, spec: ServerSpec) -> str:
        raise NotImplementedError

    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        if not target.exists:
            return Change(target, "skip", "配置文件不存在")
        try:
            text = read_text(target.path)
        except Exception as e:
            return Change(target, "error", "读取失败: %s" % e)

        block = self.build_block(spec)
        if self.begin_marker in text and self.end_marker in text:
            i = text.index(self.begin_marker)
            j = text.index(self.end_marker) + len(self.end_marker)
            if text[i:j] == block:
                return Change(target, "noop", "锚点块内容已是最新")
            new_text = text[:i] + block + text[j:]
            act = "replace"
        else:
            new_text = text.rstrip() + "\n\n" + block + "\n"
            act = "insert"

        ch = Change(target, act, "锚点块 %s" % self.begin_marker)
        ch.before, ch.after = text, new_text
        if dry_run:
            return ch
        ch.backup = backup(target.path, _stamp(), target.client_id)
        try:
            atomic_write(target.path, new_text)
        except Exception as e:
            return Change(target, "error", "落盘失败: %s" % e)
        return ch

    def read_back(self, target: Target, spec: ServerSpec):
        try:
            text = read_text(target.path)
        except Exception as e:
            return False, "读取失败: %s" % e
        if self.begin_marker not in text or self.end_marker not in text:
            return False, "锚点块不存在"
        i = text.index(self.begin_marker)
        j = text.index(self.end_marker) + len(self.end_marker)
        if text[i:j] != self.build_block(spec):
            return False, "锚点块内容与期望不一致"
        return True, "锚点块一致"
