# MemTether 项目介绍

> 跨客户端 AI 记忆中枢 · 一份物理数据库 · 多个 Agent 共享 · 零云端 · 零 API 费用
>
> GitHub: https://github.com/MemTether/MemTether · PyPI: `pip install memtether`
> 在线演示: https://memtether.github.io/MemTether/court/ （浏览器直接玩，无需安装）

---

## 一句话介绍

MemTether 让同一台机器上的**多个异构 AI 客户端**（Claude Code、Codex CLI、Cursor、Windsurf、Gemini CLI 等皆可）**共享同一份物理记忆**——不是各存一份再同步，而是通过操作系统级的文件指针（junction/symlink）指向**同一个 SQLite 数据库**。没有守护进程，没有云端，没有同步逻辑。

## 解决什么问题

使用多个 AI 编程助手的人每天都会遇到：

- **会话失忆**：今天告诉 Agent A 的结论，明天它忘了
- **客户端隔离**：Agent A 知道的，Agent B 永远不知道
- **云端记忆的代价**：现有方案（mem0、Hindsight 等）要上传你的代码上下文到别人的服务器，按量付费
- **自建方案的重量**：部分方案需要 Node.js + WSL2 + 8GB 内存

MemTether 的答案：**记忆放在本地一个 SQLite 文件里，谁需要谁挂载**。文件级指针意味着天然一致——客户端 A 写入，客户端 B 立即可见，因为它们读的就是同一个文件。

## 核心能力

### 1. 跨客户端共享（23 个适配器）
一条命令 `memtether init` 自动检测本机已安装的 AI 客户端，写入 MCP 配置并验证。支持通用适配器（任何支持 MCP 的客户端）+ 本机专属适配器，全部有写路径契约测试覆盖。

### 2. 双时间轴（Bi-temporal）
每条记忆有两套时间：

| 时间轴 | 记录内容 | 回答的问题 |
|---|---|---|
| **T（有效时间）** | 事实在现实中何时成立/失效 | "上周三那天，什么是真的？" |
| **T'（摄录时间）** | 系统**何时知道**这个事实 | "当时系统为什么做那个决策？" |

这两条轴的差异正是记忆治理的价值：避免用今天的信息去苛责昨天的判断。每条记忆都可回答"在任意历史时刻，系统知道什么、什么为真"。

### 3. 替代链（Supersession）
纠正一条记忆**不删除**旧条，而是创建替代关系，形成版本链。全程可追溯：谁在什么时候、因为什么、把哪条记忆替代成了什么。配合时间轴查询，可以还原任何事实的完整演化史。

### 4. 记忆法庭（Memory Court）
这是 MemTether 区别于所有竞品的核心：

- **防篡改证据链**：审计日志每 10 条生成一个 SHA-256 哈希锚点，串成哈希链。任何一条记录被修改或删除，重算哈希链立即失配
- **五段式卷宗**：每条记忆可生成内容、时间轴、溯源链、冲突裁决、完整性证明五段式卷宗
- **证据导出**：一键导出证据包（JSON），含全部审计记录
- **浏览器可验证**：导出的证据可以在任何人的浏览器里重算哈希验证，无需安装任何东西

在线演示：https://memtether.github.io/MemTether/court/ —— 在浏览器里直接写入、纠正、验链，零安装零注册。

### 5. 混合检索
四路召回 + RRF 融合排序：

- 本地 bge-m3 语义向量（可选，没装则自动降级）
- 关键词 IDF
- SQLite FTS5 全文检索
- 字面精确匹配

检索结果会叠加 Q-Value 价值分——被采纳次数多的记忆自动上浮，长期不用自动衰减。配合 30 天半衰期的时间衰减，避免"旧结论霸榜"。

### 6. 冲突检测与人工裁决
- **精确检测**：同一实体上显式状态断言相反（"X 可用" vs "X 403 失效"），低误报率，可自动化生成候选
- **启发式检测**：跨条极性冲突，作为候选生成器供人工复核
- **人工裁决**：每对冲突可记录人工结论（真冲突/非冲突），已否定的对从评分中排除

### 7. 安全与隔离
- 写入侧：内容长度/类型/来源/置信度/scope 全参数验证
- 检索侧：scope 隔离（shared/private/restricted），private 记忆不进默认检索
- 导出侧：PII 脱敏（手机号/邮箱/API Key/AWS Key/Slack Token 正则脱敏）
- 运行时：OWASP 规则守卫（prompt injection/代码执行/敏感信息检测）
- 速率限制：API 模式 60 请求/分钟

### 8. 部署形态
- **CLI**：`pip install memtether` 即用
- **MCP Server**：自动接入任何支持 MCP 的客户端
- **REST API**：FastAPI，14 个端点，Docker Compose 一键部署
- **跨平台**：Windows（junction）/ macOS、Linux（symlink）

## 诚实的数据

LongMemEval 500 题全量运行（严格子串匹配口径）：

| 指标 | 得分 |
|---|---|
| 单会话召回 | 86.4% / 40.4%（user/assistant 分母） |
| 知识更新 | 76.6% |
| 多会话聚合（strict） | 48.8% |
| 多会话 + EAF 证据锚定 | **60.0%**（提升 +11.2pp，配对 p=0.008） |
| 全局 strict | 62.6% |

完整数字与复现命令见 [benchmark 页](https://memtether.github.io/MemTether/benchmark.html) 与 [复现指南](https://memtether.github.io/MemTether/site/zh/reproduce.html)。

**我们不掩盖弱点**：多会话聚合是最难的部分，top-k 检索天然难以汇聚散落在 N 个会话里的证据。EAF（证据锚定框架）把 32.5% 提到 60.0%，但离单会话水平仍有差距——这个差距被量化并写进了文档。

## 工程质量

| 指标 | 数值 |
|---|---|
| 测试 | **466 passed, 4 skipped** |
| CI | 9 jobs（ubuntu×2 + windows×2 + macos×2 + Docker boot + lint + build）全绿 |
| 核心模块覆盖率 | memory_court 98% · predicates 95% · consolidation 67% · governance 57% · gateway 54% |
| 发布 | PyPI · GitHub Release（双附件 + SHA-256）· MCP Registry · Docker |
| 协议 | Apache-2.0 |

每个结论都有可执行的复现命令。 benchmarks 页的数字全部来自我们自己的测试工具，失败模式全部披露。

## 快速开始

```bash
pip install memtether
memtether init
```

就这两条。`init` 会检测你机器上的 AI 客户端、写入 MCP 配置、验证接入状态。

```bash
# 写一条记忆
memtether remember "Clash 代理端口是 7890" --source my-notes

# 搜索
memtether search "Clash 端口"

# 纠正（不删除，建替代链）
memtether correct <uid> "端口改为 7897" --reason "迁移了"

# 生成记忆法庭卷宗
memtether court <uid>

# 验证审计链完整性
memtether court --verify
```

## 链接

- GitHub：https://github.com/MemTether/MemTether
- PyPI：https://pypi.org/project/memtether/
- 在线演示（记忆法庭）：https://memtether.github.io/MemTether/court/
- 复现指南：https://memtether.github.io/MemTether/site/zh/reproduce.html
- 基准数据：https://memtether.github.io/MemTether/benchmark.html
- API 文档：https://github.com/MemTether/MemTether/blob/main/docs/API.md

## 许可证

Apache-2.0。欢迎贡献。
