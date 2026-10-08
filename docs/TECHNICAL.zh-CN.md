# MemTether 技术文档

> 面向开发者的架构与实现细节。项目介绍见 [INTRO.zh-CN.md](INTRO.zh-CN.md)。

---

## 1. 架构总览

MemTether 是一个**平铺模块的 Python 单仓**（非多层包，30 个根模块 + clients/integrations/scripts 包），核心链路四条：

```
写入链路    gateway.remember() → 验证 → 查重 → 谓词抽取 → SQLite + 审计日志 + 向量索引
检索链路    memsearch.search_hybrid() → 质量门禁 → 四路召回 → RRF 融合 → 精排 → 衰减 → 去重
投影链路    gateway.rebuild() → sink.json + MEMORY.md 导航版（预算硬约束 + 类型配额 + pin 保序）
治理链路    governance.* → 冲突检测 → 人工裁决 → retire（替代不删除）→ 记忆法庭卷宗
```

### 模块地图（核心模块，全部 30 个见仓库根）

| 模块 | 职责 | 覆盖率 |
|---|---|---|
| `gateway.py` | 唯一写入口 + CLI 门面（remember/correct/retire/as_of/timeline/stats/qvalue/rebuild） | 54% |
| `memsearch.py` | 混合检索（四路召回 + RRF + 精排 + 衰减 + 去重 + 分支策略） | ~57% |
| `governance.py` | 冲突检测（精确+启发式）、衰减、退役、评分卡 | 57% |
| `memory_court.py` | 哈希链锚点 + 全链验证 | 98% |
| `predicates.py` | 谓词抽取（写入侧）+ 属性覆盖校验（检索侧） | 95% |
| `hubguard.py` | 审计格式化 + 投影写入守卫（原子写 + 并发锁） | — |
| `tether_connect.py` | 23 客户端适配器接线器（detect/plan/apply/verify/rollback） | 49% |
| `api_server.py` | REST API（FastAPI，14 端点） | — |
| `mcp_server.py` | MCP Server（stdio） | — |
| `consolidation.py` | 巩固索引（价值史 + 常驻指令） | 67% |
| `predicates.py` / `extract.py` | 结构化抽取 | — |
| `clients/` | 适配器实现（23 个客户端） | 契约测试 115 用例 |
| `mem0_exchange.py` / `zep_exchange.py` | 外部记忆格式互转 | 75%/69% |
| `tool_audit.py` | 工具资产普查/校验 | 62% |
| `memtether_pipeline.py` | PII 脱敏 + 注入检测统一管线 | 52% |
| `memtether_paths.py` | 路径解析单点（MEM_DB/MEM_HUB_DIR） | — |

## 2. 数据模型

### 2.1 facts 表（核心）

```sql
CREATE TABLE facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,              -- fact-<ts>-<rand> 稳定唯一 id
  type TEXT,                    -- fact/decision/incident/preference/environment/...
  subject TEXT,                 -- 主语
  content TEXT NOT NULL,
  status TEXT DEFAULT 'active', -- active/superseded/retired/conflicted/frozen
  superseded_by TEXT,           -- 指向替代它的 uid（替代链的边）
  valid_from TEXT,              -- T 轴：现实世界开始成立
  valid_to TEXT,                -- T 轴：现实世界停止成立
  recorded_at TEXT,             -- T' 轴：系统第一次记录
  invalidated_at TEXT,          -- T' 轴：系统认定失效
  temporal_source TEXT,         -- native/backfilled/inferred
  source TEXT,                  -- 写入来源（强制归属）
  scope TEXT DEFAULT 'shared',  -- shared/private/restricted
  confidence REAL DEFAULT 0.8,
  tags TEXT,
  q_value REAL DEFAULT 0.5,     -- Q-Value 价值分
  use_count INTEGER DEFAULT 0,  -- 采纳次数
  predicate TEXT                -- 谓词 JSON（实体→属性对）
);
```

**设计要点**：
- **双时间轴**：`valid_from/valid_to`（T）与 `recorded_at/invalidated_at`（T'）独立。写入时可显式传 `valid_from` 表示"事实其实早就成立"，实现"事实何时成立"与"系统何时知道"分离
- **替代不删除**：`status='superseded'` + `superseded_by` 指针，旧条永久保留，`valid_to`/`invalidated_at` 记录失效时刻
- **归属强制**：`source` 必填（1-64 字符），未注册来源会被降级标记

### 2.2 辅助表

| 表 | 用途 |
|---|---|
| `supersessions` | 替代链边表（old_uid, new_uid, reason, by_agent, ts） |
| `audit_log` | 审计日志（op/target/agent/detail/ts），memory_court 在此上建哈希链 |
| `conflict_reviews` | 人工冲突裁决（uid_a/uid_b/verdict），已裁决的不再重复报 |
| `tool_assets` | 工具资产（与 facts 同池检索，有独立 q_value） |
| `run_events` | 事件队列（incident/retrieval_miss → 反射器提炼） |

