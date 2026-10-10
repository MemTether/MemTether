<div align="center">

<img src="assets/demo.gif" width="100%" alt="MemTether — 跨客户端 AI 记忆演示"/>

# MemTether — AI 记忆法庭

**你的 agent 记住了什么，谁来审计？**

MemTether 是一个 local-first 的多客户端 AI agent 记忆中枢。大多数记忆系统比拼检索准确率，MemTether 比拼**治理**：谁写的这条记忆、何时生效、替代了什么、人裁了哪些冲突，以及任何人零代码即可验证的**防篡改哈希链**。检索也有（SGM 确定性 SQL 路由 + 混合 embedding），但那不是头牌——可审计、可纠正、可信任的 agent 记忆才是。

来自 mem0 / Zep / Letta？[`examples/mem0_migration.py`](examples/mem0_migration.py)
演示从 mem0 JSON 导出的完整往返迁移——不需要 mem0 SDK。

```bash
$ memtether court fact-20261007-abc
+============================================================+
| MEMTETHER MEMORY COURT - 案件档案                            |
| 1. 内容       Clash 代理端口是 7897                        |
| 2. 时间轴     有效: 10-05 → 至今 | 已知: 10-05 → 现在        |
| 3. 来源链     #1 remember by=codex → #2 correct by=user      |
| 4. 冲突       case #3: vs fact-...old verdict=superseded     |
| 5. 完整性     chain: PASS (3 anchors, 42 entries hashed)    |
+============================================================+
```

## 30 秒看懂 MemTether

- **你的 AI 客户端互相不认识？** 每个 agent 各自维护记忆，互不知道对方知道什么。MemTether 用文件级指针让它们共享同一个 governed 大脑——零云端、零同步进程。
- **你的 AI 写了不该写的东西？** 每条记忆都带来源身份和哈希链审计。谁直接改库，重算链路立刻变红——运行 `python examples/audit_chain_demo.py` 亲眼看到。
- **你的 AI 改口了？** 纠正永不删除。双时间轴 supersession 链记录谁改的、什么时候改的、为什么改，任何历史时点可查。

自己跑一遍：

```text
$ python examples/audit_chain_demo.py
写入 25 条记忆（自动生成 2 个审计锚点）...
篡改前 verify_chain: PASS {'anchors': 2, 'entries_covered': 20}
模拟攻击者直接改库...
篡改后 verify_chain: FAIL {'reason': 'chain hash mismatch'}
✅ 哈希链成功检测到篡改！

> 注：entries_covered: 20 而写入 25 条是正常的——哈希链每 10 条审计记录生成一个锚点，
> 最后 5 条处于未锚定尾部直到下一批。篡改"已锚定"的条目才触发红色告警。
```

## 从 mem0 迁移

已在用 mem0？[`examples/mem0_migration.py`](examples/mem0_migration.py)
演示完整往返：mem0 JSON 导出 → MemTether Exchange v1 → 在线检索 → 回写 mem0 格式。
无需 mem0 SDK，双时间轴字段诚实标注 `temporal_source="backfilled"`。

## 性能（微基准，n=200，本地 SQLite）

| 操作 | p50 | p95 | p99 |
|---|---|---|---|
| remember (写入) | 28.7ms | 34.6ms | 117.8ms |
| search (SGM 实体) | 33.1ms | 39.5ms | 41.6ms |
| search (关键词) | 32.2ms | 35.0ms | 135.0ms |
| correct (替代) | 50.8ms | 54.7ms | 54.7ms |

复现：`python _dev/benchmarks/bench_latency.py --n 200`

## 文档

核心机制：**supersession 链**（纠正不删除）、**双时间轴**（事实何时成立 vs 系统何时知道）、
**人工裁决的冲突处理**、**哈希链审计日志**（参照 EU AI Act Article 12 记录保存要求设计，非认证合规）。

一份物理 SQLite 数据库，通过文件级指针被所有 AI 客户端共享。
零云端。零 API 费用。零同步进程。可 grep、可 git、永远属于你——并且**可审计**。

