# 安装指南（INSTALL）

三种装法，按场景选。**Windows 用户只想跑起来 → 方式一**；开发者改源码 → 方式二；
最小依赖/无网环境 → 方式三。

## 方式一：一键脚本（Windows，推荐）

```bat
install.bat
```

或 PowerShell 里：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

它依次做四件事（每步幂等，可重复跑；任一步失败即停，退出码指明死在哪一步）：

| 步骤 | 做什么 | 跳过参数 |
|---|---|---|
| 0 | 探测 Python >= 3.10 | `-Python <路径>` 手动指定 |
| 1 | `pip install` 本仓库（版本与 `pyproject.toml` 不一致才重装） | `-SkipInstall` |
| 2 | 生成全合成演示库 `~/.memtether/memory.db` | `-SkipDemo` |
| 3 | `tether_connect` 自动接入本机所有已检测到的 AI 客户端（**MCP 配置层**） | `-SkipConnect` |
| 3.5 | `auto_onboard` 注入指令/扩展层（Codex 桌面 app 官方插件、OpenClaw/dsh 的 AGENTS.md、投影腐蚀修复） | `-SkipOnboard` |
| 4 | `memtether stats` 收尾自检 | — |

常用变体：

```bat
install.bat -Vector            :: 加语义检索档（chromadb/onnxruntime，体积大；缺了自动降级关键词+字面）
install.bat -Editable          :: 开发模式，改源码即时生效
install.bat -SkipConnect       :: 只装包和演示库，不动任何客户端配置
install.bat -SkipOnboard       :: 保留 MCP 配置层，只跳指令层注入
install.bat -Home D:\memdata   :: 自定义数据目录（等价环境变量 MEMTETHER_HOME）
```

退出码：`0` 成功 · `1` 环境/参数 · `2` 装包失败 · `3` 演示库失败 · `4` 客户端接入失败。
（3.5 步指令层注入失败不阻断安装——MCP 功能底线已在第 3 步保证，但会打印 `[warn]`；
可随时重跑 `python integrations/auto_onboard.py apply`，幂等。）

## 方式二：pip 直接装

```bash
pip install .                    # 标准安装
pip install '.[vector]'          # 含语义检索档
pip install -e .                 # 开发模式（editable）
```

装完得到 `memtether` 命令：`memtether demo` / `memtether search "关键词"` /
`memtether stats` / `memtether remember "结论：……" --type experience --source my_agent`。

数据目录默认 `~/.memtether/`，用环境变量 `MEMTETHER_HOME` 可改。

从 PyPI 装（**尚未发布，占位中**；现在请从仓库目录装）：

```bash
pip install memtether
```

## 方式三：不装包，源码直跑

引擎模块平铺在仓库根（`import gateway` 这类顶层互相引用是**有意设计**，
生产环境按脚本路径直接调用），所以在仓库根就能跑：

```bash
python mem.py search "关键词"
python gateway.py remember "结论：……" --type experience --source my_agent
```

不装任何第三方依赖时，检索自动降级为关键词 + 字面（会打印 `[warn]`，不崩，
但排序质量下降）；装上 `.[vector]` 后走本地 bge-m3 embedding + chromadb。

## 接入你的客户端（安装之外的一步）

包装好只解决「读写通道」。「客户端会不会用、写进来的结论归属谁」由另两层决定：
技能层（`~/.agents/skills` 里告诉 agent 中枢在哪）与来源注册（`agents.json`）。
三步一把梭：

```bash
python tether_connect.py detect   # 只读：本机有哪些客户端、当前接入程度
python tether_connect.py plan     # 预演：将改哪些文件、改什么（不写盘）
python tether_connect.py apply    # 落盘（自动备份 + 幂等）
python tether_connect.py verify   # 回读校验 + 信任状态 + 在线探测
python tether_connect.py rollback # 从备份还原（任何一步不满意都能回）

python integrations/auto_onboard.py detect   # 只读：指令/扩展层缺哪些注入
python integrations/auto_onboard.py apply    # 落盘注入（幂等，可反复跑）
```

设计原则：只读优先（detect/plan/verify 绝不写盘）、幂等、可回滚（备份在
`~/.memtether/backups/<时间戳>/`）、失败关闭（解析不了/不认识就报错跳过，绝不猜着写）。

## 自检

```bash
python tether_connect.py selftest   # 客户端接入器 16 项自检
python scripts/check_packaging.py   # 打包完整性（缺模块/多余文件都会报）
python hub_selfcheck.py             # 全库健康巡检
```

## 常见问题

| 现象 | 原因与解法 |
|---|---|
| `'python' 不是内部或外部命令` | 装 Python 时没勾 "Add to python.exe to PATH"；重装或改用 `py` 启动器（install.ps1 也会自动试） |
| pip install 卡住/超时 | 换源：`-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 检索结果排序变差 | 没装 `[vector]` 档，走了关键词降级——装了就好，不装也能用 |
| 装完 `memtether` 命令找不到 | pip 的 Scripts 目录不在 PATH；用 `python -m memtether` 等价替代 |
| 客户端接入后 agent 仍不读写 | 看 verify 输出的三层状态；技能层是共享目录，工具只检查不覆盖 |
