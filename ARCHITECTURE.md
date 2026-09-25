# MemTether 架构

> **唯一权威真源 = `memory.db`（SQLite）。** 其余文件（`sink.json` / `*.md` / 各客户端的注入槽位）
> **全是投影**，由 `rebuild` 生成 —— 勿手改。
>
> 本文是总纲。怎么装、怎么跑见 [README](README.md)。

---

## 一、架构分层

```
任意 AI 客户端（本地 Agent / IDE 插件 / 桌面客户端 / 脚本 / 你自己的程序）
        │  统一走 gateway.py（唯一写入入口，带 source 归属）
        ▼
┌─────────────────────────────────────────────┐
│ gateway.py  Memory Gateway                  │
│  ├─ remember   写事实（SQLite + 可选 Mem0）  │
│  ├─ search     混合检索（向量+ASCII+字面）   │
│  ├─ correct    用户纠正 → supersede 旧事实   │
│  ├─ retire     退役机制/工具                 │
│  ├─ as_of      时序查询：某时刻什么为真 / 系统当时认为什么为真 │
│  ├─ timeline   沿替代链还原一条事实的完整演化（双轴并列）│
│  ├─ record_tool 记录工具/路径/地址资产       │
│  ├─ resolve_task 按任务召回完整执行配方      │
│  ├─ event/process_events  事件驱动自动闭环   │
│  ├─ incident/on_miss  故障与检索未命中留痕   │
│  ├─ rebuild    从 SQLite 重建所有投影        │
│  └─ stats      统计                          │
└──────┬──────────────────────┬────────────────┘
       │                      │
   memory.db (SQLite)     mem0_store/ (ChromaDB)
   权威事实账本            向量索引 facts_active
   + run_events 事件队列    （写入自动同步，无需手动 rebuild）
       │                      │
       └────── rebuild ───────┘
              │
   投影：sink.json / *.md / 各客户端的注入槽位
   （全是投影，勿手改）
```

## 一·补、技能层（记忆中枢的第 5 层）

**技能是记忆中枢的一类资产**，不是另一个项目。

- **记忆**管「我记得什么」——事实、经验、事故、决策。
- **技能**管「我怎么做」——把反复用对的做法固化成可复用的执行配方。

两者是同一条链：技能从记忆里长出来（`skill_forge` 沉淀），受预算约束不把上下文撑爆
（`skill_budget` 守卫），再分发到两个客户端共享同一份物理文件（junction）。
分开看会各自失真——所以统一收进 `skillctl.py` 一个入口。

```
  记忆层 memory.db
      │  反复出现的经验（同主题命中 >= 2 条）
      ▼
 ┌──────────────────────────────────────────────┐
 │ 沉淀侧  skill_forge.py   scan/draft/admit     │
 │   批量归纳 → 草稿 → 验证门控 → 人在环路准入    │
 └───────────────────┬──────────────────────────┘
                     ▼
 ┌──────────────────────────────────────────────┐
 │ 预算侧  skill_budget.py  health/gate/preflight│
 │   description <= 1024 硬门槛；量注入成本       │
 └───────────────────┬──────────────────────────┘
                     ▼
        中立真源  ~/.agents/skills
                     │  junction（文件级指针）
        ┌────────────┴────────────┐
 ~/.workbuddy/skills    ~/.workbuddy-ai/skills
   （国内版）              （国际版）
```

三条实测事实（2026-09-18）：

1. **装一处，两版生效。** `skillctl.py link` 实测两个客户端目录都 realpath 到 `~/.agents/skills`。
   共享靠文件级指针（junction），不是同步——所以不存在"两边漂移"。
2. **治理杠杆在「每个 description 多少字」，不在「装几个」。**
   09-25 复测 103 个技能，注入成本 **37,266 字符**（name+description，平均 361/个）；
   比 09-18 首测的 79 个 / 26,013 涨了 43%。真正的风险是检索质量被稀释，不是字符数本身。
