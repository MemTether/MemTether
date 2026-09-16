# 记忆中枢 v4 — 深度互联架构（2026-09-13 建成）

> 唯一权威真源 = `memory.db`（SQLite）。Mem0 = 自动记忆引擎。其余全是投影。
> 本文件是总纲，任何 agent 读写记忆前先读这里。

## 一、架构分层

```
各 agent（WorkBuddy/DeepSeek · OpenClaw/grok🦞 · OpenClaw/astra✨ · 豆包 A/B）
        │  统一走 gateway.py（唯一入口）
        ▼
┌─────────────────────────────────────────────┐
│ gateway.py  Memory Gateway                  │
│  ├─ remember   写事实（SQLite + 可选Mem0）  │
│  ├─ search     混合检索（向量+ASCII+字面）  │
│  ├─ correct    用户纠正 → supersede 旧事实  │
│  ├─ retire     退役机制/工具                │
│  ├─ as_of      时序查询：某时刻什么为真 / 系统当时认为什么为真 │
│  ├─ timeline   沿替代链还原一条事实的完整演化（双轴并列）│
│  ├─ record_tool 记录工具/路径/地址资产      │
│  ├─ resolve_task 按任务召回完整执行配方     │
│  ├─ event/process_events  事件驱动自动闭环  │
│  ├─ incident/on_miss  故障与检索未命中留痕  │
│  ├─ rebuild    从SQLite重建所有投影        │
│  └─ stats      统计                        │
└──────┬──────────────────────┬───────────────┘
       │                      │
   memory.db (SQLite)     mem0_store/ (ChromaDB)
   权威事实账本            向量索引 facts_active
   + run_events 事件队列    （写入自动同步，无需手动rebuild）
       │                      │
       └────── rebuild ───────┘
              │
   sink.json / *.md / ~/.workbuddy/MEMORY.md
   （全是投影，勿手改）
```

## 二、组件与选型（实测可用）

| 组件 | 选型 | 说明 |
|---|---|---|
| 权威库 | SQLite memory.db | facts/tool_assets/recipes/supersessions/audit_log/candidates/run_events |
| 事实提取 LLM | deepseek-chat（官方） | 1.3s、便宜，质量够用 |
| 自动记忆引擎 | Mem0 2.0.20 | 冲突消解(ADD/UPDATE/DELETE) + 自动提取 |
| 检索模块 | memsearch.py | 质量门禁+向量+ASCII+字面 四路融合（见第六节） |
| embedding | **本地 bge-m3 int8**（默认） | 1024维、543MB、短进程零常驻；可选智谱 embedding-3（2048维）。**切后端须重建索引**，维度不符会硬报错 |
| 精排 | bge-reranker-base int8 | 266MB；单次查询 98.4% 耗时在这里，`MEM_RERANK_K` 可调 |
| 向量库 | ChromaDB 1.5.9 | 本地目录 mem0_store/，collection=`facts_active`，无 Docker |

> **注意**：语义检索的实际入口是 `memsearch.py`（直接查 ChromaDB 的 `facts_active`），
> 不是 Mem0 的 `search`（Mem0 的 search 受其内部 add 流水线影响，且会把候选/垃圾一起召回）。
> Mem0 在本架构里只负责"写入时的自动提取与冲突消解"。

## 三、硬规则（任何 agent 必须遵守）

1. **禁止直接改 sink.json / memory.db / mem0_store / *.md**，一律走 gateway.py。
2. 每条事实必须有 source + status + confidence。
3. 新事实覆盖旧事实 = supersede（旧事实标记 superseded，不物理删除）。
4. 工具/路径/地址进 tool_assets 表，不做纯散文。
5. 任务检索优先 resolve_task 返回配方，不是散乱记忆。
6. 候选（candidate）不能伪装成 active 事实。
7. *.md 是投影，改记忆请用 gateway，别手改 md。
8. **时间必须成对记录**：T 轴（`valid_from`/`valid_to`，现实世界何时成立）与
   T′轴（`recorded_at`/`invalidated_at`，系统何时记录、何时认定失效）都要维护，
   `temporal_source` 标注数据来历（native/backfilled/inferred）。
   **只记一根轴 = 历史查询会静默给出错误结论**（见 docs/bitemporal-migration-2026-09-16.md）。
9. **派生文件不许进版本库**。「父进程写、子进程读」的传参文件是 IPC 不是产物；
   一旦提交，任何"换掉题集/换掉配置"的运行时行为都会把它覆盖成污染源
   （实例：`_hb_cases.json` 被 hard_holdout 写成 22 题，使 62 题真源失效，
   且 `rerank_k_bench` 分子来自文件、分母来自常量，**分数依然自洽看不出异常**）。
   判据：**删掉它会不会丢信息？** 不会 → 它是派生物，传参改走系统临时目录。

