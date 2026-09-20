# -*- coding: utf-8 -*-
"""clients/standard.py — 通用 AI 客户端的适配器（不依赖本机任何自研组件）

路径与 schema 依据
------------------
主参考：`am-will/everymcp` 的 `AGENT_CONFIG_REFERENCE.md`（MIT，2026-02）
（GitHub 全网唯一一份把 15 个客户端的**配置路径 + 根键 + schema 差异 + 各家怪癖**
逐条列全的公开资料。本项目**不重复造轮子**，直接以它为规范来源。）

★为什么不用 everymcp 本身
--------------------------
它是 TypeScript/Node，覆盖 15 个**云端/IDE 类** agent；
但**不覆盖**本机真正在用的那几个（WorkBuddy / CodeBuddy / ZCode / dsh / Tabbit /
OpenClaw / Codex），也没有「来源名注册」「MCP 信任代写」「技能层兜底投递」这些
**记忆中枢特有**的环节。所以这里只**借用它的知识**，实现留在 Python 侧，
与本仓库其余部分同一套原子写 / 备份 / 幂等约定。

各家差异速查（踩点都在这里）
---------------------------
| 客户端 | 根键 | 备注 |
|---|---|---|
| Claude Desktop | `mcpServers` | 仅 stdio；**必须完全退出重启**；Windows 上 npx 要 `cmd /c` |
| Claude Code | `mcpServers` | 全局在 `~/.claude.json`；改完自动生效 |
| Cursor | `mcpServers` | IDE 重载 |
| Windsurf | `mcpServers` | 有 `disabled` / `alwaysAllow` 两个额外字段 |
| VS Code | **`servers`** | ★**不是** `mcpServers`；还有 `inputs` 数组做凭据提示 |
| Zed | **`context_servers`** | ★必须带 `"source": "custom"` |
| Cline / Roo / Kilo | `mcpServers` | 都在 VS Code globalStorage 里，路径含扩展 ID |
| Continue | `mcpServers` | **YAML**，且是**数组**格式（`- name: / transport:`）|
| Cody | `cody.mcpServers` | 嵌在 VS Code settings.json 里 |
| JetBrains | `servers` 或 `mcpServers` | 两个都认 |
| Neovim | `mcpServers` | 走 MCPHub.nvim |
| Amazon Q | `mcpServers` | `~/.aws/amazonq/mcp.json` |
| Gemini CLI | `mcpServers` | 嵌在 `~/.gemini/settings.json` |
"""
from __future__ import annotations

import json
import os

from .base import JsonAdapter, Target, ServerSpec, read_text, atomic_write, backup, Change, _stamp
from . import jsonc

HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming"))
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))


# --------------------------------------------------------------------------
# 基础：纯 JSON 的 mcpServers
# --------------------------------------------------------------------------
class _McpServers(JsonAdapter):
    root = ["mcpServers"]

    def candidates(self):
        return [("user", self.config_path())]


def _mk(cls_name, client_id, display, path_fn, root, extra=None, strip=(), docs="",
        source="", needs_restart=True, gotchas=None, markers=()):
    """按表生成适配器类（少写样板）。

    markers：安装判据（任一命中即视为已安装）。
    ★配置落点若在**用户主目录**或**共享目录**（如 `~/.claude.json`、VS Code 的
    settings.json），必须给 markers，否则「父目录存在」永远为真 ⇒ 工具会替
    根本没装的软件造出配置文件。
    """
    ns = {
        "id": client_id,
        "display": display,
        "docs": docs,
        "source": source or client_id.replace("-", "_"),
        "needs_restart": needs_restart,
        "gotchas": gotchas or [],
        "root": list(root),
        "extra": dict(extra or {}),
        "strip": tuple(strip),
        "install_markers": tuple(markers),
        "config_path": staticmethod(path_fn),
    }
    return type(cls_name, (_McpServers,), ns)


