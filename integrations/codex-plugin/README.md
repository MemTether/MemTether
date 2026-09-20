# MemTether · Codex 插件（app / CLI / IDE 三端通用）

## 它解决什么

Codex 桌面 app（商店版）不读 `~/.codex/config.toml` 的 `[mcp_servers]`——
该配置只对 Codex CLI 生效。桌面 app 走**官方插件机制**（2026-03-25 起）：
插件是可安装 bundle，可打包 MCP server configuration，app/CLI/IDE 通用。
本插件把 memory-hub MCP server 打成 Codex 插件，桌面 app 装完即用。

## 安装（桌面 app）

1. 本目录已在本地 marketplace 注册（`~/.codex/config.toml` 的
   `[marketplaces.memtether-local]`，指向 `../codex-marketplace`）；
2. 重启 Codex app → 插件页（市场入口）→ 找到 **MemTether Memory Hub** → Install；
3. 装完在新 thread 里应能看到 `memory-hub` 的三个工具：
   `search_memory` / `add_memories` / `list_memories`。

## 安装（CLI，免插件）

CLI 直接用 config.toml 的 `[mcp_servers.memory-hub]`（本机已配好），无需本插件。

## 换机器部署时必须改的一处

`.codex-plugin/.mcp.json` 里 `args` 的路径 `E:/RUANJIAN/memory_hub/mcp_server.py`
是**本机绝对路径**——clone 到别的机器后改成实际路径。
（不写成相对/环境变量是因为插件机制按字面路径 spawn，无 shell 展开。）

## 附带：Codex 技能接入

Codex 读用户级技能目录 `~/.codex/skills`（2025-12-19 起）。
把 `memory-hub-usage` 技能放进去（symlink 即可），Codex 就自带"怎么用中枢"指引：

```bash
mkdir -p ~/.codex/skills
ln -s <agents>/skills/memory-hub-usage ~/.codex/skills/memory-hub-usage
```
