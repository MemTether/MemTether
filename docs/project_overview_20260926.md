# MemTether — 跨客户端 AI 记忆中枢 · 项目完整介绍与未来规划

> **一句话：多个异构 AI 客户端共享同一份物理记忆——文件级指针，而非同步副本。**
>
> 版本：2026-09-26 · 状态：生产运行中 · 面向架构决策者、平台工程师、AI 基础设施评估者
> PyPI：[memtether](https://pypi.org/project/memtether/) · GitHub：[MemTether/MemTether](https://github.com/MemTether/MemTether)

---

## 1. 项目定位

MemTether 是一套部署在本地、面向多 Agent 工作流的记忆基础设施。多个 AI 客户端（Codex、WorkBuddy、OpenClaw 等）通过文件级指针读写同一份 SQLite 数据库，实现记忆的统一写入、混合检索、投影分发与生命周期治理。

### 它不是什么

| 它不是 | 原因 |
|---|---|
| 云服务 | 全部数据与推理在本机，零外部 API 依赖 |
| 跨机器同步 | 单机部署，文件指针天然避免副本漂移 |
| mem0 复刻 | 独立设计双时间轴 + supersession + source 归属的治理模型 |
| RAG 系统 | 不是文档问答，是 Agent 长期记忆的持久化与上下文注入管理 |
| 向量数据库封装 | 混合检索 + 治理 + 投影是一条完整管线，不只是"加个 embedding" |

### 目标用户

- **多 Agent 重度使用者**：同时使用 3+ 个 AI 客户端（编程助手、通用对话、自动化 Agent），需要跨客户端延续工作上下文
- **AI 应用开发者**：为产品构建 Agent 记忆层时，需要一套带治理语义（版本链、时间轴、归属）的开源基座
- **企业本地化部署团队**：数据不出内网是硬性合规要求，需要零云依赖的记忆方案
- **隐私敏感场景**：个人助手记忆含敏感信息，导出/交换必须带 PII 脱敏与完整性校验

---

## 2. 架构：四条管线 + 第五层技能

### 2.1 写入管线（gateway）

所有记忆经 `gateway.py remember` 入库，强制携带：

- **source**：来源客户端标识（已注册 15 个），CLI 缺失 `--source` 时 fail-closed 拒写
- **type**：`fact` / `decision` / `incident` / `experience` / `procedure`
- **content**：首句必须是结论（投影只取首句）

写入时自动执行：
1. **敏感信息闸门**：完整 API key（sk- / ghp_ / AKIA 等）入库前直接拒绝
2. **HubGuard 并发锁**：10 个写入口全部挂锁（超时 120s，高并发压测通过）
3. **近重复检测**：near-dup 自动拒绝
4. **审计日志**：每次写操作记录 op / target / agent / ts

### 2.2 检索管线（memsearch）

- **向量引擎**：本地 BAAI/bge-m3（1024 维，int8/fp16），断网可跑，零 API 费用
- **融合排序**：语义 + 关键词双路召回 → RRF 融合 → 精排（semantic boost ×1.8 抢救被关键词噪声埋没的高相似候选）
- **Q-Value 权重**：`score = similarity × (0.3 + 0.7 × q_value)`，命中被采纳后回写
- **时间衰减**：`q_decay = 0.5^(days/90)`，90 天半衰期
- **TTL 衰减**：状态类过期条目 ×0.35 + 显式标注
- **自指惩罚**：提到"记忆检索"本身的条目降权，防反馈循环
- **索引一致性闸门**：漂移超阈值自动重建

### 2.3 投影管线（rebuild）

- **目标**：各客户端的 MEMORY.md，预算 3980 字符（与官方注入槽位上限对齐）
- **策略**：band 模式（短条优先 + pin 最高优先 + Q-Value 带内优先）+ 类型感知压缩（decision/pin 全保真、experience 压缩）—— 同样预算装的信息质量更高
- **冲突行处理**：逐行适配，绝不硬截半句
- **预算不足**：显式告警，不静默截断

### 2.4 治理管线（governance + hubguard）

- **双时间轴**：`valid_from/valid_to`（业务时间）+ `recorded_at/invalidated_at`（记录时间），全量覆盖
- **Supersession**：`correct()` 不删旧，只建指针，完整版本链可追溯
- **冲突检测**：Reflector 自动标记 → `conflict_reviews` 复核
- **Retire / Quarantine / Pin**：退役而非删除；pin 保定义类结论免于被时间序挤出
- **保护名单**：RELEASE_OWNED / GUARDED，防误改

### 2.5 第五层：技能锻造

- **记忆**管「我记得什么」，**技能**管「我怎么做」—— 同一条链，不是两个项目
- `skill_forge` 沉淀闭环 → `skill_budget` 预算守卫（当前 103 技能 / 37,266 字符）→ 中立真源 + junction 分发到多客户端
- `skillctl` 统一入口：audit / link / dup / health / scan

---

## 3. 数据规模（2026-09-26 快照）

| 维度 | 数值 |
|---|---|
| 总记忆条数 | 1,322 |
| 活跃 | 1,227 |
| 投影利用率 | 3,650 / 3,980 = **91.7%** |
| pin 条目 | 7/7 全部入投影 |
| Git HEAD | `b456dee`（= origin/main，干净） |
| PyPI | **memtether 0.1.0a8** |

---

## 4. 评测体系（可复现，评分卡源码开源）

| 基准 | 结果 | 口径说明 |
|---|---|---|
| hard_bench | **61/62 = 98.4%** | 外部知识+本机资产双源 |
| asset_bench | **23/23 = 100%** | 纯本机资产类（存在自出卷偏差，已在 README 声明） |
| LongMemEval 500题 | **strict 60.5%**（k=12） | 检索式 harness 口径；LLM judge 口径未跑（需 API 费） |
| HotpotQA 100题 | **strict 88.3%** | 检索式 harness 口径 |
| 拒答基准 26题 | **hr=0.66 拦 50% / 误拒 4.5%；hr=0.64 零误拒拦 46%** | 两个工作点，按代价自选 |
| PII round-trip | **0 残留** | 脱敏→序列化→导入→逐表扫描 |
| 并发压测 | 多客户端同写无脏写 | hubguard 跨进程锁 |

**评测诚信声明**：本赛道的公开分数已被证实大面积不可复现（同一系统自报 92% vs 第三方复现 38%）。MemTether 不与别家比数值——所有评测集、评分卡源码、双判分口径、失真警告全部在仓库里，可自己跑。

---

## 5. 安全治理（四层防线）

| 层 | 做什么 | 验证 |
|---|---|---|
| PII 脱敏 | 导出快照自动扫描并脱敏手机号/邮箱/API key 等 | `test_pii_roundtrip.py` 0 残留 |
| OWASP 运行时防御 | `memtether_guard` 检测注入/越权/泄露模式，命中即拦截 | `guard --selftest` ALL PASS |
| 泄密扫描闸门 | 打包前逐表扫全文；词表缺失时显式说"无法判定"而非"零命中" | `scan_leaks.py` BLOCK=0 |
| 并发治理锁 | hubguard 对 10 个写函数加锁 | N8 并发压测通过 |

---

## 6. 跨系统记忆交换（M2 · 2026-09-26 落地）

这是本项目从"本地工具"迈向"记忆基础设施标准"的关键一步：

- **Memory Exchange Schema v1**（`memtether_exchange.py`）：source / 双时间轴 / supersession / Q-Value / sha256 完整性，作为不可拆的最小治理单元随记忆一起迁移
- **Mem0 适配器**（`mem0_exchange.py`）：缺失字段显式回填，不伪造治理语义
- **冲突检测端到端 demo**：导入 20 条 Mem0 风格数据 → 2 重复去重 + 2 组极性冲突检出 → 自动退役 → 复检 0 冲突 → 全流程 PASS
- **定位**：**"带治理的搬运"**—— cognee 的 COGX 已有 5 个纯搬运适配器（Mem0/Zep/Letta/LangMem），但搬运时不做冲突检测、不带治理语义。MemTether 是目前唯一把冲突检测 + supersession + 双时间轴放进交换协议的实现

---

## 7. 客户端自动接入

`memtether-connect` 把每个客户端不同的 MCP 配置 schema 变成一条命令：

```bash
memtether-connect detect   # 发现本机客户端
memtether-connect plan     # 预演，不写盘
memtether-connect apply    # 写入（先备份，可回滚）
memtether-connect verify   # 校验
```

- **23 个适配器**（16 standard + 7 local），每个都有 win/mac/linux 三路路径
- **不猜**：找不到约定根路径就 fail-closed，绝不"大概写在这儿"
- **保注释**：JSONC 感知，只改该改的那一处
- **幂等**：语义一致就一个字节都不写
- **来源自动识别**：父进程映像名 + 命令行两级，零配置；刻意不依赖 env（env key 参与 Electron 信任 hash，多一个就静默掉信任）

---

## 8. 与同类项目的对比（2026-09-26 实核）

| 能力 | **MemTether** | mem0 | Zep/Graphiti | Letta | cognee | delx-memory |
|---|---|---|---|---|---|---|
| 多客户端共享同一份物理记忆 | ✅ 文件级指针 | ❌ 各存副本 | ❌ 服务端 | ❌ agent 内 | ❌ 图库 | ✅ KV 级 |
| 双时间轴 | ✅ | ❌ | ✅ | ❌ | ⚠️ COGX 有字段但 Memory 无 | ❌ |
| supersession 版本链 | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Q-Value + 时间衰减 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 交换协议带冲突检测 | ✅ M2 demo PASS | ❌ | ❌ | ❌ | ❌ 纯搬运 | ❌ |
| 本地 embedding | ✅ bge-m3 | ⚠️ 需 API | ⚠️ 需 API | ⚠️ 需 API | ⚠️ | ❌ |
| PII 脱敏 + 完整性校验 | ✅ round-trip 0 残留 | ❌ | ❌ | ❌ | ❌ | ⚠️ secret-blocking |
| 客户端自动接入 | ✅ 23 适配器 | ⚠️ 手动 | ⚠️ 手动 | ❌ | ⚠️ | ⚠️ |

> 对比信息来自各项目公开文档与 GitHub API 实核（2026-09-26，带 PAT）。delx-memory 与 cognee 的代码逐个看过。

---

## 9. 技术栈与部署

| 层 | 技术 | 说明 |
|---|---|---|
| 数据库 | SQLite 单文件 | WAL 模式，无外部依赖 |
| 向量 | BAAI/bge-m3 本地 onnxruntime | 1024 维，CPU 推理 |
| 检索 | 语义 + 关键词 + 字面 → RRF → 精排 | 全离线 |
| 投影 | Markdown | 各客户端 MEMORY.md |
| 技能 | SKILL.md 中立真源 + junction | skillctl 统一入口 |
| MCP | memory-hub MCP server | 23 客户端适配器 |
| 发布 | PyPI + GitHub Release | Apache-2.0 |

**部署**：Windows 10+ / Python 3.10+ / ~50 MB / 零 GPU / 零外部 API 费用。macOS/Linux 路径已实现，待社区反馈。

---

## 10. 未来规划（摘要 · 详见配套文档）

### 短期（1-2 周）
- **Exchange Schema v2**：吸收 UMP 的 consent/BLAKE3 完整性与 MGP 的 5 种冲突解决模式
- **适配器 roadmap**：Zep / Letta / Graphiti / LangMem（对标 cognee 的 5 个，做到带治理版）

### 中期（1-2 月）
- **LLM 压缩**（NREM 提纯 · 需少量 API 预算）
- **社区冷启动**：10+ 真实用户 / 3+ 外部 contributor（这是 CCF 的硬门槛，agent 无法替代）
- **LongMemEval LLM judge 复跑**（$3-5）

### 长期（3-6 月）
- **上下文工程统一 harness**（投影/检索/TTL/Q-Value/技能整合为可配置层）
- **跨机同步探索**（CRDT / 事件溯源）
- **CCF 开源创新大赛**（2027-03 报名）

---

## 11. 为什么值得用

1. **不丢记忆**——双时间轴 + supersession 版本链，每次更正可追溯原始版本
2. **不串台**——source 归属强制 + 父进程自动识别，多客户端归属不串号
3. **不泄密**——PII 入库拦截 + 导出脱敏，round-trip 0 残留
4. **不花钱**——100% 本机运行，零 API 费用，零云依赖
5. **不折腾**——SQLite 单文件，拷走即迁移；pip install memtether 即装
6. **治得了**——Retire / Quarantine / Pin / Conflict / 保护名单全套治理
7. **测得出**——评分卡源码、评测集、双判分口径全部开源
8. **搬得走**——Exchange Schema v1 让治理语义随记忆一起迁移，不做数据孤岛

---

## 12. 费用声明

从立项至今（2026-09-05 → 2026-09-26），**累计外部 API 费用 ¥0**。

---

*本文档数据全部来自 2026-09-26 实测快照（SQLite 直查 + benchmark 跑分 + GitHub API 实核）。未取到的数字标注"未取到"，不编造。*
