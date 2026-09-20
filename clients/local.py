# -*- coding: utf-8 -*-
"""clients/local.py — 本机这一套客户端的适配器

这些是 `everymcp` 那类通用工具**不覆盖**的：
WorkBuddy（国内版/国际版）、CodeBuddy、ZCode、dsh（含 Tabbit 内嵌的那份）、OpenClaw。

它们的 schema **全部来自本机实测**（不是抄文档 —— 这几家没有公开的 MCP 配置文档）：

| 客户端 | 配置路径 | 根键 | 来源名 | 需要授信 |
|---|---|---|---|---|
| WorkBuddy 国内版 | `~/.workbuddy/mcp.json` | `mcpServers` | `workbuddy` | ★是 |
| WorkBuddy 国际版 | `~/.workbuddy-ai/mcp.json` | `mcpServers` | `workbuddy_ai` | ★是 |
| CodeBuddy | `~/.codebuddy/mcp.json` | `mcpServers` | `codebuddy` | ★是 |
| ZCode | `~/.zcode/cli/config.json` | `mcp.servers` | `zcode` | 否 |
| dsh | `~/.dsh/profiles/*/cordis.patch.yml` | YAML `- insert:` | `dsh` | 否 |
| OpenClaw | `~/.openclaw/openclaw.json` | `mcp.servers` | `openclaw` | 否 |
| 中立真源 | `~/.agents/mcp.json` | `mcpServers` | （不写记忆） | 否 |

★「中立真源」这一条是**兜底**：`~/.agents/mcp.json` 被多个客户端当回退路径读，
即使某家的主配置路径变了，这里还能保住一份。

各家踩过的坑
------------
* **WorkBuddy / CodeBuddy**：写完配置只是第一步，**还要过信任闸门**（见 clients/trust.py）。
  且 `loadApprovals()` 是一次性内存缓存 ⇒ **正在运行的实例外改文件永不生效**，
  必须重启或点一次 Trust。
* **ZCode**：schema 是 `.strict()` 的（zod）—— 多一个未知键，**整个 server 会被静默丢弃**。
  合法键只有：`type` / `command` / `args` / `cwd` / `env` / `enabled` / `timeoutMs`。
  主路径是 `~/.zcode/cli/config.json`（嵌套 `mcp.servers`），
  `~/.agents/mcp.json` 只是**同 scope 内的回退** —— 主路径一旦定义了任何 server，
  回退文件会被**整份忽略**，所以两处都要维护。
* **dsh**：profile 的补丁层是 YAML 数组，**新增插件必须走 `insert:` 语义**
  （直接写 `- name: ...` 会报 `patch: id is required for non-insert patches`）。
  改完要**新开会话**才连接。
* **OpenClaw**：`mcp.servers` 里每个 server 还支持 `cwd` 与
  `codex.defaultToolsApprovalMode`（本工具不写这两个，保持最小改动）。
"""
from __future__ import annotations

import glob
import os
import re

from .base import (Adapter, JsonAdapter, TextPatchAdapter, Target, ServerSpec,
                   Change, read_text, atomic_write, backup, _stamp)
from . import jsonc

HOME = os.path.expanduser("~")


def _yaml_ok(text):
    """尽力校验 YAML 是否能被解析。返回 (ok, err)。

    ★为什么必须加这道闸（2026-09-20 实测踩到）：
    补丁层默认模板是「注释 + `[]`」。若在 `[]` 之后再追加 `- insert:`，
    YAML 直接报 "end of the stream or a document separator is expected"
    ⇒ **整个 profile 的补丁层失效、dsh 起不来**。静态回读看不出这个问题
    （文本里确实有我们的块），只有真解析才发现。所以写盘前必须验一次。

    dsh 的补丁层允许 `!!js` 表达式，pyyaml 不认识这些 tag —— 用
    add_multi_constructor 把它们当 None 收掉，否则会误报。
    没装 pyyaml 时返回 ok=True（宁可放过，也不因缺依赖就拒绝写入）。
    """
    try:
        import yaml
    except ImportError:
        return True, "（无 pyyaml，跳过 YAML 校验）"

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    try:
        yaml.load(text, Loader=_Loader)
        return True, ""
    except Exception as e:
        return False, str(e).splitlines()[0]


