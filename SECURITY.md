# 安全政策

## 支持版本

当前为研究原型（最新版本见 [CHANGELOG](CHANGELOG.md)，当前线为 `v0.1.0a7`），
仅主分支接受安全报告。

## 怎么报

请**不要**开公开 issue。优先用 GitHub 的 **Private vulnerability reporting**
（仓库 Security 标签页 -> Report a vulnerability）。

★为什么不用邮箱：本仓库未公开维护者邮箱，写「发邮件到联系邮箱」却不给地址，
等于给了一条走不通的路。需要邮箱时请在仓库 Security 页开私有报告并注明希望邮件沟通。

请在报告里包含：
- 影响的版本 / commit
- 复现步骤
- 影响（信息泄露 / 任意文件读写 / 提权 / 其他）

## 本项目特有的注意点

MemTether 会在本机保存**长期记忆**，其中可能包含：

- 本机软件路径与账号目录
- 使用过的模型通道与来源标识
- 用户的工作习惯与项目上下文

因此：

1. **不要把 `memory.db` / 投影文件提交进任何仓库** —— 本仓库的 `.gitignore` 已排除，请保持。
2. 对外分享代码时，**导出的是引擎，不是你的记忆数据**。
3. 多 agent 共用同一份文件时，**务必带 `--source`**，否则无法追溯某条记忆是谁写的。

## 响应预期

- 72 小时内确认收到
- 确认后尽快给出修复或缓解方案（原型阶段以说明风险为主）

## CORS

CORS `allow_origins=["*"]` by default (localhost dashboards). When `MEMTETHER_API_KEY` is set, allowed origins are restricted to none (same-origin only) - cross-origin authenticated requests are rejected by the browser before the API key is evaluated.

## 记忆投毒威胁模型（memory poisoning）

MemTether 的持久记忆是一个真实的攻击面：能影响普通外部内容（文档、网页、
用户消息）的攻击者，可以诱导 agent 把伪造事实写入记忆库，并在**之后的会话**
里被当作既有事实检索命中（sleeper memory poisoning 模式，参见
arXiv:2605.15338 与 PoisonRecall 的攻击分类学）。

### 攻击类 → MemTether 防御映射

| 攻击类 | MemTether 缓解措施 | 机制 |
|---|---|---|
| 覆盖式投毒（改写既有事实） | supersession 链（**法证追溯，非拦截**） | `correct()` 不校验 corrector 身份——任何已注册来源可 correct 任何 fact；防御是事后可审计（旧值保留 + supersessions 边记录 who/when/why），不是事前拦截 |
| 抹除攻击（删记忆灭迹） | retire 不删除原始行 | `retire()` 只改 `status`——SQLite 行保留（法证可查），但内容从检索层移除（向量+FTS+active 过滤）。对"agent 行为"而言抹除仍会生效；本防御保护的是**历史可审计性**，不是运行时可用性 |
| 归属伪造 | source 注册表强制（⚠️ **fail-open**） | 开源版默认**无 agents.json → 未注册来源 verbatim 写入**（best-effort bookkeeping，非安全控制）；部署注册表后未注册来源被硬拒 |
| 事后灭证 | 哈希链锚点 | `audit()` 每批写入自动追加 SHA-256 锚点；**绕过应用层的写入**会被 `verify_chain()` 检出（能同时改写 anchors 表的完全控制攻击者除外，见诚实边界） |
| 休眠检索（sleeper，已注入后被替代） | 检索层状态过滤 | 默认检索只取 `active`；被替代条目不作为当前事实返回 |
| **初始事实注入**（新写入即为假） | ⚠️ **无主动拦截** | local-first 已知局限：一旦写入即 active 并可被检索。现有机制只提供事后追溯（audit_log 记录每次 remember 的来源与内容），不做写入前真实性判断。多来源互信属运营层问题 |
| 跨租户投毒 | tenant 隔离 | 写入默认 `MEM_TENANT_ID`，检索层 `_tenant_filter()` 隔离；跨租户写入需显式设环境变量 |
| 未经复核的“当前值” | TTL + 时间衰减（检索层实现，见 memsearch._parse_ttl） | 状态类结论带 `ttl:YYYY-MM-DD` 后过期自动降权并标注“可能不是当前状态”；实现于检索链，`tests/test_poisoning_defense.py::test_T` 提供证据 |

### 诚实边界

- 哈希链锚点存在与 SQL 同库的表内：能改 `audit_log` 的攻击者理论上也能改
  `audit_anchors`。它防的是**绕过应用层的静默篡改**与误操作，不是能全库重写
  的完全控制攻击者（完全控制场景需要外部锚点，超出 local-first 范围）
- 来源注册表防的是**误归属**与无凭证写入，不防"攻击者本身就是某个已注册
  来源"的情况——多来源互信是运营问题，不是单机代码问题
- 检索层不因某条记忆"可疑"而隐藏——治理靠显式 retire/correct 走流程，这
  是刻意设计（审计可见性优先于静默过滤）

参考：AgentPoison (NeurIPS 2024) · Sleeper Memory Poisoning (arXiv:2605.15338)
· PoisonRecall (attack taxonomy & defenses, MIT)。
### v2 修订说明（2026-10-09 对抗评审后）

初版映射表被对抗评审（模拟 PoisonRecall 作者视角）指出三类过度声明，已修正：
1. **初始事实注入无拦截**——补入映射表并列为已知局限；audit 提供事后追溯而非事前防御
2. **supersession 是法证工具**——不校验 corrector 身份，"覆盖式投毒"实际会发生，防御仅在可审计性
3. **retire 对运行时即抹除**——"不删除"仅指 SQLite 行；检索层（向量/FTS/active）全部移除，历史保护≠可用性保护
对应证明与负例见 `tests/test_poisoning_defense.py`。