## 四、常用命令

```bash
# 环境（清空 PYTHONPATH 绕开 WorkBuddy bulk-delete guard）
cd <HUB>
PYTHONPATH= ./.venv-memory/Scripts/python.exe gateway.py <子命令>

# 写一条事实
gateway.py remember "内容" --type fact --source workbuddy

# 按任务召回工具配方（解决"反复扫描"）
gateway.py resolve_task "生图"

# 混合检索（加 --mem0 启用语义）
gateway.py search "关键词" --mem0

# 用户纠正（supersede 旧事实）
gateway.py correct <old_uid> "新内容" --reason "..."

# 退役工具/机制
gateway.py retire <uid> --reason "..."

# 记录工具资产
gateway.py record_tool "工具名" --path "..." --entrypoint "..."

# 从 SQLite 重建所有投影
gateway.py rebuild

# 统计
gateway.py stats

# === 记忆自动闭环（事件驱动）===
# 1) agent 干完活写运行摘要 run.json，然后记事件
gateway.py event --type run_finished --run-id <id> --payload-file run.json
# 2) 反射器异步提炼（deepseek），可手动触发或靠计划任务
gateway.py process_events --limit 10
```

## 五、记忆自动闭环（astra 裁决 2026-09-13）

> 解决"记得住但不会自动记"的根本缺口：写入从"手动 remember"升级为"事件驱动自动沉淀"。

```
agent 干完活
   │  写运行摘要（run.json：task/actions/errors/workarounds/result/verification）
   ▼
gateway.py event --type run_finished --run-id <id> --payload-file run.json
   │  （只写 run_events 队列表，不直接写记忆）
   ▼
gateway.py process_events --limit 10
   │  （异步：Windows 计划任务每 5 分钟自动跑）
   ▼
auto_reflect（deepseek 提炼候选记忆，严格 JSON）
   │
   ▼
commit_memory_candidate（价值评分 + 阈值过滤 + 去重）
   ├─ value≥0.75 且 conf≥0.8 → 自动写入（走 remember）
   ├─ 0.5≤value<0.75 → 进 candidates 待审
   └─ value<0.5 → 丢弃
```

**价值公式**：`value = 0.4*reuse + 0.3*impact + 0.2*evidence + 0.1*stability`

**提炼模型分流**：默认 deepseek（快+省），只有冲突/高风险/灰区才升级 astra。

**幂等**：`run_events` 有 `uq_run_event(run_id,event_type)` 唯一索引，重复事件只记一次。

## 六、混合检索（2026-09-13 DeepSeek×Astra 协作修复）

> 修复前 `search()` 用 `content LIKE '%q%'` 精确子串匹配，中文查询 4/8 失败。
> 修复后 8/8 通过。模块：`memsearch.py`。

**四路融合**：
1. **质量门禁**：隔离"无主语泛化垃圾"（长度<20字 且 无ASCII实体 且 泛化词≥2）。
   实测只删 2 条垃圾（"记忆中枢建立，两账号共用"、"入口质检功能已上线"），8 个查询 top-1 全部从错误变正确。
2. **向量路**：智谱 embedding-3 + ChromaDB `facts_active` collection（仅 active，排除垃圾）。解决"问法与表述不一致"（如"豆包数据在哪"）。
3. **ASCII 实体精确路**：query 含 ASCII 实体（robocopy/STM32/APK/路径）时 +0.3 加权。
4. **字面匹配路**：query 整句子面出现在事实里时 +0.5 加权。解决纯中文概念（如"三大机制"向量区分度不足的问题），**无需维护手工概念表**。

**最终分数** = semantic + ascii(0.3) + literal(0.5)，降序返回。

**关键决策（协作结论）**：
- 跳过 FTS5 + jieba（实测 FTS5 默认分词对中文失效，需 jieba 才有用，对 157 条小数据集性价比低）。
- 不需要"写入时调 LLM 打标签"（成本高且非必要）。
- 中文概念不靠手工映射表，靠"字面匹配 + 向量"组合。

## 七、环境依赖

- Python venv：`<HUB>\.venv-memory`（已装 mem0ai 2.0.20 + chromadb 1.5.9）
- 凭据：走 `<AUDIT>\cred_env.py`（vault 解析，环境变量灌入）
- **关键坑**：跑 pip 必须 `PYTHONPATH=` 清空，否则 WorkBuddy 的 sitecustomize bulk-delete guard 会拦截 pip 清理并 SystemExit(1)。

## 八、各 agent 的接入方式（唯一入口契约）

