# MemTether · 跨客户端 AI 记忆中枢

> **一句话：让多个异构 AI 客户端共享同一份物理记忆，而不是同步各自的副本。**

![PyPI](https://img.shields.io/pypi/v/memtether) ![Python](https://img.shields.io/pypi/pyversions/memtether) ![License](https://img.shields.io/github/license/MemTether/MemTether)

[![安装 · PyPI](https://img.shields.io/badge/PyPI-memtether-blue)](https://pypi.org/project/memtether/) · [![源码 · GitHub](https://img.shields.io/badge/GitHub-MemTether-black)](https://github.com/MemTether/MemTether)

## 安装

```bash
pip install memtether
# 语义检索档（可选）：pip install "memtether[vector]"
```

换一个 AI 客户端，它就不认识你了 —— 这件事几乎每个在多个客户端/账号之间切换的人都遇到过。
主流方案的答案是"同步"：各存一份，然后对齐。同步必然漂移，漂移之后各说各话。
MemTether 的答案更简单：**让它们指向同一份文件**。

---

## 为什么不是"又一个记忆库"

| 常见做法 | 问题 | MemTether 的做法 |
|---|---|---|
| 每个 agent 各存一份，定时同步 | 漂移、冲突、谁也不信谁 | **文件级指针**（符号链接 / 目录联接）指向同一份 SQLite → 物理上只有一份 |
| 只存事实，工具/环境信息另放一张表 | 资产表不进检索 → 用户问"X 装在哪"答不上 | **资产与事实同池检索**，且每条资产带可执行校验 |
| 记完就完，从不验证 | 记忆静默腐化（路径失效、结论过期） | **四维评分卡 + 可执行校验**，定期自检 |
| 语义检索全押外部付费 API | 通道欠费就全灭 | **本地 embedding（bge-m3 int8）兜底**，断网可跑 |
| 只有"记录时间"一根时间轴 | 无法回答"当时为什么那样决策" | **双时间轴**（有效时间 T / 摄录时间 T′） |


---

## 30 秒对比：MemTether vs 同类项目

| 能力 | **MemTether** | mem0 | Zep / Graphiti | Letta (MemGPT) | delx-memory | agentmemory |
|---|---|---|---|---|---|---|
| 多客户端共享同一份物理记忆 | ✅ 文件级指针 | ❌ 各存副本 | ❌ 服务端 | ❌ agent 内 | ✅（KV 级） | ❌ |
| 双时间轴（有效时间 / 摄录时间） | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| supersession（不删旧 + 冲突检测） | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Q-Value（检索命中→采纳→上浮） | ✅ + 时间衰减 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 技能锻造（skill_forge + 预算守卫） | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 客户端自动接入（23 个适配器） | ✅ 一条命令 | ⚠️ 手动 | ⚠️ 手动 | ❌ | ⚠️ 手动 | ❌ |
| 本地 embedding（断网可跑） | ✅ bge-m3 int8 | ⚠️ 需 API | ⚠️ 需 API | ⚠️ 需 API | ❌ | ⚠️ 需 API |
| 评分卡 + 评测集开源可复现 | ✅ 双判分口径 | ⚠️ 自报 | ⚠️ 自报 | ⚠️ 自报 | ❌ | ⚠️ 自证 |
| 导出快照 PII 自动脱敏 | ✅ | ❌ | ❌ | ❌ | ⚠️ secret-blocking | ❌ |
| MCP + CLI 双通道 | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |

> ⚠️ **诚实声明**：mem0 / Zep / Letta 列的信息来自它们各自的公开文档（截至 2026-09），
> 不是第三方审计结果。delx-memory 和 agentmemory 的代码我逐个看过（见下方"与同类项目的关系"）。
> 选型请以自己的实测为准。

---

## 架构

```
多个 AI 客户端（各厂商、各版本、各账号）
        │  文件级指针（symlink / junction，指向同一份物理文件）
        ▼
┌──────────────────────────────────────────────┐
│ memory.db（SQLite）  ← 唯一真源               │
│   facts       事实/经验/决策/事故（含双时间轴） │
│   tool_assets 工具资产（路径 + 可执行校验）     │
│   supersessions / conflict_reviews  治理留痕   │
└──────────────────────────────────────────────┘
        │  gateway.py（唯一写入入口，带归属 source）
        ▼
   检索（向量 + 关键词 + 字面，RRF 融合 + 精排）
        │
   投影（注入各客户端，可钉住） / 评分卡 / 时序查询 as_of·timeline / 被采纳价值分 Q-Value
```

**关键约定**：所有写入只经 `gateway.py`，且必须带 `--source <来源名>`。
同一份物理文件被多个 agent 写，没有归属就是灾难。

> **想深入**：分层图、组件选型、9 条硬规则、事件驱动闭环、混合检索四路融合的完整说明
> 见 **[ARCHITECTURE.md](ARCHITECTURE.md)**。

### 技能层：记忆中枢的第 5 层

**技能是记忆中枢的一类资产，不是另一个项目。**

- **记忆**管「我记得什么」—— 事实、经验、决策、事故
- **技能**管「我怎么做」—— 把反复用对的做法固化成可复用的执行配方

两者是同一条链：**技能从记忆里长出来**（`skill_forge` 沉淀），**受预算约束**（`skill_budget`
防止把上下文撑爆），**再分发到两个客户端共享同一份物理文件**（junction，不是同步）。
分开看会各自失真，所以统一收进 `skillctl.py` 一个入口。

```
记忆层 (memory.db)
   │  skill_forge 沉淀（Initialize→Execute→Diagnose→Patch→Verify）
   ▼
预算层 (skill_budget：注入成本 / 空壳 / 重名族)
   ▼
中立真源 ~/.agents/skills
   ├─ junction → ~/.workbuddy/skills       （客户端 A）
   └─ junction → ~/.workbuddy-ai/skills    （客户端 B）
```

```bash
python skillctl.py audit    # 四段体检：分发 / 预算 / 空壳 / 重名族
python skillctl.py link     # 检查两版是否都指向中立真源
python skillctl.py dup      # 空壳检测（无 SKILL.md / 无描述 / 无正文）
python skillctl.py health   # 注入成本实测
python skillctl.py scan     # 从记忆库里扫候选，起草新技能
```

**三条实测事实**（2026-09-18，79 个技能）：

1. **装一处，两版生效** —— 分发走文件级指针，读写同一份物理文件，没有同步逻辑就不会漂移。
2. **治理杠杆不在"装几个"，在"每个描述多少字"** —— 79 个技能的注入成本是 **26,013 字符**
   （name+description，平均 330/个；含 location 行 32,569，平均 412/个）。
   真正会挤爆上下文的是描述写得太长，而不是数量。
3. **解析器必须兼容 YAML 块标量** —— 很多 `SKILL.md` 用 `description: >` 把描述折成多行。
   单行正则只会读到那个 `>`：实测注入成本 **22,869 → 26,013，被低估 3,144 字符（13.7%）**，
   还把 10 个正常技能误判成"空壳"（其中 `conducting-mobile-app-penetration-test`
   的描述从 1 字符变成 570 字符）。同一语义只能有一个解析实现。

---

## 快速开始

> 当前状态：**研究原型**。已支持标准 `pip` 安装与客户端自动接入
> （`memtether-connect`，见「接入你的客户端」一节）。

### 方式 A0：一键脚本（Windows，最省事）

```bat
install.bat
```

四步一把梭（探测 Python → 装包 → 生成演示库 → 自动接入本机客户端），每步幂等可重跑、
任一步失败即停并用退出码指明死因。要语义检索档加 `-Vector`，只装包不动客户端加
`-SkipConnect`。完整参数表与常见问题见 [`INSTALL.md`](INSTALL.md)。

### 方式 A：装成一个包（推荐）

```bash
pip install memtether

# 已发布到 PyPI：https://pypi.org/project/memtether/

# 想要语义检索（本地 embedding + 向量库）再加这一档：
#   多装 chromadb / onnxruntime / tokenizers；缺它时检索自动降级为
#   关键词 + 字面（会打印 [warn]，不崩，但排序质量下降）
pip install "memtether[vector]"
```

装完得到一个 `memtether` 命令，**不再依赖仓库目录**：

```bash
memtether demo                          # 生成全合成演示库 → ~/.memtether/memory.db
memtether search "跨客户端共享"          # 混合检索
memtether stats                         # 库内统计
memtether remember "结论：……" --type experience --source my_agent
```

数据目录默认 `~/.memtether/`（可用环境变量 `MEMTETHER_HOME` 改）。

**依赖说明**：唯一的硬依赖是 `numpy`（精排用；缺它时 `memsearch` 会 `try/except`
兜住并退回 RRF，**不崩**但排序质量下降）。语义检索所需的
`chromadb` / `onnxruntime` / `tokenizers` 放在 `[vector]` 档 —— 只想跑 demo 的人
不必先下几百 MB。模型权重（本地 `bge-m3 int8` 做 embedding、`bge-reranker` 做精排）
不在包内，首次按提示离线获取。

### 方式 B：直接用源码（运维脚本 / 评测集走这条）

仓库里的模块是**平铺在根目录**的，运维与评测脚本按路径直接调用：

```bash
# 写一条记忆（source 必填，用于多 agent 归属）
python gateway.py remember "结论：……" --type experience --source my_agent

# 检索（向量 + 关键词 + 字面混合）
python mem.py search "我的问题"

# 重建投影（写入后刷新各客户端看到的内容）
python gateway.py rebuild

# 四维评分卡（覆盖度 / 保鲜度 / 正确率 / 治理度）
python hub_score.py

# 资产普查与校验
python tool_audit.py audit
python tool_audit.py verify

# 时序查询
python mem.py asof 2026-09-15
python mem.py timeline <uid>

# 投影钉住：把「必须一直在」的定义类结论排除在时间竞争之外（复用 tags，不新增 schema）
python gateway.py pin <uid>            # 钉住；--off 释放
python gateway.py rebuild              # ★改完必须重建投影才生效

# 被采纳价值分：检索命中被采纳后回写，下次排序上浮（默认中性 0.5，不改变现有排序）
python mem.py qvalue                   # 只读看分布
python mem.py qvalue <uid> --reward 1  # 1=完全采纳 / 0.5=部分有用 / 0=检索到但没用
```

> `memtether demo` 与 `python scripts/make_demo_db.py` 是同一件事的两种入口
> （前者装完即用，后者无需安装）。

**写入约定**：每条记忆的**第一句必须把结论说完** —— 投影只保留首句，结论写在后面等于白写。

---

## 接入你的客户端（一条命令）

MCP 本身是「手动档」：你得自己找到每个客户端的配置文件、照它的 schema 写一段 JSON、
再（Electron 系）去 UI 里点一次信任。**每个客户端的 schema 都不一样**，
漏一个就有一个客户端读不到记忆。

`memtether-connect` 把这段变成一条命令：

```bash
memtether-connect detect     # 发现本机装了哪些客户端、各自配置在哪、接没接
memtether-connect plan       # 预演：只打印将要改什么，不写盘
memtether-connect apply      # 写入（先备份 + 生成 manifest，可回滚）
memtether-connect verify     # 校验：配置内容 + 信任状态
memtether-connect rollback --stamp <时间戳>
```

设计要点：

- **不猜**：找不到约定的根路径（客户端 schema 变了）就**失败关闭**，
  绝不「大概写在这儿」。写坏用户的配置文件比不写更糟。
- **保注释**：配置里带 `//` 注释或尾逗号时（VS Code 系常见），用 JSONC 感知的编辑器
  **只改该改的那一处**，不整份重排。
- **幂等**：语义一致就一个字节都不写 —— 路径分隔符风格、重复斜杠、空 `env`
  都先归一化再比较。否则「每次跑都改写一遍本来正确的配置」。
- **只碰装了的**：配置落在主目录/共享目录的客户端（`~/.claude.json` 的父目录
  必然存在）会误判成「已安装」，所以额外查安装痕迹，找不到就跳过。
- **来源名一起注册**：接入时顺手把来源名写进 `agents.json`。不注册的后果是
  写入被兜底成别的名字、归属串号 —— 这是评测数据被污染的头号原因。
- **信任代写**：Electron 系客户端的「已信任 MCP 列表」是按
  `sha256(command|sorted(args)|sorted(env keys))` 算出的键。本工具按同一算法代写，
  省掉「UI 显示已连接、Agent 却拿不到工具」这一步手工操作。
  （注意：**改 `env` 就会掉信任**，所以本项目的来源识别刻意不依赖 env，见下。）

适配器共 **23 个**：`clients/standard.py`（标准客户端，路径与 schema 对照社区维护的
agent config 参考表逐条核对）+ `clients/local.py`（本机实测的 Electron 系与 dsh 系）。

### MCP server 怎么知道「我是被谁拉起的」

不传 `source` 时的归属，按 `MEM_DEFAULT_SOURCE` 环境变量 > **父进程识别** > 兜底值
（默认中性的 `local`）解析。父进程识别是**两级**的：

1. **父进程映像全路径** —— 覆盖 Electron 系（`WorkBuddy.exe` / `ZCode.exe` / Tabbit 等，
   可执行文件路径里带自己的名字）；
2. **父进程命令行**（读 PEB）—— 覆盖「被通用宿主拉起」的情况：
   独立 dsh / Claude Code 的父进程就是普通 `node.exe`，只有命令行里才带
   `@deepseek-ai/dsh` / `claude-code`。

匹配表按签名长度降序（保证 `workbuddyai` 先于 `workbuddy` 命中）。
想加自己的客户端：在同目录放 `client_signatures.json`：

```json
{ "signatures": [["myclient", "mysource"]] }
```

> **为什么不直接用 `env` 配来源**：`env` 的 key 集合参与信任 hash，多一个 key 就掉信任，
> 而掉信任后 server 会被客户端**静默跳过**（只有日志里一行 `skipping untrusted`）。
> 所以能自动就别让用户配。

---

## 仓库里的文件地图

顶层平铺 **66 个 `.py`**。平铺是为了「方式 B」下能 `python gateway.py …` 直接跑；
代价是根目录很长。先看这张表，再决定要读哪几个：

| 分组 | 数量 | 你需要它吗 | 模块 |
|---|---|---|---|
| **对外接口** | 6 | ✅ **装完即用的就是这几个** | `memtether`（包门面 + CLI）· `gateway`（唯一写入入口）· `mem`（共享总线 + CLI）· `memsearch`（混合检索）· `mcp_server`（MCP Server）· `tether_connect`（客户端自动接入） |
| 引擎核心 | 13 | ⚠️ 被上面调用，一般不直接用 | `embed_local` `rerank` `governance` `project` `refuse_live` `refuse_gate` `tool_audit` `memory_sink` `memory_maintenance` `pair_superseded` `mem0_config` `post_turn` `demo_gateway` |
| 运维 / 自检 | 12 | 🔧 自建环境才用 | `hub_score`（四维评分卡）`hub_selfcheck` `hubguard`（并发治理）`attach_hubguard` `preflight`（每轮回答前预取记忆）`interpreter`（解释器解析 + 依赖校验）`publish_pypi` `bootstrap` `board` `approve` `enrich_caps` `sync_memory` |
| **技能层** | 3 | 🧩 想让"做法"也沉淀下来就用 | `skill_forge`（沉淀闭环）`skill_budget`（预算守卫 / 注入成本）`skillctl`（**统一入口**：沉淀 + 预算 + 分发） |
| 跨客户端协作 | 2 | 🔧 多个客户端共写同一份文件时才用 | `wslog_append`（共写日志原子追加）`slot_update`（共享槽位原地更新） |
| **发布与安全** | 6 | 🔒 装包/发布/守卫时用 | `concurrent_stress`（并发压测）`memtether_export`（PII 脱敏导出）`memtether_guard`（发布守卫）`memtether_harness`（测试 harness）`memtether_paths`（路径解析）`memtether_pipeline`（安全管线编排） |
| 迁移 / 一次性 | 7 | ⛔ 一般不用碰 | `migrate_bitemporal` `migrate_sink` `migrate_qvalue` `import_mem0` `sync_reflector_mem0` `astra_dialogue` `astra_memory_closure` |
| 17 ||| 17 | | 17 |评| 17 |测| 17 | | 17 |/| 17 | | 17 |回| 17 |归| 17 | | 17 ||| 17 | | 17 |1| 17 |6| 17 | | 17 ||| 17 | | 17 |�| 17 |�| 17 | | 17 |想| 17 |复| 17 |跑| 17 |卷| 17 |子| 17 |时| 17 | | 17 ||| 17 | | 17 |`| 17 |a| 17 |s| 17 |s| 17 |e| 17 |t| 17 |_| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |`| 17 | | 17 |`| 17 |a| 17 |s| 17 |s| 17 |e| 17 |t| 17 |_| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |_| 17 |h| 17 |o| 17 |l| 17 |d| 17 |o| 17 |u| 17 |t| 17 |`| 17 | | 17 |`| 17 |a| 17 |s| 17 |s| 17 |e| 17 |t| 17 |_| 17 |s| 17 |e| 17 |l| 17 |f| 17 |c| 17 |h| 17 |e| 17 |c| 17 |k| 17 |`| 17 | | 17 |`| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |_| 17 |l| 17 |o| 17 |n| 17 |g| 17 |m| 17 |e| 17 |m| 17 |e| 17 |v| 17 |a| 17 |l| 17 |`| 17 | | 17 |`| 17 |e| 17 |2| 17 |e| 17 |_| 17 |v| 17 |e| 17 |r| 17 |i| 17 |f| 17 |y| 17 |`| 17 | | 17 |`| 17 |h| 17 |a| 17 |r| 17 |d| 17 |_| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |`| 17 | | 17 |`| 17 |h| 17 |a| 17 |r| 17 |d| 17 |_| 17 |h| 17 |o| 17 |l| 17 |d| 17 |o| 17 |u| 17 |t| 17 |`| 17 | | 17 |`| 17 |j| 17 |u| 17 |d| 17 |g| 17 |e| 17 |_| 17 |s| 17 |e| 17 |l| 17 |f| 17 |c| 17 |h| 17 |e| 17 |c| 17 |k| 17 |`| 17 | | 17 |`| 17 |q| 17 |v| 17 |a| 17 |l| 17 |u| 17 |e| 17 |_| 17 |a| 17 |b| 17 |`| 17 | | 17 |`| 17 |q| 17 |v| 17 |a| 17 |l| 17 |u| 17 |e| 17 |_| 17 |u| 17 |p| 17 |s| 17 |h| 17 |i| 17 |f| 17 |t| 17 |_| 17 |t| 17 |e| 17 |s| 17 |t| 17 |`| 17 | | 17 |`| 17 |r| 17 |e| 17 |f| 17 |u| 17 |s| 17 |e| 17 |_| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |`| 17 | | 17 |`| 17 |r| 17 |e| 17 |g| 17 |r| 17 |e| 17 |s| 17 |s| 17 |i| 17 |o| 17 |n| 17 |_| 17 |t| 17 |e| 17 |s| 17 |t| 17 |`| 17 | | 17 |`| 17 |r| 17 |e| 17 |r| 17 |a| 17 |n| 17 |k| 17 |_| 17 |k| 17 |_| 17 |b| 17 |e| 17 |n| 17 |c| 17 |h| 17 |`| 17 | | 17 |`| 17 |s| 17 |m| 17 |o| 17 |k| 17 |e| 17 |_| 17 |b| 17 |i| 17 |t| 17 |e| 17 |m| 17 |p| 17 |o| 17 |r| 17 |a| 17 |l| 17 |`| 17 | | 17 |`| 17 |t| 17 |e| 17 |s| 17 |t| 17 |_| 17 |a| 17 |u| 17 |t| 17 |o| 17 |s| 17 |y| 17 |n| 17 |c| 17 |`| 17 | | 17 |`| 17 |t| 17 |e| 17 |s| 17 |t| 17 |_| 17 |t| 17 |r| 17 |i| 17 |g| 17 |g| 17 |e| 17 |r| 17 |s| 17 |`| 17 | | 17 |`| 17 |t| 17 |e| 17 |s| 17 |t| 17 |_| 17 |p| 17 |i| 17 |i| 17 |_| 17 |r| 17 |o| 17 |u| 17 |n| 17 |d| 17 |t| 17 |r| 17 |i| 17 |p| 17 |`| 17 | | 17 ||| 17 |

- **只想用，不想读源码** → 看第一行那 5 个就够；`pip` 装完之后它们都在 `memtether` 命令背后。
- **想复跑评测 / 想核对我们说的数** → 最后一行是给你的：卷子、判分口径、评分卡源码都在仓库里。
- **想自己搭一套** → 中间三行（运维 / 自检 + 技能层 + 跨客户端协作）。

`clients/` 是客户端适配器**包**（`tether_connect` 用，按 `pyproject` 的 `packages` 发布）；
`scripts/` 放辅助脚本（合成演示库生成、打包清单闸门、泄密扫描）；`docs/` 放文档索引。
名字以 `_` 开头的 `.py` 是本地临时脚本，**不进版本库**（`.gitignore` 已排除）。

> 这份清单由 `scripts/check_packaging.py` 与仓库实际文件核对 —— 不是手抄的。


---

## 安全与治理（09-25 新增）

**导出 / 导入 / 备份 / 归档是记忆泄露的主通道。** MemTether 在同一条管线上做了四层防线：

| 层 | 做什么 | 怎么验证 |
|---|---|---|
| **PII 脱敏** | 导出快照时自动扫描并脱敏手机号 / 邮箱 / API key 等，计数上报 | `test_pii_roundtrip.py`：脱敏 → 序列化 → 导入 → 数据库 0 PII 残留 |
| **OWASP 运行时防御** | `memtether_guard` 检测注入 / 越权 / 泄露模式，命中即拦截 | `guard --selftest`：ALL PASS |
| **泄密扫描闸门** | 打包前逐表扫全文，词表缺失时**显式说"无法判定"** 而非"零命中" | `scan_leaks.py` + `scripts/leak_terms.local.json`（词表不进仓库） |
| **并发治理锁** | hubguard 对 10 个写函数加锁，锁超时 120s（高并发压测验证） | N8 并发压测：多客户端同写同一份 DB，无脏写 |

**统一安全管线（N7）**：guard + 导出 PII 扫描已合并为一条管线（`memtether_pipeline.py`），
导出时自动跑完整套防线，不需要调用方单独触发。

---

## 想先看看它长什么样？用合成演示库

**本仓库不含任何真实记忆。** 为了让你 `clone` 下来就能跑起来，仓库带一个**生成脚本**，
一条命令产出**全合成**演示库：假客户端名（`alphachat`/`betamind`/`gammacli`…）、
假路径（`/opt/demo/…`）、假工具（`DemoEditor`/`DemoArchiver`…），
固定随机种子 → **逐字节可复现**（100 条 facts / 10 个工具资产）。

```bash
# 1) 生成演示库（自带三项自检，任一不通过即返回非 0）
python scripts/make_demo_db.py      # 源码方式
memtether demo                      # 装成包之后（等价入口，输出到 ~/.memtether/）
python scripts/make_demo_db.py --strict   # 更严：没词表 / 冒烟有告警都直接判失败

# 2) 用 MEM_DB 指向它 —— gateway / memsearch / mem.py 会一起切过去
export MEM_DB=demo/memory_demo.db
python mem.py stats
python mem.py search "跨客户端共享"
```

**自带三项自检**（不是"跑通了"，而是故意让它报）：

| 检查 | 口径 |
|---|---|
| 泄密检查 | 正则逐表扫全文。**有词表时才说「零命中」**；没词表时只说「**无法判定**」，绝不把"没查"说成"干净" |
| 表结构一致性 | 演示库列集与 `gateway.SCHEMA` **逐表比对**，不一致即失败 |
| 功能冒烟 | 子进程 `MEM_DB=…` 真跑 `gateway.stats` + `memsearch.asset_text` + `gateway.search`；**子进程打了告警就不算通过** |

> **词表不在仓库里，所以默认会看到「⚠ 无法判定」而不是「✓ 零命中」，这是故意的** ——
> 词表（`scripts/leak_terms.local.json`）记录的是"本项目要防哪些真实串"，本身含真实信息。
> 缺失时"姓名 / 安全事件"两类规则为空，脚本会**显式说明哪几类没参与检查**，
> 而不是假装"扫出 0 处 = 很干净"。
>
> 两个开关：`MEM_SCAN_TERMS=<词表路径>` 指向真词表（与 `scan_leaks.py` 同一口径）；
> `--strict` 让"没词表"或"冒烟有告警"**直接失败退出**（CI / 发布前用这个）。

> **为什么演示库不进版本库**：它是**生成物**。入库必然与生成脚本**漂移**
> —— 改一句模板、库没重新生成，别人拿到的就是旧内容。现场生成顺带证明「可复现」本身。
>
> **为什么需要 `MEM_DB`**：`gateway.py` / `memsearch.py` / `mem.py` 各自开库。
> 若只有一处认 `MEM_DB`，就会出现**同一轮查询读两个库**、结果半真半假
> —— 属"跑起来不报错、但结果错"的一类。新增读真源的模块时请复制同一段路径解析。
>
> **关于语义检索**：演示库**不带**预建向量索引（索引与 embedding 模型绑定，
> 本地 `bge-m3-int8` 是 1024 维，预置了别人换后端会撞维度校验）。
> 首次检索会提示降级为纯关键词，属预期；想要语义检索自己建一次：
> `python memsearch.py --rebuild`

---

## 它现在是什么水平（诚实版）

本机实测（2026-09-25）：

| 指标 | 值 |
|---|---|
| 硬基准 hard_bench（62 题，双判分口径） | **100%** |
| 资产基准 asset_bench（23 题） | **100%** |
| E2E 端到端（13 项探针） | **13/13** |
| LongMemEval oracle 全量 500 题（strict 下界） | **60.5%** (202/334) |
| HotpotQA distractor 100 题抽样（strict 下界） | **88.3%** (83/94) |

**请连同下面这句一起读这两个数**：hard_bench 是外部知识+本机资产双源基准，asset_bench 是纯本机资产类（确实存在"自出卷"偏差）。
E2E 是生产链路端到端探针（写入 → 检索 → 归属 → 单一真源一致性）。

**已知短板（不藏）**：
- **注入槽位瓶颈**：活跃条目数百条，但受客户端注入上限约束，每轮实际只能喂进几十条 → 存得多、喂得少
- **拒答能力中等（已实测，不吹）**：检索式系统默认只会返回"最像的"。现已接入拒答判据
  （词项覆盖率 + 相似度双阈值），**最新 26 条基准（09-25）实测：拦下 13/26 = 50%，误拒 1/22 = 4.5%**
  —— 比 09-22 初版标定（41%）有所提升，但"同形不同属性"这类负样本（如"X 的**端口**是多少" vs 记忆里只有"X 的**路径**"）
  **在词形法原理上无解**，是剩下 50% 漏拒的主要来源。当前默认 `warn`（只提示不阻断），不改变原有输出
- 通用基准：LongMemEval oracle 全量 500 题已跑完（strict 60.5%，k=12，检索式 harness，不可与论文全上下文口径直接对比）；LLM judge 口径未跑（需付费 API）
- 双时间轴里 `native`（原生记录）占比很低，多数为回填/推定
- **Q-Value 仍是「机制就位、数据在积累」**：回写需要调用方**显式**调
  `mem.py qvalue <uid>`，本项目**没有**自动判断"这条记忆被采纳了"的机制。
  但 09-24 已加入**时间衰减**（`q_decay = 0.5^(days/90)`，90 天半衰期）：即使
  Q-Value 尚未被回写，旧条目在排序中的"硬度"也会随时间自然下降，新条目
  获得相对优先级。09-25 已在 MCP server 加 `feedback` tool（`bump_qvalue`）：客户端检索命中后可显式调 `feedback(uid, reward)` 回写 Q-Value；但"全自动判定"仍需客户端配合，仍是路线图项。
- **投影钉住（`pin`）只是缓解、不是解决**：它让少数"必须一直在"的定义类结论
  免于被时间序挤出，但注入槽位的总量瓶颈没变。

---

## 与同类项目的关系

**不比分数** —— 这个赛道的公开分数已被证实大面积不可复现（同一系统自报 92% 与第三方复现 38%）。
MemTether 的卖点是**可验证性**：评分卡源码、评测集、双判分口径、失真警告全部在仓库里，你可以自己跑。

- **mem0 / supermemory**：抽取式记忆层，记什么由模型决定 —— 偏产品化记忆
- **Zep / Graphiti**：时序知识图谱，双时间轴定义即源于此 —— 偏企业级图存储
- **Letta（MemGPT）**：OS 式分页，模型自己编辑记忆 —— 偏 agent 框架
- **engram / agent-memory**：本地优先、单文件/单二进制 —— 工程形态最接近
- **[delx-memory](https://github.com/davidmosiah/delx-memory)**：最接近的形态对标 —— 同样是「一个本地 SQLite + 多 agent MCP 共享」；但它是 KV 存储（key/value + tags），无双时间轴 / supersession / Q-Value / 技能锻造 / 客户端自动接入。亮点：explicit_user_intent 硬闸门 + secret-blocking 写入端拦截 + lite transport（不加载 MCP SDK 降 RSS）
- **cass / anda-brain / spector**：2025-2026 新一轮 agent memory 探索 —— 均处早期，关注点各偏一面（检索质量 / 长期一致性 / 多 agent 协同）
- **Memmy**：目标最接近（多 agent 共享本地记忆），但是常驻服务 + 商业云侧

---

## 路线图

- [x] 合成 demo 库（对外示例，不含任何真实记忆）
- [x] 标准 `pip` 安装（wheel：平铺模块 + `memtether` 命令 + `[vector]` 可选档）
- [x] 拒答 / 置信度门槛（**默认 warn**；实测零误拒拦截面 41%，天花板 59%）
- [x] 评测集与评分卡源码开源（双判分口径 + 失真警告）
- [x] 投影钉住（`pin`）—— 把定义类结论排除在时间竞争之外（缓解注入槽位瓶颈）
- [x] 被采纳价值分（Q-Value）—— 检索命中被采纳后回写、下次上浮；**默认中性，不改变现有排序**
- [x] 客户端自动接入器（`memtether-connect`：发现 / 预演 / 写入 / 校验 / 回滚；23 个适配器）
- [x] MCP server 的来源自动识别（父进程映像名 + 命令行两级；零配置，不动 `env` 以免掉信任）
- [~] 「被采纳」的自动判定（Q-Value 上游）：**MCP `feedback` tool 已上线**（09-25），客户端可显式回写；全自动判定需客户端配合，仍是路线图项
- [x] 发布到 PyPI（[pip install memtether](https://pypi.org/project/memtether/)）
- [x] 语义相似度 boost（R1）— 高语义候选被关键词噪音淹没时自动上浮
- [x] 索引一致性闸门（R2）— 向量索引漂移超阈值自动重建
- [x] PII 脱敏层 + 导出快照 round-trip 测试（L2/T7/R3）
- [x] 统一安全管线（N7）— guard OWASP 防御 + 导出 PII 扫描合并为一条管线
- [x] 并发压测（N8）— hubguard 并发锁 20→120s，高并发下数据完整性验证
- [x] 索引重建闸门（N9）— rebuild 后自动检查向量索引一致性
- [x] 投影预算 3980（N2）— 从 2700 提升至官方注入槽位上限，消除 133 条记忆被截断
- [ ] 注入槽位策略优化（存得多、喂得少是当前最大瓶颈）
- [x] 拒答判据升级实验（B-3）— **结论：不升级，维持 P4 词形法**
  - 26 条基准（09-25 重测）：P4 词形法拦截 13/26 (50%)、误拒 1/22 (4.5%)
  - 纯语义法只拦 5/26 (23%)，联合判据 8/26 (31%) —— 都不如 P4 词形法
  - **瓶颈不在判据本身，而在检索 top-1 准确度**：如果检索召回的就不是最相关的记忆，再精的判据也判不对
  - 优先级应让给「检索质量优化」（注入槽位策略 / top-1 命中率）
- [ ] 自动接入器覆盖 macOS / Linux 的客户端路径（当前以 Windows 实测为准）

---

## 已知限制（实测，别被它们误导）

### 1. tether_connect detect 对「同源异路径」的配置会误报未接入

`detect` / `plan` 判定接入的依据是**配置里的 MCP 路径必须等于发布仓自己的
`memtether/mcp_server.py`**。因此当某个客户端按设计指向**另一份同源副本**时
（典型场景：本机生产实例用 `memory_hub/` 那份，而非发布仓那份），
即使配置完全正确、通道完全可用，`detect` 也会报「未接入/不一致」，
`plan` 会报「失败：内容与期望不一致 —— 不替你覆盖」。

**这不是故障，也不要去点 apply 把它改回来。** 判据是看通道本身是否通：
对该客户端跑一次检索，能返回 `engine=hybrid` 即为已接入。

> 实测记录（2026-09-22）：本机 10 个落点全部报「未接入/不一致」，
> 但逐个核对配置后确认 10/10 实际均已接入，且 `mem.py search` 全部命中。

### 2. dsh 的 cordis.patch.yml 若为手工深度定制，工具无法安全改写

`cordis.patch.yml` 允许 `!!js` 表达式，工具侧做「改写前先解析校验」时
可能解析不了定制版文件，此时**放弃写盘、原文不动**（fail-closed，不会写坏）。
若该 profile 的 `insert:` 块里已手工写好 `mcp-memory-hub`，
那它已经接入，不需要工具再动。

### 3. memory_hub 与 memtether 是两份同源代码，改动要判方向

两仓各有 `gateway.py` / `memsearch.py` / `mem.py` / `mcp_server.py` 等，
本机生产用 `memory_hub`，对外发布用 `memtether`。**不是同步镜像**：
发布仓做过 `DEFAULT_SOURCE` 泛化、脱敏、依赖外置等刻意差异。
修 bug 时应两仓同修，但**不能整文件互拷**（会把对方的刻意改动覆盖掉）。


---

## 声明

- **本仓库不包含任何真实记忆数据。** 示例数据全部为合成。
- 模型权重不在仓库内（体积过大），首次运行时按提示获取。
- 许可：**Apache-2.0**（见 `LICENSE`）；第三方组件与模型权重的归属声明见 `NOTICE`
- 安全问题请走 `SECURITY.md`