3. **解析必须兼容 YAML 块标量。** 大量 `SKILL.md` 用 `description: >` 折叠成多行，
   单行正则只会读到那个 `>`。实测 **22,869 → 26,013，失真 3,144 字符（13.7%）**，
   并会把 10 个正常技能误判成「空壳」（`conducting-mobile-app-penetration-test`
   的描述从 1 字符变回 570 字符）。现在 frontmatter 解析只有 `skill_budget.parse_desc()`
   一个实现，`skillctl.py` 直接复用——**避免两套正则给出两套结论**。
4. **重名族 ≠ 冗余，要人工看 description 才能定。** 09-18 首测 79 个技能时 `skillctl.py families`
   报出 4 个重名族（前两 token 相同）。2026-09-18 逐条复核的结论是
   **1 个真重复、3 个合理拆分**——这正是不做自动删除的理由：

   | 族 | 数量 | 复核结论 |
   |---|---|---|
   | `ai-security` | 5 | **合理拆分**：`dispatch` 是路由器，`prompt`/`rag`/`infra-supplychain`/`redteam` 是四个不重叠的专项切面 |
   | `conducting-mobile` | 2 | **★真重复**：两个都按 OWASP MASTG 做 iOS/Android 渗透，职责重叠 |
   | `csm-llm` | 2 | **合理拆分**：一个是模型红队，一个是 Agent 权限与访问控制（不同模块） |
   | `js-reverse` | 2 | **边缘**：一个是逆向全链路，一个是浏览器自动化出 JSRPC 代码（工具配套，暂留） |

   处置口径：真重复的那个**不做自动删除**——技能刚装好、用户还没用过，
   谁是"该留下的那一个"应当在使用中见分晓。工具只负责把候选和描述摆出来，
   删除永远是人按 `skillctl.py dup` 的建议手动做（可逆：移入 `_quarantine`）。

常用命令：

```bash
python skillctl.py audit       # 一眼看全：分发 / 体积 / 重叠 / 空壳
python skillctl.py link        # 两版是否都指向中立真源（跨客户端共享的前提）
python skillctl.py dup         # 空壳 / 重名族
python skillctl.py health      # 注入预算体检
python skillctl.py scan        # 从记忆里扫可沉淀的主题（等价于 skill_forge scan）
```

## 二、组件与选型（实测可用）

| 组件 | 选型 | 说明 |
|---|---|---|
| 权威库 | SQLite `memory.db` | facts / tool_assets / recipes / supersessions / audit_log / candidates / run_events |
| 事实提取 LLM | **可配置**（任何 OpenAI 兼容通道） | 默认用便宜的快模型；冲突 / 高风险 / 灰区才升级到强模型 |
| 自动记忆引擎 | Mem0 2.0.20（可选） | 写入时的冲突消解（ADD/UPDATE/DELETE）+ 自动提取 |
| 检索模块 | `memsearch.py` | 质量门禁 + 向量 + ASCII + 字面 四路融合（见第六节） |
| embedding | **本地 bge-m3 int8**（默认） | 1024 维、543MB、短进程零常驻；可选云端 embedding-3（2048 维）。**切后端须重建索引**，维度不符会硬报错 |
| 精排 | bge-reranker-base int8 | 266MB；单次查询约 98% 耗时在这里，`MEM_RERANK_K` 可调 |
| 向量库 | ChromaDB 1.5.9（可选） | 本地目录 `mem0_store/`，collection=`facts_active`，无 Docker |

> **注意**：语义检索的实际入口是 `memsearch.py`（直接查 ChromaDB 的 `facts_active`），
> 不是 Mem0 的 `search`（Mem0 的 search 受其内部 add 流水线影响，且会把候选/垃圾一起召回）。
> Mem0 在本架构里只负责"写入时的自动提取与冲突消解"。

> **缺依赖不等于崩**：`chromadb` / `onnxruntime` / `tokenizers` 都在 `[vector]` 可选档。
> 缺任何一个，检索自动降级为关键词 + 字面，**打印准确的补救命令**，不中断。

## 三、硬规则（任何客户端必须遵守）

