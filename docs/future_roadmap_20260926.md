# MemTether 未来方向 · 深度规划（2026-09-26）

> 基底：2026-09-24《前沿方案全景对照·深度版》+ 2026-09-26 二轮 GitHub 调研（12 项目实核，带 PAT）
> 定位：回答三个问题——方向对不对、竞品空位在哪、CCF 一等奖路径怎么走。极度诚实。

---

## 0. 一句话结论

**方向对，叙事成立，但差三块拼图。** "带治理的搬运"（数据海关）在 GitHub 上仍然是空位——cognee 有交换无治理、MGP 有治理无交换叙事、UMP 有 schema 无引擎、OMP 有名分无深度。MemTether 的 M2 demo 已经证明这条路走得通。但要拿一等奖，缺的不是代码，是**适配器广度、社区数据、LLM judge 评测**这三件硬货。

---

## 1. 本轮调研 · 五强对比（2026-09-26 实核）

| 项目 | Stars | 核心设计 | 与 MemTether 的关系 |
|---|---|---|---|
| **cognee** | 30,986 | 图+向量混合 poly-store；**已有 migration 模块**（Mem0/Zep/Letta/LangMem/CogX 五个源） | **最大发现**。COGX 格式有 valid_at/invalid_at/confidence/provenance[]，但 COGXMemory 只有 content+categories——无双时间轴/supersession/Q-Value。**它是"纯搬运"，我们是"带治理的搬运"** |
| **UMP**（universal-memory-protocol） | 33 | v1.0 完整 schema：bi-temporal + provenance（W3C PROV/DID）+ supersession + consent + BLAKE3 签名 + L0-L3 conformance | **纸面最强竞品，但只有 33★、无真实引擎**。它的 consent 与完整性签名值得吸收进 Schema v2 |
| **MGP**（HKUDS/MGP） | 60 | Memory Governance Protocol v0.1.1：治理网关（policy hook + audit + 8 适配器）；**spec/conflicts.md 有 5 种冲突类型 + 5 种解决模式** | **治理维度最强的早期项目**。它的冲突解决模式枚举（latest_wins / source_priority / confidence_weighted / coexist_with_validity_window / manual_review_required）直接可抄进我们的 Schema v2 |
| **OMP**（open-memory-protocol） | 84 | v0.4，占了 "memory protocol" 名分 | schema 无 supersession/双时间轴，治理深度不足。名分被他占了，但实质空位仍在 |
| **letta agent-file (.af)** | 1,202 | 开放 agent 状态格式，blocks 有 preserve_on_migration | 无治理语义。验证了"开放状态格式"是趋势，但深度不够 |

**空位确认**：GitHub 全文搜 "memory exchange protocol conflict detection" 组合零命中。存在的都是单面。**"数据海关"叙事（导入时冲突检测 + supersession 合并 + 双时间轴随记忆迁移）仍无人做。**

---

## 2. 差异化定位（对外话术）

> "大部分记忆库解决的是单机单点的记忆存储问题。MemTether 解决的是多客户端、多系统之间的**记忆交换与治理**问题。我定义了一套带双时间轴和来源归属的记忆交换协议，并实现了参考代码与冲突检测 demo。未来的 AI 记忆不再被某个平台锁死——用户可以把自己的记忆主权带走，并且保证迁移后不被污染。我不是在做另一个记忆库，我是在做 AI 记忆世界的**数据海关**和**标准集装箱**。"

---

## 3. Schema v2 方向（吸收五强之长）

| 来源 | 吸收什么 | 怎么落 |
|---|---|---|
| UMP | **consent 字段**（记忆的知情同意状态）+ **BLAKE3/SHA3 完整性**（我们现在是 SHA256，可加签名链） | Schema v2 加 `consent` 可选字段 + `integrity_signature` |
| MGP | **5 种冲突解决模式枚举** + policy hook | Schema v2 的 supersessions[] 加 `resolution_mode`；governance 加策略配置 |
| cognee COGX | **provenance[] 数组**（多级来源链） | 我们已有 source（单值），v2 可加 `provenance` 可选数组 |
| letta .af | **preserve_on_migration** 标记 | 我们已有 pin；可加 `preserve` 语义（迁移时必带） |
| OMP | 警示——名分先占者未必赢，深度才是壁垒 | 不抄内容，只抄教训 |

**Schema v2 原则**：向后兼容 v1（v1 文件 v2 引擎可读）；新字段全部 optional；缺失不猜、显式报。

---

## 4. 适配器 roadmap（对标并超越 cognee 的 5 个）

| 优先级 | 适配器 | 理由 | 工作量 |
|---|---|---|---|
| P0 | **Zep** | cognee 已有，双时间轴同源，导入时可以校验语义一致性 | 1-2 天 |
| P0 | **Letta (.af)** | agent-file 格式开放，blocks→facts 映射直接 | 1-2 天 |
| P1 | **Graphiti** | 图结构→扁平 facts 需要设计（节点→subject，边→fact） | 3-5 天 |
| P1 | **LangMem** | cognee 有，API 简单 | 1-2 天 |
| P2 | **cognee COGX 导入** | 直接吃它的 JSONL | 2-3 天 |

