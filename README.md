# MemTether · 跨客户端 AI 记忆中枢

> **一句话：让多个异构 AI 客户端共享同一份物理记忆，而不是同步各自的副本。**

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
   投影（注入各客户端） / 评分卡 / 时序查询 as_of·timeline
```

**关键约定**：所有写入只经 `gateway.py`，且必须带 `--source <来源名>`。
同一份物理文件被多个 agent 写，没有归属就是灾难。

> **想深入**：分层图、组件选型、9 条硬规则、事件驱动闭环、混合检索四路融合的完整说明
> 见 **[ARCHITECTURE.md](ARCHITECTURE.md)**。

---

## 快速开始

> 当前状态：**研究原型**。已支持标准 `pip` 安装；一键安装脚本在路线图中（见文末）。

### 方式 A：装成一个包（推荐）

```bash
# 从 PyPI（★尚未发布，占位中；现在请用下面那条）
pip install memtether

# 现在就能用：直接从仓库装
pip install "memtether @ git+https://github.com/MemTether/MemTether"

# 想要语义检索（本地 embedding + 向量库）再加这一档：
#   多装 chromadb / onnxruntime / tokenizers；缺它时检索自动降级为
#   关键词 + 字面（会打印 [warn]，不崩，但排序质量下降）
pip install "memtether[vector] @ git+https://github.com/MemTether/MemTether"
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
```

> `memtether demo` 与 `python scripts/make_demo_db.py` 是同一件事的两种入口
> （前者装完即用，后者无需安装）。

**写入约定**：每条记忆的**第一句必须把结论说完** —— 投影只保留首句，结论写在后面等于白写。

---

## 仓库里的文件地图

顶层平铺 **52 个 `.py`**。平铺是为了「方式 B」下能 `python gateway.py …` 直接跑；
代价是根目录很长。先看这张表，再决定要读哪几个：

| 分组 | 数量 | 你需要它吗 | 模块 |
|---|---|---|---|
| **对外接口** | 5 | ✅ **装完即用的就是这几个** | `memtether`（包门面 + CLI）· `gateway`（唯一写入入口）· `mem`（共享总线 + CLI）· `memsearch`（混合检索）· `mcp_server`（MCP Server） |
| 引擎核心 | 13 | ⚠️ 被上面调用，一般不直接用 | `embed_local` `rerank` `governance` `project` `refuse_live` `refuse_gate` `tool_audit` `memory_sink` `memory_maintenance` `pair_superseded` `mem0_config` `post_turn` `demo_gateway` |
| 运维 / 自检 | 12 | 🔧 自建环境才用 | `hub_score`（四维评分卡）`hub_selfcheck` `hubguard`（并发治理）`attach_hubguard` `preflight`（每轮回答前预取记忆）`publish_pypi` `bootstrap` `board` `approve` `enrich_caps` `sync_memory` `skill_forge` |
| 跨客户端协作 | 2 | 🔧 多个客户端共写同一份文件时才用 | `wslog_append`（共写日志原子追加）`slot_update`（共享槽位原地更新） |
| 迁移 / 一次性 | 6 | ⛔ 一般不用碰 | `migrate_bitemporal` `migrate_sink` `import_mem0` `sync_reflector_mem0` `astra_dialogue` `astra_memory_closure` |
| 评测 / 回归 | 14 | 🔬 想复跑卷子时 | `asset_bench` `asset_bench_holdout` `asset_selfcheck` `bench_longmemeval` `e2e_verify` `hard_bench` `hard_holdout` `judge_selfcheck` `refuse_bench` `regression_test` `rerank_k_bench` `smoke_bitemporal` `test_autosync` `test_triggers` |

- **只想用，不想读源码** → 看第一行那 5 个就够；`pip` 装完之后它们都在 `memtether` 命令背后。
- **想复跑评测 / 想核对我们说的数** → 最后一行是给你的：卷子、判分口径、评分卡源码都在仓库里。
- **想自己搭一套** → 中间两行（运维 / 自检 + 跨客户端协作）。

`scripts/` 放辅助脚本（合成演示库生成、打包清单闸门、泄密扫描）；`docs/` 放文档索引。
名字以 `_` 开头的 `.py` 是本地临时脚本，**不进版本库**（`.gitignore` 已排除）。

> 这份清单由 `scripts/check_packaging.py` 与仓库实际文件核对 —— 不是手抄的。

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

本机实测（2026-09-16）：

| 指标 | 值 |
|---|---|
| 四维评分卡（本机资产类） | 综合 95.5% |
| 外部基准 LongMemEval（60 题抽样） | strict 58.5% / llm 45.8% |

**请连同下面这句一起读这两个数**：评分卡只测「本机资产」这一维，而且主集是修完资产之后才出的卷，
存在"自出卷"偏差。外部基准那一栏才是通用能力的位置。

**已知短板（不藏）**：
- **注入槽位瓶颈**：活跃条目数百条，但受客户端注入上限约束，每轮实际只能喂进几十条 → 存得多、喂得少
- **拒答能力很弱（已实测，不吹）**：检索式系统默认只会返回"最像的"。现已接入拒答判据
  （词项覆盖率 + 相似度双阈值），但**离线标定结果是：零误拒前提下最多拦下 41%（9/22）**，
  理论天花板 59% —— 且"同形不同属性"这类负样本（如"X 的**端口**是多少" vs 记忆里只有"X 的**路径**"）
  **在词形法原理上无解**。当前默认 `warn`（只提示不阻断），不改变原有输出
- 通用能力仅在 1 个基准上跑过抽样，未跑全量
- 双时间轴里 `native`（原生记录）占比很低，多数为回填/推定

---

## 与同类项目的关系

**不比分数** —— 这个赛道的公开分数已被证实大面积不可复现（同一系统自报 92% 与第三方复现 38%）。
MemTether 的卖点是**可验证性**：评分卡源码、评测集、双判分口径、失真警告全部在仓库里，你可以自己跑。

- **mem0 / supermemory**：抽取式记忆层，记什么由模型决定 —— 偏产品化记忆
- **Zep / Graphiti**：时序知识图谱，双时间轴定义即源于此 —— 偏企业级图存储
- **Letta（MemGPT）**：OS 式分页，模型自己编辑记忆 —— 偏 agent 框架
- **engram / agent-memory**：本地优先、单文件/单二进制 —— 工程形态最接近
- **Memmy**：目标最接近（多 agent 共享本地记忆），但是常驻服务 + 商业云侧

---

## 路线图

- [x] 合成 demo 库（对外示例，不含任何真实记忆）
- [x] 标准 `pip` 安装（wheel：平铺模块 + `memtether` 命令 + `[vector]` 可选档）
- [x] 拒答 / 置信度门槛（**默认 warn**；实测零误拒拦截面 41%，天花板 59%）
- [x] 评测集与评分卡源码开源（双判分口径 + 失真警告）
- [ ] 发布到 PyPI（当前只能从仓库装）
- [ ] 一键安装脚本（Windows 优先）
- [ ] 注入槽位策略优化（存得多、喂得少是当前最大瓶颈）
- [ ] 拒答判据从"词形法"升级为"带语义的判据"（词形法对同形不同属性无解）

---

## 声明

- **本仓库不包含任何真实记忆数据。** 示例数据全部为合成。
- 模型权重不在仓库内（体积过大），首次运行时按提示获取。
- 许可：**Apache-2.0**（见 `LICENSE`）；第三方组件与模型权重的归属声明见 `NOTICE`
- 安全问题请走 `SECURITY.md`