# --------------------------------------------------------------------------
# 1~3. Electron 系：WorkBuddy 双版本 + CodeBuddy（配置形态相同，差别在路径与来源名）
# --------------------------------------------------------------------------
class _ElectronWorkBuddy(JsonAdapter):
    """配置形态：`~/.<dir>/mcp.json` 的顶层 `mcpServers`。写完还要授信。"""

    root = ["mcpServers"]
    needs_restart = True
    allow_create = True

    def config_dir(self):
        raise NotImplementedError

    def config_path(self):
        return os.path.join(self.config_dir(), "mcp.json")

    def candidates(self):
        return [("user", self.config_path())]

    def probe(self, scope, path):
        d = self.config_dir()
        if os.path.isdir(d):
            return True, "配置目录存在: %s" % d
        return False, "配置目录不存在: %s" % d

    @property
    def trust_root(self):
        return self.config_dir()

    def read_back(self, target: Target, spec: ServerSpec):
        """★只判「文件内容对不对」；信任状态由 verify 单独查（两者是独立的失败面）。"""
        return super().read_back(target, spec)


class WorkBuddyCN(_ElectronWorkBuddy):
    id = "workbuddy-cn"
    display = "WorkBuddy 国内版"
    source = "workbuddy"
    docs = "本地实测（无公开文档）"
    gotchas = [
        "★写完配置还要过**信任闸门**（mcp-approvals.json），否则 UI 显示已连接但 agent 拿不到工具",
        "★loadApprovals() 是一次性内存缓存 ⇒ 正在运行的实例外改文件**永不生效**，必须重启或点一次 Trust",
        "来源名必须用 workbuddy（不带 --source 会被记成它，污染归属）",
    ]

    def config_dir(self):
        return os.path.join(HOME, ".workbuddy")


class WorkBuddyIntl(_ElectronWorkBuddy):
    id = "workbuddy-intl"
    display = "WorkBuddy 国际版"
    source = "workbuddy_ai"
    docs = "本地实测（无公开文档）"
    gotchas = [
        "★与国内版**同一套物理真源**（投影是同一 inode），但来源名必须区分",
        "★写完配置还要过信任闸门（同国内版）",
    ]

    def config_dir(self):
        return os.path.join(HOME, ".workbuddy-ai")


class CodeBuddy(_ElectronWorkBuddy):
    id = "codebuddy"
    display = "CodeBuddy"
    source = "codebuddy"
    docs = "本地实测（无公开文档）"
    gotchas = [
        "配置目录里通常只有 models.json —— mcp.json 可能不存在，本工具会创建",
        "同属 Electron 系，写完要授信",
    ]

    def config_dir(self):
        return os.path.join(HOME, ".codebuddy")


# --------------------------------------------------------------------------
# 4. ZCode：嵌套 mcp.servers + strict schema
# --------------------------------------------------------------------------
class ZCode(JsonAdapter):
    id = "zcode"
    display = "ZCode"
    source = "zcode"
    docs = "本地实测（读 zcode.cjs 的 zod schema 逆出）"
    root = ["mcp", "servers"]
    #: ★只允许这些键 —— 多一个未知键整个 server 会被静默丢弃
    extra = {"type": "stdio", "timeoutMs": 60000}
    needs_restart = True
    gotchas = [
        "★schema 是 .strict()（zod）：多一个未知键，**整个 server 被静默丢弃**",
        "合法键仅 type/command/args/cwd/env/enabled/timeoutMs",
        "★主路径 ~/.zcode/cli/config.json 一旦定义了任何 server，~/.agents/mcp.json 会被整份忽略",
        "MCP 在**会话启动时**连接 ⇒ 改完要新开会话（或重启客户端）",
    ]

    def config_path(self):
        return os.path.join(HOME, ".zcode", "cli", "config.json")

    def candidates(self):
        return [("user", self.config_path())]

    def probe(self, scope, path):
        p = os.path.join(HOME, ".zcode")
        if os.path.isdir(p):
            return True, "配置目录存在: %s" % p
        return False, "配置目录不存在: %s" % p

    def render(self, spec: ServerSpec):
        d = super().render(spec)
        # ★strict：只保留白名单内的键
        allowed = {"type", "command", "args", "cwd", "env", "enabled", "timeoutMs"}
        return {k: v for k, v in d.items() if k in allowed}