## 3. 写入链路（gateway.remember）

```
remember(content, type, source, scope, ...)
  ├─ 参数验证（长度≤10240/type 白名单/source 1-64/confidence 0-1/scope 枚举）
  ├─ TTL 提醒（状态类结论建议带 ttl，写入 stderr 警告）
  ├─ init_db()（幂等，含列迁移）
  ├─ 精确查重（同 source + 同 content → noop_dup，更新时间戳）
  ├─ 近义去重（向量相似度 ≥0.92 + 文本确认 → 走 absorb 逻辑）
  ├─ INSERT + audit_log
  ├─ 谓词抽取（extract_predicates → facts.predicate JSON）
  └─ 向量索引同步（可选 chromadb）
```

**Q-Value 更新**（显式接口）：`bump_qvalue(uid, reward)` → `q += LR·(reward - q)`，reward∈[0,1]，单调收敛到长期平均采纳率。检索侧因子 `score *= (0.3 + 0.7·q)`——q=0.5（默认）时因子恒 0.65，对同批候选是常数，不改变既有排序。

## 4. 检索链路（memsearch.search_hybrid）

```
search_hybrid(query, limit, ...)
  ├─ 查询扩展（synonyms.json 同义词）
  ├─ 质量门禁（垃圾内容过滤）
  ├─ 四路召回
  │   ├─ 语义向量（bge-m3，可选，缺库降级跳过）
  │   ├─ BM25（FTS5）
  │   ├─ 关键词 IDF
  │   └─ 字面精确匹配
  ├─ RRF 融合（K=60，关键词权重 1.6）
  ├─ 实体图 PPR 多跳扩展（可选）
  ├─ consolidation 索引补充（standing instructions 高位提升）
  ├─ cross-encoder 精排（bge-reranker，可选）
  ├─ 时间衰减（30 天半衰期，floor 0.35）
  ├─ Q-Value 加权
  ├─ 属性覆盖校验（"X的Y"问句 → 只提 X 不提 Y 的候选降权 ×0.15）
  ├─ Recall Budget（MEM_RECALL_BUDGET=6000 字符硬上限 + min-max 归一化）
  ├─ 多轮检索（可选，首查分数不足时简化 query 重搜一轮）
  ├─ 三层去重（supersession → content → tag 签名）
  ├─ 低置信标记（kw 空 + vec < 0.5）
  ├─ scaffold/AFAG 提示（counting/comparison/temporal 等题型生成推理脚手架）
  └─ 返回 {query, results[], diag{}}
```

## 5. 治理链路

### 5.1 冲突检测（三级）

| 级别 | 机制 | 误报率 | 用途 |
|---|---|---|---|
| 精确 | 实体 + 显式状态断言（正则 + 别名归一化），跨条极性相反 | 低 | 自动生成退役候选（需 --apply 才写库） |
| 启发式 | 句级极性 + 强实体 + 时间窗 | 高（诚实声明） | 仅作人工复核候选 |
| 自相矛盾 | 同一条记忆内同实体正负极性并存 | — | 人工复核 |

人工裁决通过 `conflict_reviews` 表留痕（uid_a/uid_b/verdict），`reviewed_no_conflict()` 供评分卡排除已否定对。

### 5.2 退役（retire）

- 默认 dry-run：只报告将要做什么
- **残留事实护栏**：若记忆中混有与冲突无关的独立事实（如一条里既有失效结论又有仍有效的价格数据），自动退役被**拒绝**，需 `--force` 显式覆盖
- 退役 = 置 `status='superseded'` + 填 `valid_to`/`invalidated_at` + `superseded_by`，**不删除任何数据**

### 5.3 记忆法庭（memory_court）

```
anchor 生成：每 10 条审计日志或强制触发 → SHA-256 锚点
验证：全链重算（GENESIS → 逐条 hash(prev_hash + canonical_row)）
卷宗：五段式（内容 / 双时间轴 / 溯源链 / 冲突裁决 / 完整性证明）
导出：JSON 证据包（fact + audit_entries + 元数据），浏览器可独立验证
```

设计取舍：全链重算而非 Merkle 树——本地优先场景下数据量有限，全量重算更简单且无需受信第三方锚点服务。

## 6. 投影链路（gateway.rebuild）

投影是"给客户端注入用的浓缩视图"：

- **sink.json**：全量兼容导出（active facts 按 type 分组）
- **MEMORY.md 导航版**：每条只取首句（决策类不截断、experience 截 100 字），总预算硬上限（默认 3980 字符），超预算按时间倒序丢最老的
- **pin 机制**：tags 含 `pin` 的条目无条件进投影（防定义类知识被新记忆挤出）
- **原子写**：hubguard.atomic_write + 并发锁 + 写前快照，两侧实例共写同一文件时不互相静默覆盖
- **隔离测试**：`MEM_SINK_PATH` + `MEM_PROJ_PATH` + `HG=None` 三件套可完全沙箱化