> *对比 mem0 (66.6K★)、Zep/Graphiti (31.5K★)、agentmemory (29.2K★)：
> 那些系统要么覆盖要么删除旧值。MemTether 的 supersession 链保留完整历史，带来源归属和 Q-Value 排名。*
> EAF 检索：LongMemEval 500Q strict 75.7%（配对 delta +19.4pp，McNemar p=7.8e-7）。

### 与同类项目的对比

| | MemTether | [memora](https://github.com/agentic-box/memora) (731★) | [memtrace](https://github.com/syncable-dev/memtrace-public) (486★) | [icarus](https://github.com/esaradev/icarus-memory-infra) (290★) | [mem0](https://github.com/mem0ai/mem0) (66K★) |
|---|---|---|---|---|---|
| **Supersession 链**（纠正不删除） | ✅ 强制 | ❌ | ❌ | ✅ | ❌ 覆盖 |
| **证据链**（防篡改，浏览器可验证） | ✅ court CLI + zip | ❌ | ❌ | ❌ | ❌ |
| **人工冲突裁决**（有记录） | ✅ conflicts CLI | ❌ | ❌ | ❌ | ❌ |
| **双时间轴**（valid_from ≠ recorded_at） | ✅ 100% 覆盖 | ❌ | ✅ 仅图边 | ❌ | ❌ |
| **来源归属**（谁写的，强制） | ✅ registry + reject | ❌ | ❌ | ✅ | ❌ |
| **冲突检测**（规则 + 人工复核） | ✅ 候选生成器 | ❌ | ❌ | ❌ | ❌ |
| **MCP server** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **23+ 客户端适配器** | ✅ 自动检测 | ❌ | ❌ | ❌ | ❌ |
| **评测诚信框架** | ✅ 5 patterns | ❌ | ❌ | ❌ | ❌ |
| **CI: 9 jobs / 523 tests** | ✅ | ? | ? | ? | ✅ |

*各单元格反映截至 2026-10-07 该项目公开文档中的功能现状。MemTether 的差异化不是任何单项功能——是所有治理维度在同一个 local-first 部署中的组合。*

[![PyPI](https://img.shields.io/pypi/v/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

mcp-name: io.github.lanbass869-cell/memtether
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.lanbass869--cell%2Fmemtether-blue)](https://registry.modelcontextprotocol.io)

[**在线试用**](https://huggingface.co/spaces/lanbass/memtether-demo) · [安装](#快速开始) · [文档站](https://memtether.github.io/MemTether/site/en/) · [复现指南](https://memtether.github.io/MemTether/site/en/reproduce.html) · [English](README.md)

</div>

---

## 快速开始

```bash
pip install memtether
memtether init    # 创建数据库 + 自动连接所有检测到的 AI 客户端
```

就这样。MemTether 自动检测你机器上的 Claude Code、Cursor、Windsurf、Codex、Gemini CLI 等 23 个客户端，写入 MCP 配置并验证。

<details>
<summary>更多安装选项</summary>

```bash
# 逐个接入
memtether setup claude-code
memtether setup cursor
memtether setup gemini-cli    # memtether setup list 查看全部 23 个

# 本地语义检索（不联网、不付费）
pip install "memtether[vector]"          # 1. 依赖（chromadb、onnxruntime）
memtether download-models                # 2. 权重（bge-m3-int8 约560MB；--profile bge-small-zh 约46MB）
python -m memsearch --rebuild            # 3. 建向量索引
# 不装模型时检索自动降级为关键词 + BM25，功能不受影响

# REST API 服务器 + Web 控制台
pip install "memtether[server]"
memtether dashboard

# Docker
docker-compose up

> **安全提示**：REST API 默认无认证，除非设置 MEMTETHER_API_KEY。
> 只在可信网络暴露 8080 端口——共享机器上先在 .env 设好 key 再 docker-compose up。
> **memtether init 注意事项**：自动检测覆盖标准安装路径的客户户端。
> 自定义位置的客户端可能遗漏或误报——用 memtether connect verify 确认，
> 参考 docs/ADAPTERS.md 手动配置 MCP。

```

</details>

---

## 为什么选 MemTether

### 其他记忆系统没有的：纠正不删除

```bash
# 周一：Claude Code 写入
$ memtether remember "部署到 8080 端口" --source claude-code

# 周三：通过 Cursor 纠正
$ memtether correct fact-xxx "部署到 9090 端口" --reason "端口变了"

# 周五：另一个 agent 搜索
$ memtether search "部署 端口" --list
  1. [fact] 部署到 9090 端口 (active)     ← 当前真相
     旧的 "8080" 已被替代，没有删除。
     完整审计链：memtether timeline fact-xxx
```

每条记忆都有 `valid_from` / `valid_to`（业务时间）+ `recorded_at` / `invalidated_at`（系统时间）。
可以查"系统周二知道什么？"

### 怎么不一样

| | MemTether | engram | mem0 | agent-memory |
|---|---|---|---|---|
| 存储 | SQLite 文件指针 | SQLite (Go 二进制) | Cloud API | Markdown 文件 |
| Supersession | ✅ 双时间轴，永不删除 | ❌ | ⚠️ 部分 | ✅ |
| 来源归属 | ✅ 每条标注 | ❌ | ❌ | ✅ |
| Q-Value 反馈 | ✅ 用过的排前面 | ❌ | ❌ | ❌ |
| 投毒防御 | ✅ OWASP LLM01/02/06 | ❌ | ⚠️ 忠实度检查 | ❌ |
| 跨客户端 | ✅ 文件指针（23 适配器） | ✅ MCP | ✅ API | ✅ CLI |
| 需要云 | ❌ | 可选 | ✅ | ❌ |

<details>
<summary>与 cognee / zep / letta 的功能对比</summary>

| 功能 | MemTether | cognee | zep | letta | Engram* |
|---|---|---|---|---|---|
| Local-first | ✅ | ❌ | ❌ | ⚠️ self-hosted | ✅ |
| 零配置 | ✅ | ❌ (Neo4j) | ❌ (Docker) | ⚠️ | ✅ |
| 单文件 DB | ✅ | ❌ | ❌ | ❌ | ✅ |
| REST API | ✅ (12 端点) | ✅ | ✅ | ✅ | ❌ |
| MCP server | ✅ | ❌ | ✅ | ✅ | ❌ |
| Web 控制台 | ✅ | ❌ | ✅ | ✅ | ❌ |
| Supersession 链 | ✅ | ❌ | ❌ | ⚠️ | ❌ |
| 双时间轴 | ✅ | ❌ | ❌ | ❌ | ✅ |
| 来源归属 | ✅ | ❌ | ⚠️ | ❌ | ❌ |
| Q-Value 排名 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 投毒防御 | ✅ | ❌ | ❌ | ❌ | ❌ |
| Markdown 导出 | ✅ | ❌ | ❌ | ❌ | ✅ |
| 23 客户端适配器 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 治理层 | ✅ | ❌ | ⚠️ | ❌ | ❌ |

</details>

---

## 架构

```
Claude Code ────┐                  ┌──── Cursor
                │   symlink /      │
Codex ──────────┤   junction       ├──── Windsurf
                │                  │
Gemini CLI ─────┤                  ├─── WorkBuddy
                └───────┬──────────┘
                        │
                        ▼
              ┌──────────────────┐
              │   memory.db      │  ← 一份物理 SQLite
              │   (WAL 模式)     │
              ├──────────────────┤
              │ FTS5 trigram     │  ← BM25 全文
              │ ChromaDB + bge-m3│  ← 语义（可选）
              │ RRF 融合         │  ← 多路合并
              │ 4 因子重排       │  ← Z-score + sigmoid
              │ Supersession     │  ← 永不删除
              │ 双时间轴         │  ← T 轴 + T' 轴
              │ Q-Value          │  ← 基于使用频率的排名
              │ Hubguard         │  ← 并发写保护
              │ 投毒防御         │  ← OWASP LLM 防御
              └──────────────────┘
```

<details>
<summary>技术细节</summary>

| 组件 | 技术 | 用途 |
|---|---|---|
| 数据库 | SQLite (WAL 模式) | 单文件、零配置、跨平台 |
| 全文检索 | FTS5 trigram + triggers | O(1) BM25 搜索，SQL 级同步 |
| 向量 | ChromaDB + bge-m3 (1024 维) | 语义搜索，本地 embedding |
| 融合 | Reciprocal Rank Fusion (K=60) | 多路结果合并 |
| 重排 | 4 因子 (sem .45 + rec .25 + freq .05 + imp .10) | Z-score + sigmoid |
| 治理 | Supersession + 双时间轴 + 冲突检测 | 永不删除 |
| 并发 | Hubguard（文件锁 + 原子写） | 跨进程安全 |
| 安全 | memtether_guard (OWASP LLM01/02/06) | 投毒防御 |
| API | FastAPI REST (12 端点) + MCP server (4 工具) | 任意语言 |
| 打包 | PyPI + Docker + 23 客户端适配器 | pip install memtether |

</details>

---

## 连接你的客户端

```bash
# 全部自动检测（推荐）
memtether init

# 逐个接入
memtether setup claude-code
memtether setup cursor
memtether setup windsurf
memtether setup codex
```

**支持（23 个）：** Claude Code, Claude Desktop, Cursor, Windsurf, VS Code, Zed, JetBrains, Cline, Roo Code, Kilo Code, Continue, Cody, Amazon Q, Gemini CLI, Neovim, Codex CLI, WorkBuddy (国内 + 国际), CodeBuddy, ZCode, DSH, OpenClaw, Agents-Neutral

---

## 记忆操作

```bash
memtether remember "用户偏好深色主题" --source claude-code
memtether search "主题" --list        # L0: 一行 + uid
memtether search "主题"               # 完整内容
memtether correct <uid> "更新了"      # 替代，永不删除
memtether timeline <uid>              # 审计链
memtether stats
memtether export-md --out ./backup    # 导出为 Markdown
memtether dashboard                   # Web 界面
```

---

## 评测与诚实边界

我们不掩盖弱点。以下数字全部可复现（复现指南见文档站）：

<details>
<summary>LongMemEval（500 题，完整运行，2026-10-02）</summary>

| 系统 | 指标 | 结果 |
|---|---|---|
| MemTether + EAF | strict | 75.7%（多会话子集 +19.4pp，McNemar p=7.8e-7） |
| MemTether（无 EAF） | strict | 62.6% |
| mem0 v2.0.20（self-hosted） | strict / judge | 76.2% / 84.0%（309 题 haystack>20K 跳过，子集偏易） |
| LangMem v0.0.30 | strict / judge | 21.8% / 6.6%，NOT_FOUND 79.2% |

</details>

| 评测 | 口径 | 结果 |
|---|---|---|
| LongMemEval-S 500Q（MemTether+EAF） | strict | 60.0%（多会话子集 +11.2pp，配对 p=0.008） |
| MemDaily（MemSim 官方数据集，纯检索无 LLM judge，k=15） | 答案子串命中 | simple 46.7% · noisy 0%（干扰导致检索偏移——已知多会话弱点） |
| 合成多会话 SGM 对照（20 实体×25 会话） | 召回数 | SGM 84% vs embedding 64%；加 300+ 干扰后仍 84% vs 64% |

### 已知局限（附可执行证明）

- **初始事实注入无拦截**：local-first 不做写入前真实性判断——任何注册来源写入的新事实立即可被检索。防御是事后可审计（来源归属 + 哈希链 + supersession 法证链），见 `tests/test_poisoning_defense.py::test_F`（负例测试）
- **写入时值翻转闸门**：对"同一实体同一属性出现不同值"型冲突，提供确定性拦截（p50=0.037ms）。仅覆盖值翻转，不覆盖语义矛盾/时序过时/实体消解错误。中文解析器，英文需另行适配
- **记忆投毒威胁模型**：完整攻防映射见 [SECURITY.md](SECURITY.md)
- **审计锚点与数据同库**：防绕过应用层的静默篡改；对拥有全库写权限的攻击者无效（需外部锚点，roadmap）
- 多会话聚合仍是全行业难点——SGM 的 SQL 确定性路由是我们当前最有效的逃逸路径，但只覆盖「实体→属性」型查询，关系路径型问题尚未解决

---

## 连接

- [GitHub Issues](https://github.com/MemTether/MemTether/issues) — 报 bug / 请求功能
- [GitHub Discussions](https://github.com/MemTether/MemTether/discussions) — 提问 / 分享用例
- [CONTRIBUTORS.md](CONTRIBUTORS.md) — 如何贡献

## 许可证

[Apache-2.0](LICENSE)