# --------------------------------------------------------------------------
# 5. dsh：YAML 补丁层（insert 语义）
# --------------------------------------------------------------------------
class DshAdapter(Adapter):
    id = "dsh"
    display = "DeepSeek Harness (dsh)"
    source = "dsh"
    docs = "本地实测（读 dsh-mcp-client/README.zh.md + cordis.patch.yml 语义）"
    needs_restart = True
    gotchas = [
        "★新增插件必须走 `insert:` 语义 —— 直接写 `- name: ...` 会报 patch: id is required",
        "MCP 在会话启动时连接 ⇒ 改完要新开会话",
        "★`~/.dsh` 是用户自己的 dsh home；Tabbit 内嵌的那份 dsh 有自己的 DSH_HOME",
        "dsh 的 agentsHome 默认 ~/.agents ⇒ 技能走 ~/.agents/skills 与 DSH_HOME 无关",
    ]

    def profiles(self):
        return [d for d in glob.glob(os.path.join(HOME, ".dsh", "profiles", "*"))
                if os.path.isdir(d) and os.path.isfile(os.path.join(d, "cordis.patch.yml"))]

    def candidates(self):
        return [("profile:%s" % os.path.basename(p), os.path.join(p, "cordis.patch.yml"))
                for p in self.profiles()]

    def probe(self, scope, path):
        return os.path.isfile(path), ("存在: %s" % path) if os.path.isfile(path) else "不存在"

    def render(self, spec: ServerSpec):
        return {"serverName": spec.name, "transport": "stdio",
                "command": spec.command, "args": list(spec.args)}

    def _block(self, spec: ServerSpec):
        args = "\n".join("          - '%s'" % a.replace("'", "''") for a in spec.args)
        return ("- insert:\n"
                "    - id: mcp-%s\n"
                "      name: '@deepseek-ai/dsh-mcp-client'\n"
                "      config:\n"
                "        serverName: %s\n"
                "        transport: stdio\n"
                "        command: '%s'\n"
                "        args:\n%s" % (spec.name, spec.name, spec.command.replace("'", "''"), args))

    def _top_level_spans(self, text):
        """切出顶层 `- ` 项的范围。"""
        starts = [m.start() for m in re.finditer(r"^-\s", text, re.M)]
        return [(s, starts[i + 1] if i + 1 < len(starts) else len(text))
                for i, s in enumerate(starts)]

    def _existing_block_span(self, text, name):
        """找**我们自己那个** `- insert:` 块（serverName / id 匹配）。

        ★必须遍历所有顶层项：profile 的补丁层里通常还有别的 insert，
        只看第一个会漏（早期实现只搜第一个 `- insert:`，一旦用户先加了别的
        插件就再也找不到我们的块，于是每次 apply 都重复追加）。
        """
        for s, e in self._top_level_spans(text):
            seg = text[s:e]
            if not re.match(r"^-\s*insert:", seg):
                continue
            if ("serverName: %s" % name) in seg or ("id: mcp-%s" % name) in seg:
                return (s, e, seg)
        return None

    def _body(self, text):
        """去掉注释与空行后的正文行。"""
        return [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def write(self, target: Target, spec: ServerSpec, dry_run=True):
        p = target.path
        try:
            text = read_text(p)
        except Exception as e:
            return Change(target, "error", "读取失败: %s" % e)

        span = self._existing_block_span(text, spec.name)
        if span:
            i, j, seg = span
            want = self._block(spec)
            # 用「命令 + 参数」判等价（YAML 缩进风格可能与我们的不同，不做字符串比较）
            cmd_ok = spec.command in seg
            args_ok = all(a in seg for a in spec.args)
            if cmd_ok and args_ok:
                return Change(target, "noop", "已有 %s 的 insert 块且 command/args 一致" % spec.name)
            new = text[:i] + want + "\n" + text[j:]
            act = "replace"
        else:
            body = self._body(text)
            if body in ([], ["[]"]):
                # ★空序列必须**替换**，不能追加（2026-09-20 实测踩到）：
                #   默认模板就是「注释 + []」。在 `[]` 后面再接 `- insert:`，
                #   YAML 会直接报
                #     "end of the stream or a document separator is expected"
                #   ⇒ 整个 profile 的补丁层失效（dsh 起不来）。
                i = text.find("[]")
                if i >= 0:
                    new = text[:i] + self._block(spec) + "\n"
                else:
                    new = text.rstrip() + "\n" + self._block(spec) + "\n"
                act = "replace"
            elif body and body[0].startswith("-"):
                new = text.rstrip() + "\n\n" + self._block(spec) + "\n"
                act = "insert"
            else:
                # 不是序列（可能是映射/标量）—— 不猜，交回用户
                return Change(target, "error",
                              "cordis.patch.yml 的正文既不是空序列也不是列表"
                              "（首行: %r）。为避免写坏 YAML，本工具不动它 —— "
                              "请确认该 profile 的补丁层格式。" % (body[0] if body else ""))

        ch = Change(target, act, "cordis.patch.yml 的 insert 块")
        ch.before, ch.after = text, new

        # ★写前 YAML 校验：解析不过就一个字节都不写
        yok, yerr = _yaml_ok(new)
        if not yok:
            return Change(target, "error",
                          "写入后的 YAML 解析失败，已放弃写盘（原文未动）: %s" % yerr)
        if dry_run:
            return ch
        ch.backup = backup(p, _stamp(), self.id)
        try:
            atomic_write(p, new)
        except Exception as e:
            return Change(target, "error", "落盘失败: %s" % e)
        return ch

    def read_back(self, target: Target, spec: ServerSpec):
        try:
            text = read_text(target.path)
        except Exception as e:
            return False, "读取失败: %s" % e
        span = self._existing_block_span(text, spec.name)
        if not span:
            return False, "补丁层里找不到 %s 的 insert 块" % spec.name
        _i, _j, seg = span
        if spec.command not in seg:
            return False, "insert 块里的 command 与期望不符"
        if not all(a in seg for a in spec.args):
            return False, "insert 块里的 args 不全"
        return True, "insert 块一致"


# --------------------------------------------------------------------------
# 6. OpenClaw
# --------------------------------------------------------------------------
class OpenClaw(JsonAdapter):
    id = "openclaw"
    display = "OpenClaw"
    source = "openclaw"
    docs = "本地实测（~/.openclaw/openclaw.json 的 mcp.servers）"
    root = ["mcp", "servers"]
    needs_restart = True
    gotchas = [
        "配置是 ~/.openclaw/openclaw.json，根键 mcp.servers（不是顶层 mcpServers）",
        "每个 server 还支持 cwd / codex.defaultToolsApprovalMode —— 本工具不写这两个，保持最小改动",
    ]

    def config_path(self):
        return os.path.join(HOME, ".openclaw", "openclaw.json")

    def candidates(self):
        return [("user", self.config_path())]

    def probe(self, scope, path):
        if os.path.isfile(path):
            return True, "配置文件存在"
        return False, "配置文件不存在: %s" % path


# --------------------------------------------------------------------------
# 7. 中立真源 ~/.agents/mcp.json（兜底，被多家当回退路径读）
# --------------------------------------------------------------------------
class NeutralAgents(JsonAdapter):
    id = "agents-neutral"
    display = "中立真源 ~/.agents/mcp.json"
    source = ""
    docs = "本机约定：多客户端共享的中立配置源"
    root = ["mcpServers"]
    needs_restart = True
    gotchas = [
        "★同 scope 内若客户端主路径已定义任何 server，这份回退文件会被**整份忽略**",
        "所以它必须与各客户端主配置**同时维护**，不能只写这一份",
    ]

    def config_path(self):
        return os.path.join(HOME, ".agents", "mcp.json")

    def candidates(self):
        return [("neutral", self.config_path())]

    def probe(self, scope, path):
        d = os.path.join(HOME, ".agents")
        if os.path.isdir(d):
            return True, "中立真源目录存在: %s" % d
        return False, "中立真源目录不存在: %s" % d


LOCAL = [
    WorkBuddyCN(), WorkBuddyIntl(), CodeBuddy(),
    ZCode(), DshAdapter(), OpenClaw(), NeutralAgents(),
]


def by_id(client_id: str):
    for a in LOCAL:
        if a.id == client_id:
            return a
    return None