1. **禁止直接改 `sink.json` / `memory.db` / `mem0_store` / `*.md`**，一律走 `gateway.py`。
2. 每条事实必须有 `source` + `status` + `confidence`。
3. 新事实覆盖旧事实 = supersede（旧事实标记 `superseded`，**不物理删除**）。
4. 工具 / 路径 / 地址进 `tool_assets` 表，不做纯散文。
5. 任务检索优先 `resolve_task` 返回配方，不是散乱记忆。
6. 候选（candidate）不能伪装成 active 事实。
7. `*.md` 是投影，改记忆请用 `gateway`，别手改 md。
8. **时间必须成对记录**：T 轴（`valid_from` / `valid_to`，现实世界何时成立）与
   T′ 轴（`recorded_at` / `invalidated_at`，系统何时记录、何时认定失效）都要维护，
   `temporal_source` 标注数据来历（`native` / `backfilled` / `inferred`）。
   **只记一根轴 = 历史查询会静默给出错误结论**（断言见 `smoke_bitemporal.py`）。
9. **派生文件不许进版本库**。「父进程写、子进程读」的传参文件是 IPC 不是产物；
   一旦提交，任何"换掉题集 / 换掉配置"的运行时行为都会把它覆盖成污染源。
   判据：**删掉它会不会丢信息？** 不会 → 它是派生物，传参改走系统临时目录。

## 四、常用命令

```bash
# 装成包之后（推荐）
memtether demo                     # 生成全合成演示库
memtether search "关键词"           # 混合检索
memtether remember "结论：……" --type experience --source my_agent

# 直接用源码（运维脚本 / 评测集走这条）
python gateway.py remember "内容" --type fact --source my_agent
python gateway.py resolve_task "生图"      # 按任务召回工具配方（解决"反复扫描"）
python gateway.py search "关键词" --mem0   # 加 --mem0 启用语义
python gateway.py correct <old_uid> "新内容" --reason "..."
python gateway.py retire <uid> --reason "..."
python gateway.py record_tool "工具名" --path "..." --entrypoint "..."
python gateway.py rebuild                  # 从 SQLite 重建所有投影
python gateway.py stats

# === 记忆自动闭环（事件驱动）===
# 1) 干完活写运行摘要 run.json，然后记事件
python gateway.py event --type run_finished --run-id <id> --payload-file run.json
# 2) 反射器异步提炼，可手动触发或交给定时任务
python gateway.py process_events --limit 10
```

## 五、记忆自动闭环（事件驱动）

> 解决"记得住但不会自动记"的根本缺口：写入从"手动 `remember`"升级为"事件驱动自动沉淀"。

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
auto_reflect（提炼候选记忆，严格 JSON）
   │
   ▼
commit_memory_candidate（价值评分 + 阈值过滤 + 去重）
   ├─ value≥0.75 且 conf≥0.8 → 自动写入（走 remember）
   ├─ 0.5≤value<0.75 → 进 candidates 待审
   └─ value<0.5 → 丢弃
