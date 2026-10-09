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

| 攻击类 | MemTether 对应防御 | 机制 |
|---|---|---|
| 覆盖式投毒（改写既有事实） | supersession 链 | `correct()` 永不删除旧值，只置 `superseded` + 记录 `supersessions` 边（who/when/why） |
| 抹除攻击（删记忆灭迹） | retire 不删除 | `retire()` 只改 `status`，内容与时间戳保留 |
| 归属伪造 | source 注册表强制 | 未注册来源被降级/拒绝，所有写入强制 1-64 字符来源标识 |
| 事后灭证 | 哈希链锚点 | `audit()` 每批写入自动追加 SHA-256 锚点；直接改库 `verify_chain()` 必红 |
| 休眠检索（sleeper） | 检索层状态过滤 | 默认检索只取 `active`；被替代条目不作为当前事实返回 |
| 未经复核的“当前值” | TTL + 时间衰减 | 状态类结论建议带复核截止日，过期自动降权并标注 |

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