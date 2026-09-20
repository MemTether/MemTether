# MemTether · Codex 插件（app / CLI / IDE 三端通用）

## 它解决什么

Codex 桌面 app（商店版）不读 `~/.codex/config.toml` 的 `[mcp_servers]`——
该配置只对 Codex CLI 生效。桌面 app 走**官方插件机制**（2026-03-25 起）：
插件是可安装 bundle，可打包 MCP server configuration，app/CLI/IDE 通用。
本插件把 memory-hub MCP server 打成 Codex 插件，桌面 app 装完即用。

## 文件结构（照本机 openai-bundled 真实样板，零猜测）

```
codex-marketplace/                     ← marketplace 根（config.toml 注册的 source）
├── .agents/plugins/
│   └── marketplace.json               # 插件目录：name/source/policy/category
└── plugins/
    └── memtether-memory/              ← 插件必须在 marketplace 根内！
        ├── .codex-plugin/
        │   └── plugin.json            # 声明 "mcpServers": "./.mcp.json"（必须有）
        ├── .mcp.json.template         # MCP 配置模板（占位符 <HUB>/<HUB_VENV_PY>）
        ├── .mcp.json                  # ★auto_onboard 实例化生成，.gitignore，勿手改
        ├── .gitignore
        └── README.md
```

⚠️ **两条用事故换来的规则**（2026-09-21 实测，缺一即"装了也没工具"）：
1. **`.mcp.json` 在插件根目录**，不在 `.codex-plugin/` 里；`plugin.json` 必须有
   `"mcpServers": "./.mcp.json"` 引用字段。
2. **插件目录必须在 marketplace 根内**（`./plugins/<name>`）。放在根外
   （`../codex-plugin` 或绝对路径）CLI 会**静默拒绝**——`plugin list` 里
   根本不出现，且不报错。

## 安装：零 GUI（auto_onboard 全自动）

```bash
python integrations/auto_onboard.py apply     # 幂等，可反复跑
```

它依次做：注册 marketplace 进 `~/.codex/config.toml`（标记块）→
按本机路径实例化 `.mcp.json`（venv 解释器）→ **`codex plugin add
memtether-memory --marketplace memtether-local`（免 GUI 安装并启用）** →
建 `~/.codex/skills` symlink。

手动等价命令（排查用）：

```bash
codex plugin marketplace list                                  # 确认 marketplace 已注册
codex plugin list --marketplace memtether-local                # 确认插件可见
codex plugin add memtether-memory --marketplace memtether-local  # 安装
```

## 安装（CLI 直接对话，免插件）

Codex CLI 用 config.toml 的 `[mcp_servers.memory-hub]`（tether_connect 负责），
无需本插件。

## 换机器部署：零手工

`.mcp.json` 不手改——仓库里只有 `.mcp.json.template`（占位符），本机实例化
文件不进 git。`auto_onboard.py apply` 一次搞定：实例化 + 注册 + 安装 + symlink。

## 两个字段的取舍（照样板 + 本中枢历史坑）

- `omit_tools_from: ["deferred"]`：不让三个工具进 deferred 池——本中枢的
  历史事故正是"工具被 defer 隐藏，agent 从来想不起去搜"（实测国内版
  DeferExecuteTool 调 memory-hub 0 次）。bundled 样板对需要即用的工具同样处理。
- `startup_timeout_sec: 90`：venv + SQLite 冷启动慢于样板默认 10s。

## 附带：Codex 技能接入

Codex 读用户级技能目录 `~/.codex/skills`（2025-12-19 起）。
`auto_onboard.py` 会把 `memory-hub-usage` 技能 symlink 进去，
Codex 就自带"怎么用中枢"指引。