每个适配器交付三件套：转换器 + 治理语义映射文档 + 冲突检测 demo 复用（`demo_exchange_conflict.py` 改数据源即可）。

---

## 5. CCF 一等奖 · 极诚实路径

### 5.1 当前位置（2026-09-26）

- **三等奖：八成能拿**（治理完备 + PyPI 已发布 + 评测开源 + M2 demo）
- **二等奖：补齐三块拼图后稳**
- **一等奖：补齐后"有真实可能"，但取决于社区数据**

### 5.2 三块拼图（按优先级）

| # | 拼图 | 现状 | 目标 | 谁来做 |
|---|---|---|---|---|
| ① | **适配器广度** | Mem0 × 1 | Mem0/Zep/Letta/Graphiti/LangMem × 5 | Codex 可独立完成 |
| ② | **社区数据** | 1★ / 0 fork | **10+ 真实用户 / 3+ 外部 contributor / 1+ 外部 PR** | **agent 无法替代——必须推广** |
| ③ | **LongMemEval LLM judge** | 未跑（strict 60.5%） | LLM judge 口径复跑（$3-5 API 费） | Codex，等用户确认预算 |

### 5.3 为什么社区数据是硬门槛

一等奖评委看的不只是技术——是"生态级基础设施贡献"。而生态贡献的硬指标是**别人真的在用**。1★ 0 fork 的项目，无论技术多强，在"社区关注度"这一项上就是零分。agent 可以把代码写到完美，但不能替你找用户。

### 5.4 时间线（结合用户学业节奏）

| 时间 | 动作 |
|---|---|
| 现在 → 12 月 | ①③ Codex 推进（适配器 + LLM judge）；用户学业优先，项目只做"验收式参与" |
| 12 月 → 次年 1 月 | 四级 + 考研数学冲刺，项目冻结 |
| 次年 1-2 月（寒假） | 适配器全部完成 + Schema v2 落地 + demo 视频录制 |
| 次年 2-3 月 | **社区推广窗口**：awesome-list PR、Hacker News / V2EX / 即刻 / X 帖子、找人试用 |
| 次年 3 月 | CCF 报名（材料三张牌：治理叙事 + 交换协议 + 评测诚信） |
| 4-7 月 | 复赛打磨：外部系统对接测试 + 验收文档 + 外部反馈截图 |
| 8 月 | 决赛答辩 |

### 5.5 一等奖答辩叙事（三张牌）

1. **治理牌**：双时间轴 + supersession + source 归属 + PII 四层防线——治理完备度超过 90% 开源项目（实核数据支撑）
2. **交换牌**：Memory Exchange Schema + 5 适配器 + 冲突检测端到端 demo——"数据海关"空位唯一占据者
3. **诚信牌**：评测集 + 评分卡 + 双判分口径 + 失真警告全部开源——在"Benchmark Theatre"泛滥的赛道里，这本身就是差异化

---

## 6. 长期方向（CCF 之后）

| 方向 | 说明 | 参考 |
|---|---|---|
| **PyPI 1.0** | 适配器齐 + 社区反馈吸收后发布 1.0 stable | 当前 0.1.0a8 |
| **Web UI / Dashboard** | 投影/检索/治理可视化（用户此前画过 lifecycle SVG） | 考虑作为独立包 |
| **跨机同步** | 文件级指针单机优势在多机失效；评估 CRDT（Automerge/Yjs）vs 事件溯源 | 先做 issue/discussion 收集需求 |
| **记忆谄媚测试** | MemSyco-Bench（arXiv:2607.01071）维度加入 refuse_bench | 学术支撑已就位 |
| **模型升级 survivability** | bge-m3 升级后旧向量有效性测试（arXiv:2609.05339） | L2 规划 |
| **多机权限模型** | consent + ACL（UMP 路线） | Schema v3 |

---

## 7. 数据来源声明

- GitHub API 实核（2026-09-26，带 PAT，走 Clash 7890 代理）：12 个仓库元数据 + cognee migration 源码 + MGP conflicts spec + UMP schema 全文
- cognee migration 模块：`modules/migration/sources/{base,mem0,zep,letta,langmem,cogx_archive}.py` + `IMPORT_MODES`
- MGP：`spec/conflicts.md`（5 冲突类型 + 5 解决模式，原文逐条读过）
- UMP：schema v1.0 全文（bi-temporal/provenance/supersession/consent/BLAKE3/L0-L3）
- OMP：README + schema v0.4
- letta agent-file：README + 格式说明
- 全部 star 数为 09-26 当日 API 返回值

*所有判断可溯源。未取到的数字标注"未取到"。不编造。*