# --------------------------------------------------------------------------
# 逐个客户端
# --------------------------------------------------------------------------
def _claude_desktop():
    return os.path.join(APPDATA, "Claude", "claude_desktop_config.json")


def _claude_code():
    return os.path.join(HOME, ".claude.json")


def _cursor():
    return os.path.join(HOME, ".cursor", "mcp.json")


def _windsurf():
    return os.path.join(HOME, ".codeium", "windsurf", "mcp_config.json")


def _vscode_user():
    return os.path.join(APPDATA, "Code", "User", "settings.json")


def _vscode_ws():
    return os.path.join(os.getcwd(), ".vscode", "mcp.json")


def _zed():
    return os.path.join(APPDATA, "Zed", "settings.json")


def _cline():
    return os.path.join(APPDATA, "Code", "User", "globalStorage",
                        "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")


def _roo():
    return os.path.join(APPDATA, "Code", "User", "globalStorage",
                        "rooveterinaryinc.roo-cline", "settings", "mcp_settings.json")


def _kilo():
    return os.path.join(APPDATA, "Code", "User", "globalStorage",
                        "kilocode.kilo-code", "settings", "mcp_settings.json")


def _continue_yaml():
    return os.path.join(HOME, ".continue", "config.yaml")


def _junie():
    return os.path.join(HOME, ".junie", "mcp", "mcp.json")


def _neovim():
    return os.path.join(HOME, ".config", "mcphub", "servers.json")


def _amazonq():
    return os.path.join(HOME, ".aws", "amazonq", "mcp.json")


def _gemini():
    return os.path.join(HOME, ".gemini", "settings.json")


REGISTRY = []


def _reg(a):
    REGISTRY.append(a())
    return a


_claude_desktop_A = _reg(_mk(
    "ClaudeDesktop", "claude-desktop", "Claude Desktop", _claude_desktop, ["mcpServers"],
    docs="https://modelcontextprotocol.io/quickstart/user",
    gotchas=["仅支持 stdio；远程 MCP 只能在 Settings > Connectors 里加，配置文件写不进去",
             "改完必须**完全退出**（含托盘）再重启，不是刷新",
             "Windows 上跑 npx 要写成 cmd /c npx，或直接给 .cmd 全路径"]))

_claude_code_A = _reg(_mk(
    "ClaudeCode", "claude-code", "Claude Code", _claude_code, ["mcpServers"],
    docs="https://docs.claude.com/en/docs/claude-code/mcp",
    needs_restart=False,
    markers=["~/.claude", "~/.claude.json"],
    gotchas=["改完自动生效，不用重启"]))

_cursor_A = _reg(_mk(
    "Cursor", "cursor", "Cursor", _cursor, ["mcpServers"],
    docs="https://docs.cursor.com/context/model-context-protocol",
    gotchas=["IDE 需要 reload 窗口"]))

_windsurf_A = _reg(_mk(
    "Windsurf", "windsurf", "Windsurf", _windsurf, ["mcpServers"],
    extra={"disabled": False, "alwaysAllow": []},
    docs="https://docs.windsurf.com/windsurf/cascade/mcp",
    gotchas=["HTTP 传输的 URL 字段叫 serverUrl（不是 url）",
             "环境变量语法是 ${env:VAR}"]))

_vscode_ws_A = _reg(_mk(
    "VSCodeWorkspace", "vscode-workspace", "VS Code（工作区）", _vscode_ws, ["servers"],
    extra={"type": "stdio"},
    docs="https://code.visualstudio.com/docs/copilot/chat/mcp-servers",
    gotchas=["★根键是 servers，不是 mcpServers —— 写成 mcpServers 会被静默忽略",
             "支持 inputs 数组做凭据提示（本工具不写 inputs）"]))

_zed_A = _reg(_mk(
    "Zed", "zed", "Zed", _zed, ["context_servers"],
    extra={"source": "custom"},
    docs="https://zed.dev/docs/assistant/model-context-protocol",
    gotchas=["★必须带 \"source\": \"custom\"，缺了不生效",
             "配置是嵌在 settings.json 里的，本工具会保住你原有的注释与其它设置"]))