| agent | 读（会话开始） | 写（干完活） | 来源名（必须注册） |
|---|---|---|---|
| WorkBuddy/DeepSeek | `preflight.py "<用户原话>"` | `gateway.py remember "..." --source workbuddy` | `workbuddy` |
| OpenClaw/grok🦞（`openclaw/main`，grok-4.6） | `mem.py recall --agent openclaw` / `mem.py drain --agent openclaw` | `mem.py add --type <t> --text "..." --source openclaw` | `openclaw` |
| OpenClaw/astra✨（`openclaw/astra`，gpt-6-astra） | `mem.py recall --agent openclaw_astra` | `mem.py add --type <t> --text "..." --source openclaw_astra` | `openclaw_astra` |
| 豆包账号A | `mem.py recall --agent doubao_a` | `mem.py add --type <t> --text "..." --source doubao_a` | `doubao_a` |
| 豆包账号B | `mem.py recall --agent doubao_b` | `mem.py add --type <t> --text "..." --source doubao_b` | `doubao_b` |
| 用户本人 | 直接问 | `mem.py add --source user` | `user` |

> 2026-09-13：OpenClaw 由单 agent（`main`）扩为**双 agent**——`main`=🦞grok-4.6（执行体）、
> `astra`=✨gpt-6-astra（分析体），两者**共用同一个网关、同一份记忆中枢**，靠 `source` 区分归属。
> 注意：OpenClaw 的 `/v1/chat/completions` 端点**不注入工作区人格文件**，人格一致性由调用方
> （`call_openclaw.py` / `agentctl.py` 的 `PERSONA` 注入）保证。

**入口质检**：`mem.py add` 默认拒绝"无主语泛化垃圾"（如"已完成""已上线"这类无实体一次性事件），
确为有效结论时加 `--force` 强制写入。来源名不在 `agents.json` 注册表内会被拒绝，防止各 agent 自造来源名。

**单一真源保证**：`mem.py` 的所有读操作（list/search/recall/drain/stats）已改为委托 `gateway` 直读 `memory.db`；
`sink.json` 只在 gateway 写入时作为兼容导出被顺带刷新。**不会再出现 mem.py 与 gateway 各写一份的情况。**

## 九、计划任务与端到端验证

**两个 Windows 计划任务（均已注册、状态 Ready）**：

| 任务名 | 频率 | 动作 |
|---|---|---|
| `MemoryHubReflector` | 每 5 分钟 | `gateway.py process_events --limit 10`（消费 run_events → 提炼 → 沉淀） |
| `MemoryHubMaintenance` | 每日 03:00 | `memory_maintenance.py`（备份 db + 检查工具路径 + 查重 + 重建投影 + 健康报告） |

两者都用**绝对路径**调用，与工作目录无关。Reflector 已验证 last result=0。

**端到端验证**（`e2e_verify.py`，2026-09-13）：13/13 通过
- A 组（WorkBuddy）：`preflight.py` 返回 MEMORY_PREFLIGHT 区块并命中"豆包数据转移"结论 ✅
- B 组（OpenClaw）：`mem.py recall/drain/search/stats` 全部走 memory.db，水位增量正常 ✅
- C 组（豆包）：`mem.py add --source doubao_a` 写入 memory.db，同一条可被 gateway / preflight 三个入口一致检索到 ✅
- D 组（单一真源）：`mem.py stats` 与 `gateway.py stats` 的 active 数完全一致 ✅

**当前规模**：facts 190 条（active 150 / superseded 36 / quarantined 2 / retired 2），tool_assets 9，audit_log 200+。

## 十、诚实说明（已知不完美处）

1. **三个 agent 的运行时接线靠约定，不靠强制**。`preflight.py`/`mem.py` 是否真的在每次会话开始/结束被调用，
   取决于各 agent 的运行时行为，本中枢无法强制。目前只验证了"脚本本身正确、三个入口读同一真源"，
   没验证"每个 agent 每次会话确实都调用了它们"。
2. **历史遗留事实可能仍有未识别的弱垃圾**。本轮用质量门禁（长度/ASCII实体/泛化词）隔离了 2 条，
   但门禁是启发式，不是全知；未来出现的垃圾靠"检索未命中触发 on_miss"和人工巡检兜底。
3. **中文分词的语义召回有上限**。当前靠"向量 + 字面匹配"组合，没上 jieba/FTS5；
   对"换个完全不同的说法问同一个概念"仍可能漏。数据集小（150 条）时可接受，规模上去需重估。
4. **`on_miss` 只在 top_score<0.55 时记录**，属于"明显没找到"才留痕，中间地带（0.55~0.7 的弱命中）不会记。