```

**价值公式**：`value = 0.4*reuse + 0.3*impact + 0.2*evidence + 0.1*stability`

**提炼模型分流**：默认用便宜的快模型（省 token），只有冲突 / 高风险 / 灰区才升级到强模型。

**幂等**：`run_events` 有 `uq_run_event(run_id,event_type)` 唯一索引，重复事件只记一次。

## 六、混合检索

> 修复前 `search()` 用 `content LIKE '%q%'` 精确子串匹配，中文查询 4/8 失败；修复后 8/8 通过。
> 模块：`memsearch.py`。

**四路融合**：

1. **质量门禁**：隔离"无主语泛化垃圾"（长度<20 字 且 无 ASCII 实体 且 泛化词≥2）。
   实测只删掉 2 条泛化垃圾，8 个查询的 top-1 **全部从错误变正确**。
2. **向量路**：本地 embedding（默认 bge-m3 int8）+ ChromaDB `facts_active` collection
   （仅 active，排除垃圾）。解决"问法与表述不一致"。
3. **ASCII 实体精确路**：query 含 ASCII 实体（`robocopy` / `STM32` / `APK` / 路径）时 +0.3 加权。
4. **字面匹配路**：query 整句子面出现在事实里时 +0.5 加权。解决纯中文概念
   （向量区分度不足）的问题，**无需维护手工概念表**。

**最终分数** = semantic + ascii(0.3) + literal(0.5)，降序返回。

**关键决策**：

- 跳过 FTS5 + jieba（实测 FTS5 默认分词对中文失效，需 jieba 才有用，对万条以下的小数据集性价比低）。
- 不需要"写入时调 LLM 打标签"（成本高且非必要）。
- 中文概念不靠手工映射表，靠"字面匹配 + 向量"组合。

## 七、环境依赖

- **Python 3.10+**。`pip install memtether` 只装 `numpy`（精排用，缺它时退回 RRF，不崩）。
- 语义检索所需的 `chromadb` / `onnxruntime` / `tokenizers` 在 `[vector]` 档，
  只想跑 demo 的人不必先下几百 MB。
- 模型权重（embedding / rerank）不在包内，首次按提示离线获取。
- **凭据只从环境变量读，绝不落盘**（同一约定见 `publish_pypi.py`）。

## 八、接入契约（唯一入口）

任何客户端接入只需满足三条：

| 事项 | 读（会话开始） | 写（干完活） | 来源名 |
|---|---|---|---|
| 约定 | 调 `search` / `resolve_task` 取配方 | 调 `remember --source <你注册的名字>` | 必须在 `agents.json` 注册 |

**别手工做这件事 —— 用接入器。** 上面三条的落地方式（MCP 配置文件在哪、schema 长什么样、
要不要点信任、来源名怎么注册）每个客户端都不一样，手工做必漏。一条命令搞定：

```bash
memtether-connect detect     # 发现本机装了哪些客户端、各自配置在哪、接没接
memtether-connect plan       # 预演：只打印将要改什么，不写盘
memtether-connect apply      # 写入（先备份 + 生成 manifest，可回滚）
memtether-connect verify     # 校验：配置内容 + 信任状态
memtether-connect rollback --stamp <时间戳>
```

它做四件事：① 按客户端 schema 写 MCP 配置（JSONC 感知，保住注释与缩进风格）；
② 代写 Electron 系的信任记录（`sha256(command|sorted(args)|sorted(env keys))`）；
③ 把来源名补进 `agents.json`；④ 回读校验。写盘原则是**不猜**：
找不到约定的根路径就失败关闭，绝不「大概写在这儿」。

### `source` 的解析顺序

**`source` 是归属的唯一依据**：同一条记忆由谁写入、哪个客户端在何时认定它失效，全靠它。

| 优先级 | 取值来源 | 说明 |
|---|---|---|
| 1 | 显式传参 | 多客户端场景下**每条写入都应显式传** —— 否则你无法回答"这条结论是谁记的" |
| 2 | `MEM_DEFAULT_SOURCE` 环境变量 | 本客户端固定用某个名字时设它 |
| 3 | **父进程识别**（零配置） | 两级：父进程**映像全路径** → 父进程**命令行**（读 PEB）。覆盖 Electron 系与「被通用 `node.exe` 拉起」的 dsh / Claude Code |
| 4 | 兜底值 | 默认中性 `local`（`MEM_FALLBACK_SOURCE` 可覆盖） |

> **为什么不把来源写进 MCP 配置的 `env`**：`env` 的 key 集合参与信任 hash，
> 多一个 key 就掉信任，而掉信任后 server 会被客户端**静默跳过**
> （只有日志里一行 `skipping untrusted`）。所以第 3 级（自动识别）比第 2 级更该优先用。
>
> 想加自己的客户端：在同目录放 `client_signatures.json`，
> `{"signatures": [["myclient", "mysource"]]}`，不必改代码。

### 来源校验（fail-open）

写入入口（`remember` / `correct` / `record_tool`）会校验来源名是否已注册，
防止脚本自造名把归属写花。但校验**故意是 fail-open**：

- 读不到 `agents.json` ⇒ **放行**（不能让"配置缺失"变成"写不进记忆"）；
- 中性默认值 `local` / `unknown` ⇒ **永远放行**（它们是"未识别来源"的诚实标记）；
- 未注册 ⇒ 有 `MEM_SOURCE_FALLBACK` 则**软着陆回退** + warning，否则硬报错；
  `MEM_SOURCE_GUARD=0` 可应急放行。

**★为什么不直接 `raise SystemExit`**：它**不被** `except Exception` 捕获，
长驻进程（MCP server）会**直接死掉** —— 客户端侧表现为工具静默消失且不会自动拉起。
比报错更糟。

**入口质检**：`mem.py add` 默认拒绝"无主语泛化垃圾"（如"已完成""已上线"这类无实体一次性事件），
确为有效结论时加 `--force` 强制写入。

**单一真源保证**：`mem.py` 的所有读操作（`list`/`search`/`recall`/`drain`/`stats`）已改为
委托 `gateway` 直读 `memory.db`；`sink.json` 只在 gateway 写入时作为兼容导出被顺带刷新。
**不会再出现 `mem.py` 与 `gateway` 各写一份的情况。**

## 九、定时任务与端到端验证

**两个可选定时任务**（Windows 计划任务，需自行注册；都用**绝对路径**调用，与工作目录无关）：

| 任务名 | 频率 | 动作 |
|---|---|---|
| `MemTetherReflector` | 每 5 分钟 | `gateway.py process_events --limit 10`（消费 run_events → 提炼 → 沉淀） |
| `MemTetherMaintenance` | 每日 03:00 | `memory_maintenance.py`（备份 db + 检查工具路径 + 查重 + 重建投影 + 健康报告） |

**端到端验证**（`e2e_verify.py`，13/13 通过）：

- **A 组（注入侧）**：`preflight.py` 返回预检区块，并命中预期结论 ✅
- **B 组（CLI 侧）**：`mem.py` 的 `recall` / `drain` / `search` / `stats` 全部走 `memory.db`，水位增量正常 ✅
- **C 组（多来源）**：用不同 `--source` 写入的条目，可被所有入口一致检索到 ✅
- **D 组（单一真源）**：`mem.py stats` 与 `gateway.py stats` 的 active 数完全一致 ✅

## 十、诚实说明（已知不完美处）

1. **接入靠约定，不靠强制**。`preflight.py` / `mem.py` 是否真的在每次会话开始 / 结束被调用，
   取决于各客户端自身的运行时行为，本中枢**无法强制**。目前只验证了"脚本本身正确、
   各入口读同一真源"，没验证"每个客户端每次会话确实都调用了它们"。
   `memtether-connect` 解决的是**配置侧**（把 MCP 配置写对、信任写对、来源名注册对），
   它**不改变**这一条：客户端拿到工具之后用不用，仍取决于客户端的运行时行为。
2. **接入器的实测面只覆盖 Windows + 本机装过的客户端**。23 个适配器里，
   `clients/local.py` 那批（Electron 系 / dsh 系）是**本机实测**的；
   `clients/standard.py` 那批路径与 schema 是**对照社区维护的 agent config 参考表**核对的，
   没有逐个真机验证。macOS / Linux 的路径已按惯例写入但**未经实测**。
   `detect` 对没装的客户端会跳过，所以"没被写"≠"不支持"。
3. **来源自动识别有两处已知盲区**：
   - 读 PEB 取命令行需要 `PROCESS_VM_READ` 权限，权限不足时静默返回 `None`
     （退到下一级，不会误报）；
   - 非 Windows 平台直接跳过识别（`os.name != 'nt'` 时返回 `None`），
     此时请用 `MEM_DEFAULT_SOURCE` 显式指定。
4. **历史遗留事实可能仍有未识别的弱垃圾**。质量门禁（长度 / ASCII 实体 / 泛化词）是启发式，
   不是全知；未来出现的垃圾靠"检索未命中触发 `on_miss`"和人工巡检兜底。
5. **中文分词的语义召回有上限**。当前靠"向量 + 字面匹配"组合，没上 jieba/FTS5；
   对"换个完全不同的说法问同一个概念"仍可能漏。数据集小时可接受，规模上去需重估。
6. **`on_miss` 只在 `top_score<0.55` 时记录**，属于"明显没找到"才留痕，
   中间地带（0.55~0.7 的弱命中）不会记。
7. **注入槽位瓶颈**：活跃条目数百条，但受客户端注入上限约束，每轮实际只能喂进几十条
   → 存得多、喂得少。这是当前最大瓶颈（见 README 路线图）。
