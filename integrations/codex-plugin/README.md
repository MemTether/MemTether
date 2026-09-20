# MemTether · Codex 插件（app / CLI / IDE 三端通用）

## 它解决什么

Codex 桌面 app（商店版）不读 `~/.codex/config.toml` 的 `[mcp_servers]`——
该配置只对 Codex CLI 生效。桌面 app 走**官方插件机制**（2026-03-25 起）：
插件是可安装 bundle，可打包 MCP server configuration，app/CLI/IDE 通用。
本插件把 memory-hub MCP server 打成 Codex 插件，桌面 app 装完即用。

## 文件结构（照本机 openai-bundled 真实样板，零猜测）

```
codex-plugin/
├── .codex-plugin/
│   └── plugin.json      # 声明 "mcpServers": "./.mcp.json" 引用字段（必须有！）
├── .mcp.json.template   # MCP 配置模板（占位符 <HUB> / <HUB_VENV_PY>）
├── .mcp.json            # ★由 auto_onboard.py 实例化生成，.gitignore，勿手改
├── .gitignore
└── README.md
```

⚠️ **`.mcp.json` 在插件根目录，不在 `.codex-plugin/` 里**——本机 bundled 样板
（codex-app-tools / unified-computer-use）都是根目录布局；且 `plugin.json`
必须有 `"mcpServers": "./.mcp.json"` 引用字段，否则插件装上也没有工具
（2026-09-20 23:50 实测踩坑：字段缺失 + 位置放错 = 装了等于没装）。

## 安装（桌面 app）

1. marketplace 已由 `auto_onboard.py` 注册进 `~/.codex/config.toml`
   （`[marketplaces.memtether-local]`，指向 `../codex-marketplace`，
   带 `# >>> memtether-auto-onboard` 标记块，幂等）；
2. 重启 Codex app → 插件页（市场入口）→ 找到 **MemTether Memory Hub** → Install；
3. 装完在新 thread 里应能看到 `memory-hub` 的三个工具：
   `search_memory` / `add_memories` / `list_memories`。

## 安装（CLI，免插件）

CLI 直接用 config.toml 的 `[mcp_servers.memory-hub]`（tether_connect 负责），
无需本插件。

## 换机器部署：零手工

`.mcp.json` 不手改——仓库里只有 `.mcp.json.template`（占位符），
本机实例化文件不进 git。部署时跑一次：

```bash
python integrations/auto_onboard.py apply        # 幂等，可反复跑
```

它会：把 `<HUB>` / `<HUB_VENV_PY>` 换成本机实际路径（**解释器用 hub venv 的
python**——用系统 python 会静默降级成纯关键词检索，这是本中枢的已知坑）、
注册 marketplace、建 `~/.codex/skills` symlink。

## 两个字段的取舍（照样板 + 本中枢历史坑）

- `omit_tools_from: ["deferred"]`：不让三个工具进 deferred 池——本中枢的
  历史事故正是"工具被 defer 隐藏，agent 从来想不起去搜"（实测国内版
  DeferExecuteTool 调 memory-hub 0 次）。bundled 样板对需要即用的工具
  同样处理。
- `startup_timeout_sec: 90`：venv + SQLite 冷启动慢于样板默认 10s。

## 附带：Codex 技能接入

Codex 读用户级技能目录 `~/.codex/skills`（2025-12-19 起）。
`auto_onboard.py` 会把 `memory-hub-usage` 技能 symlink 进去，
Codex 就自带"怎么用中枢"指引。