_cline_A = _reg(_mk(
    "Cline", "cline", "Cline (VS Code)", _cline, ["mcpServers"],
    extra={"disabled": False, "alwaysAllow": []},
    docs="https://docs.cline.bot/mcp/configuring-mcp-servers"))

_roo_A = _reg(_mk(
    "RooCode", "roo-code", "Roo Code (VS Code)", _roo, ["mcpServers"],
    extra={"disabled": False, "alwaysAllow": []},
    docs="https://docs.roocode.com/features/mcp/using-mcp-in-roo"))

_kilo_A = _reg(_mk(
    "KiloCode", "kilo-code", "Kilo Code (VS Code)", _kilo, ["mcpServers"],
    extra={"disabled": False, "alwaysAllow": []},
    docs="https://kilocode.ai/docs/features/mcp/using-mcp-in-kilo-code"))

_junie_A = _reg(_mk(
    "JetBrainsJunie", "jetbrains", "JetBrains (Junie)", _junie, ["servers"],
    docs="https://www.jetbrains.com/help/ai-assistant/mcp.html",
    gotchas=["servers 与 mcpServers 两个根键都认；本工具写 servers"]))

_neovim_A = _reg(_mk(
    "NeovimMcpHub", "neovim", "Neovim (MCPHub.nvim)", _neovim, ["mcpServers"],
    docs="https://github.com/ravitemer/mcphub.nvim",
    gotchas=["依赖 MCPHub.nvim 插件，没装插件的话写了也没人读"]))

_amazonq_A = _reg(_mk(
    "AmazonQ", "amazon-q", "Amazon Q Developer", _amazonq, ["mcpServers"],
    docs="https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/mcp.html"))

_gemini_A = _reg(_mk(
    "GeminiCli", "gemini-cli", "Gemini CLI", _gemini, ["mcpServers"],
    docs="https://github.com/google-gemini/gemini-cli",
    gotchas=["配置是嵌在 ~/.gemini/settings.json 里的，本工具会保住你原有的其它设置"]))

_cody_A = _reg(_mk(
    "Cody", "cody", "Sourcegraph Cody", _vscode_user, ["cody", "mcpServers"],
    docs="https://sourcegraph.com/docs/cody/capabilities/mcp",
    markers=["~/.vscode/extensions/*cody*", "~/.vscode-insiders/extensions/*cody*"],
    gotchas=["嵌在 VS Code settings.json 的 cody.mcpServers 下",
             "★同一份 settings.json 被 Cody 与 VS Code 用户级共用，本工具按路径去重",
             "★装了 VS Code 但没装 Cody 扩展时不该写 —— 已用扩展目录做安装判据"]))