## 7. 接入层（tether_connect）

```
memtether-connect detect   # 只读探测：23 客户端 × 各自配置落点
memtether-connect plan     # 预演（绝不写盘）
memtether-connect apply    # 落盘（自动备份 + 幂等 + 失败关闭）
memtether-connect verify   # 回读 + 信任状态
memtether-connect rollback # 从备份 manifest 还原
```

设计原则：
- **幂等**：已接入且语义等价 → noop 不写盘（分隔符风格差异视为等价）
- **失败关闭**：解析不了/schema 不认识/根路径缺失 → 报错跳过，绝不猜着写
- **只注册真实接入的客户端**：agents.json 是归属可信的判据表，没装的客户端绝不注册（防归属污染）
- **备份 + manifest**：任何写盘前备份，可一键回滚

适配器需实现的契约（全部有测试）：`candidates()` / `probe()` / `write()` / `read_back()` / `trust()` / `needs_restart`。

## 8. Exchange（跨记忆系统互操作）

Memory Exchange Schema v2（自描述 JSON）：

```
schema_name / schema_version / exported_at / producer / counts
facts[] / supersessions[] / tool_assets[]
sha256（内容哈希）
```

- `mem0_exchange`：Mem0 导入/导出（JSON/JSONL/多字段形态归一）
- `zep_exchange`：Zep v2 导入/导出（含嵌套 message）
- 导入时双时间轴字段缺失显式回填 `temporal_source="backfilled"`（不伪造来源）
- 导出侧自动 PII 脱敏（可关）
- 冲突策略：latest_wins / source_priority / merge_concat / human_review

## 9. 测试与质量

```
466 passed, 4 skipped
CI: 9 jobs（ubuntu×2 + windows×2 + macos×2 + Docker boot + lint + build）
```

测试结构（部分）：

| 测试文件 | 用例 | 覆盖点 |
|---|---|---|
| test_all_adapters_write.py | 115 | 23 适配器 × 5 契约 |
| test_memory_court.py | 9 | 哈希链/篡改检测 |
| test_predicates.py | 12 | 抽取/存储/覆盖校验 |
| test_governance_detect.py | 8 | 精确冲突检测 |
| test_gateway_deep.py | 16 | 双时间轴/验证矩阵 |
| test_gateway_qvalue.py | 11 | Q-Value 契约 |
| test_gateway_rebuild.py | 7 | 投影管线/幂等 |
| test_tether_connect_deep.py | 13 | 接线器 |
| test_exchange_adapters_io.py | 13 | mem0/zep 互转 |
| test_memsearch_branches.py | 10 | 检索分支 |
| test_security_redteam.py | 6 | 注入/超长/限流 |

隔离测试三件套：`MEM_DB` + `MEM_SINK_PATH` + `MEM_PROJ_PATH`（+ `HG=None`）——全部写路径可沙箱化，测试不碰真实数据。

## 10. 安全模型

| 层 | 机制 |
|---|---|
| 写入 | 参数验证（长度/类型/来源/scope/置信度）、归属强制、精确查重 |
| 检索 | scope 过滤（private 不进默认检索）、租户隔离（tenant_id）、限流 60/min（API 模式） |
| 导出 | PII 脱敏（手机/邮箱/API Key/AWS/Slack）+ 用户确认 |
| 运行时 | OWASP 规则守卫（prompt injection/代码执行/敏感凭证检测，三级严重度） |
| 完整性 | 审计哈希链，篡改必红 |
| Dashboard | XSS 全转义、API Key 认证（server 模式） |

已知限制（诚实声明）：
- 单文件 SQLite 的并发写入依赖 WAL + busy_timeout，极端并发下仍有锁竞争窗口
- 多租户隔离是**逻辑隔离**（scope/tenant_id 过滤），非物理隔离——真正的多租户硬隔离需要 server 模式，与 local-first 定位矛盾，暂不实现
- 语义向量索引（chromadb）与 SQLite 之间用 `reconcile` 命令对账，删除操作可能产生 ghost vector（已自动清理）

## 11. 路线图

- 公开基准工件包（fixture digests，目标 2026-12）
- REST API 端点扩展（当前 14 个）
- 更多客户端适配器
- topic timelines 巩固索引（需写入侧实体抽取配套，见 CHANGELOG a60 的移除说明）

## 12. 贡献

见 [CONTRIBUTING.md](https://github.com/MemTether/MemTether/blob/main/CONTRIBUTING.md)。所有数字鼓励独立复现——复现指南见 https://memtether.github.io/MemTether/site/zh/reproduce.html
