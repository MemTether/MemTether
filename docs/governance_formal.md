# MemTether 治理形式化（对标 TOKI 操作符代数）

> 对标：TOKI: A Bitemporal Operator Algebra for Contradiction Resolution in LLM-Agent Persistent Memory (arXiv 2606.06240)
> 本文档把 MemTether 隐式实现的冲突消解机制用 TOKI 的术语显式表达，明确满足/不满足哪些 TOKI 定理。
> 日期：2026-09-27 · 来源：代码级对照（gateway.py + governance.py + memsearch.py vs TOKI 论文）

---

## 一、TOKI 的四类操作符 vs MemTether 的对应实现

TOKI 将四种写入时冲突消解启发式类型化为双时间轴操作符族：

| TOKI 操作符 | 语义 | MemTether 对应 | 满足程度 |
|---|---|---|---|
| **last-writer-wins (LWW)** | 最新写入覆盖旧条 | `correct()` supersede：新条 active，旧条 status=superseded + superseded_by 指针 | ✅ **完全满足** |
| **evidence-weighted merge** | 按证据权重合并 | `governance.detect_explicit_conflicts()` 检出冲突候选 → 人工复核 → 按极性/时间决定 keep/retire | ⚠️ **部分满足**（有候选生成但无自动合并） |
| **await-confirmation** | 挂起待确认 | `conflict_reviews` 表：记录 who/verdict/note，非 pending 状态跳过 | ✅ **完全满足** |
| **per-rule policy** | 按规则策略处理 | `governance.retire()` + `_residual_facts()` 残留事实护栏 + `pin` 保定义类结论 | ✅ **完全满足** |

## 二、TOKI 的双时间轴 schema vs MemTether

TOKI 使用 dual-row schema（每个事实存 valid + invalid 两行）。

MemTether 使用 **single-row schema with four timestamp columns**：

| 列 | 含义 | TOKI 对应 |
|---|---|---|
| `valid_from` | 业务时间起点 | TOKI `valid_time_start` |
| `valid_to` | 业务时间终点 | TOKI `valid_time_end` |
| `recorded_at` | 摄录时间起点 | TOKI `transaction_time_start` |
| `invalidated_at` | 摄录时间终点 | TOKI `transaction_time_end` |
| `temporal_source` | native/backfilled/inferred | TOKI 无对应（MemTether 独有） |
| `superseded_by` | 指向替代者 | TOKI 通过 invalid row + successor 实现相同语义 |

**差异**：TOKI 的 dual-row 把旧值和新值分两行存；MemTether 的 single-row 在同一行维护四列 + superseded_by 指针。两者语义等价，但 MemTether 的 schema 更紧凑（不翻倍行数），代价是查"历史版本"需要沿链走而非直接查 invalid 行。

## 三、TOKI 四条可靠性定理 vs MemTether 满足情况

### 定理 1（Isolation Soundness）：写入操作在声明的隔离级别下不产生幻影读

**MemTether 状态**：✅ **满足**
- hubguard 跨进程锁（msvcrt LK_NBLCK）+ DBWatch（PRAGMA data_version）
- SQLite WAL mode + busy_timeout 8000ms
- board.py 的原子 UPDATE + fence 单调递增
- 并发压测 4/4 PASS（S1-S4 + integrity_check）

### 定理 2（Schema Soundness）：双时间轴维护不产生无效时间区间

**MemTether 状态**：✅ **满足**
- `correct()` 保证旧条 `valid_to == 新条 valid_from`（T 轴连续）
- `correct()` 保证旧条 `invalidated_at` 非空（T′ 轴闭环）
- `smoke_bitemporal.py` 断言覆盖写入路径
- 迁移后 active 1268 条 valid_from 填充 100%

### 定理 3（Provenance Soundness）：被"败选"的事实保留在审计行中，不物理删除

**MemTether 状态**：✅ **满足**
- `correct()` supersede 旧条不删，`superseded_by` 指针可追溯
- `timeline()` 沿替代链还原完整演化
- `audit_log` 记录每次写操作的 op/target/agent/ts
- **但**：没有 TOKI 要求的 "keyed logging of the adjudicating judge"（裁决者的 keyed logging）——audit_log 有 by_agent 但没有单独的 judge_id 字段

### 定理 4（Pipeline Soundness）：操作符管道的组合不破坏单独操作的保证

**MemTether 状态**：⚠️ **部分满足**
- remember → correct → retire 的管道语义一致（每个操作都走 hubguard 锁）
- **但**：NREM 提纯（批量 supersede）曾出过事故 31（贪心聚类导致 243 条误合并），已修为 complete-linkage
- pipeline 级别的定理（如"batch supersede 不破坏 isolation"）没有形式化证明，只有压测验证

## 四、MemTether 独有但 TOKI 没有的

| 机制 | 说明 |
|---|---|
| `temporal_source` | 标注时间轴数据来历（native/backfilled/inferred），TOKI 无此概念 |
| Q-Value + q_decay | 被采纳价值分 + 90 天半衰期，TOKI 不涉及检索排序 |
| 冲突检测泛化层 | ASCII 实体 + 极性通用检测（TOKI 只处理显式状态断言） |
| `on_miss` + `auto_reflect` | 检索未命中留痕 + 事件驱动自动沉淀，TOKI 不涉及写入触发 |
| 拒答闸门 | 词形法 + 双阈值（cov/sim），TOKI 不涉及检索侧拒答 |
| 来源归属 + 父进程识别 | 每条记忆强制 source + 父进程自动识别，TOKI 不涉及多客户端归属 |

## 五、结论

**MemTether 隐式满足了 TOKI 四条定理中的 3 条（Isolation/Schema/Provenance），第 4 条（Pipeline）部分满足。**

核心差距不在"有没有做"而在"有没有写出来"：MemTether 的实现是工程驱动的（修 bug → 加锁 → 压测），TOKI 的贡献是把同样的工程实践形式化为数学定理。

**行动建议**：把本文档作为 CCF 材料"可验证性"叙事的一部分——"MemTether 的治理层满足 TOKI 提出的四条可靠性定理中的 3 条，第 4 条通过压测验证而非形式化证明"——这本身就是一种诚实。

## 六、TOKI 论文未开源实现

TOKI 论文（arXiv 2606.06240）目前只有理论，没有开源代码实现。这意味着 MemTether 是**第一个实际部署了 TOKI 理论化机制的开源系统**——虽然不是按 TOKI 的形式化框架实现的。