# --------------------------------------------------------------------------
# Continue：YAML + 数组格式，单独实现
# --------------------------------------------------------------------------
class ContinueAdapter(JsonAdapter):
    """Continue 的 `~/.continue/config.yaml` 是 YAML，且 mcpServers 是**数组**：

        mcpServers:
          - name: memory-hub
            transport:
              type: stdio
              command: ...
              args: [...]
              env: {...}

    YAML 做外科式编辑代价高，所以这里**只在文件不存在时创建**；
    已存在时走「锚点块追加」（YAML 支持文档分隔符，但 continue 只读第一个文档），
    因此改为：检测到已有 mcpServers 就报 skip 并提示手工合并 —— **不猜着改**。
    """

    id = "continue"
    display = "Continue.dev"
    docs = "https://docs.continue.dev/customize/deep-dives/mcp"
    source = "continue"
    root = ["mcpServers"]
    needs_restart = True
    gotchas = ["★mcpServers 是**数组**格式（- name: / transport:），不是映射",
               "YAML 无法安全外科式编辑 ⇒ 已存在 mcpServers 时本工具只提示、不改动"]

    def config_path(self):
        return _continue_yaml()

    def candidates(self):
        return [("user", self.config_path())]

    def render(self, spec: ServerSpec):
        return {"name": spec.name,
                "transport": {"type": "stdio", "command": spec.command,
                              "args": list(spec.args), "env": dict(spec.env)}}

    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        p = target.path
        if not os.path.exists(p):
            block = self._yaml_block(spec)
            ch = Change(target, "create", "新建 Continue 配置")
            ch.after = block
            if dry_run:
                return ch
            ch.backup = ""
            atomic_write(p, block)
            return ch
        try:
            text = read_text(p)
        except Exception as e:
            return Change(target, "error", "读取失败: %s" % e)
        if spec.name in text:
            return Change(target, "noop", "配置里已出现 %s" % spec.name)
        return Change(target, "skip",
                      "已存在 %s 且含 mcpServers；YAML 不做外科式编辑，"
                      "请手工把下面这段并入（本工具不猜着改）：\n%s" % (p, self._yaml_block(spec)))

    def _yaml_block(self, spec: ServerSpec):
        args = ", ".join('"%s"' % a.replace("\\", "\\\\") for a in spec.args)
        lines = ["mcpServers:",
                 "  - name: %s" % spec.name,
                 "    transport:",
                 "      type: stdio",
                 '      command: "%s"' % spec.command.replace("\\", "\\\\"),
                 "      args: [%s]" % args]
        return "\n".join(lines) + "\n"

    def read_back(self, target: Target, spec: ServerSpec):
        try:
            text = read_text(target.path)
        except Exception as e:
            return False, "读取失败: %s" % e
        return (spec.name in text), ("配置里%s %s" % ("存在" if spec.name in text else "不存在", spec.name))


# --------------------------------------------------------------------------
# OpenAI Codex：TOML，`[mcp_servers.<name>]` 表
# --------------------------------------------------------------------------
class CodexAdapter(JsonAdapter):
    """`~/.codex/config.toml` 里的 `[mcp_servers.memory-hub]` 表。

    TOML 无法用 JSON 编辑器改，所以走「锚点块」：
    在文件末尾维护一段被注释包裹的 TOML 表。Codex 读 TOML 时注释无副作用。
    """

    id = "codex"
    display = "OpenAI Codex CLI"
    docs = "https://github.com/openai/codex"
    source = "codex"
    root = []
    needs_restart = True
    gotchas = ["配置是 TOML（~/.codex/config.toml），走锚点块方式写入",
               "★若 config.toml 里 model_provider 指向了非官方值，会架空 ChatGPT 登录态"]

    begin = "# >>> memtether:begin（自动生成，勿手改本块内容）"
    end = "# <<< memtether:end"

    def config_path(self):
        return os.path.join(HOME, ".codex", "config.toml")

    def candidates(self):
        return [("user", self.config_path())]

    def render(self, spec: ServerSpec):
        return {"command": spec.command, "args": list(spec.args)}

    def _block(self, spec: ServerSpec):
        args = ", ".join('"%s"' % a.replace("\\", "\\\\") for a in spec.args)
        return "\n".join([
            self.begin,
            "[mcp_servers.%s]" % spec.name,
            'command = "%s"' % spec.command.replace("\\", "\\\\"),
            "args = [%s]" % args,
            self.end,
        ])

    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        p = target.path
        block = self._block(spec)
        if not os.path.exists(p):
            ch = Change(target, "create", "新建 ~/.codex/config.toml")
            ch.after = block + "\n"
            if dry_run:
                return ch
            atomic_write(p, block + "\n")
            return ch
        try:
            text = read_text(p)
        except Exception as e:
            return Change(target, "error", "读取失败: %s" % e)
        if self.begin in text and self.end in text:
            i = text.index(self.begin)
            j = text.index(self.end) + len(self.end)
            if text[i:j] == block:
                return Change(target, "noop", "锚点块已是最新")
            new = text[:i] + block + text[j:]
            act = "replace"
        else:
            # ★已存在同名表时不能追加（2026-09-20 实测踩到）：
            #   TOML 里同名表出现两次是**硬错误**，整个 config.toml 会解析失败
            #   ⇒ 直接毁掉用户的 Codex 配置。但也不能一律报错 ——
            #   用户/别的工具完全可能已经用**原生 TOML 写法**接好了，
            #   这时正确的动作是「识别并采纳」，而不是逼他删掉重来。
            existing = self._existing_table(text, spec.name)
            if existing is not None:
                if self._table_matches(existing, spec):
                    return Change(target, "noop",
                                  "已接入（config.toml 里是原生 TOML 写法，非本工具锚点块）")
                return Change(target, "error",
                              "config.toml 里已存在 [mcp_servers.%s]，但内容与期望不一致 —— "
                              "不替你覆盖。\n        实际: %s\n        期望: command=%s args=%s"
                              % (spec.name,
                                 json.dumps(existing, ensure_ascii=False),
                                 spec.command, spec.args))
            new = text.rstrip() + "\n\n" + block + "\n"
            act = "insert"
        ch = Change(target, act, "TOML 锚点块")
        ch.before, ch.after = text, new
        if dry_run:
            return ch
        ch.backup = backup(p, _stamp(), self.id)
        try:
            atomic_write(p, new)
        except Exception as e:
            return Change(target, "error", "落盘失败: %s" % e)
        return ch

    def _existing_table(self, text, name):
        """读出 `[mcp_servers.<name>]` 这一节（不依赖 tomllib 也能工作）。

        返回 dict 或 None。优先用 tomllib（准确），没有就退回到手写扫描。
        """
        try:
            import tomllib
            d = tomllib.loads(text)
            t = (d.get("mcp_servers") or {}).get(name)
            if isinstance(t, dict):
                return t
            return None
        except Exception:
            pass
        # 退化路径：纯文本扫描（够用，因为我们只关心 command / args）
        header = "[mcp_servers.%s]" % name
        if header not in text:
            return None
        out = {}
        started = False
        for raw in text.splitlines():
            ln = raw.strip()
            if ln.startswith("["):
                if ln == header:
                    started = True
                    continue
                if started:
                    break
            if not started or not ln or ln.startswith("#"):
                continue
            if "=" in ln:
                k, _, v = ln.partition("=")
                out[k.strip()] = v.strip()
        return out or None

    def _table_matches(self, existing, spec):
        """已有表与期望 spec 是否「语义一致」。

        ★只比 command / args —— 用户额外加的键（如 startup_timeout_sec）不该算不一致，
        否则每次 apply 都会把一条已经正确的配置报成 error。
        """
        from .base import norm_for_compare
        want = {"command": spec.command, "args": list(spec.args)}
        got = {k: existing.get(k) for k in ("command", "args") if k in existing}
        return norm_for_compare(got) == norm_for_compare(want)

    def read_back(self, target: Target, spec: ServerSpec):
        try:
            text = read_text(target.path)
        except Exception as e:
            return False, "读取失败: %s" % e
        if self.begin in text and self.end in text:
            i = text.index(self.begin)
            j = text.index(self.end) + len(self.end)
            same = text[i:j] == self._block(spec)
            return same, "锚点块%s" % ("一致" if same else "不一致")
        # 没有锚点块也可能是接好的（原生 TOML 写法）
        existing = self._existing_table(text, spec.name)
        if existing is None:
            return False, "既无锚点块，也没有 [mcp_servers.%s]" % spec.name
        if self._table_matches(existing, spec):
            return True, "已接入（原生 TOML 写法，command/args 一致）"
        return False, "存在 [mcp_servers.%s] 但 command/args 不一致" % spec.name


STANDARD = REGISTRY + [ContinueAdapter(), CodexAdapter()]


def by_id(client_id: str):
    for a in STANDARD:
        if a.id == client_id:
            return a
    return None
