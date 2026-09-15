# 记忆中枢 README — 三 agent 共用（v3）

> 建立：2026-09-10（v2 双豆包账号）｜升级：2026-09-12（v3 三 agent）｜位置：`E:\RUANJIAN\memory_hub\`
> 用途：**让 WorkBuddy 侧 DeepSeek、OpenClaw 🦞、豆包 共用同一份记忆**

## 三个 agent

| 来源标识 | 是谁 | 怎么读 | 怎么写 |
|---|---|---|---|
| `workbuddy` | WorkBuddy 侧 DeepSeek（指挥官） | `agentctl.py mem-read` / `mem.py recall` | `agentctl.py mem-write <type> "..."` |
| `openclaw` | OpenClaw 🦞（agentId=main，执行体） | 投影 + `mem.py drain --agent openclaw` | `mem.py add --source openclaw` |
| `doubao_a` / `doubao_b` | 豆包 App 两账号 | `projections\account_*_prompt.md` 注入 | `mem.py add --source doubao_a` |
| `user` | 用户本人 | 直接看 md | `mem.py add --source user` |

注册表在 `agents.json`。**新增 agent 只改这一个文件**，然后跑 `mem.py render`。

## 唯一真源与派生物

- **`sink.json` = 唯一真源**（机器可读权威）。所有写入只经 `mem.py add`，带跨进程锁 + 原子替换写 + 自动备份轮转。
- **`experience.md` = 人读镜像**（自动追加；已有手写段落永不被重写）。
- **`DIGEST.md` / `projections\*.md` / `HUB_INDEX.md` = 渲染产物**，每次写入后自动重生成，可随时 `mem.py render` 重建。
- **`profile.md` 用户画像**（手写）、**`toolbox.json` 工具箱**（结构化）。
- 敏感信息仍走 DPAPI vault（`E:\RUANJIAN\.secure\vault\vault.bin`）；写入时 `sk-…`/长 key 自动脱敏为 `<见vault:key>`，本中枢只存引用。

## 写入质检规则（2026-09-12 新增，所有 agent 必须遵守）

> 背景：中枢曾沦为"谁都往里扔、没人做质检"的仓库（12 条并发压测垃圾长期滞留、下午的关键结论没人补记）。为避免重演，立下四条铁律 + 两道程序闸：

1. **只记结论，不记流水账**。原始日志、临时状态、一次性事件（"今天跑了个测试"）**拒收**。只进：跨 agent 结论、可复用经验、稳定偏好/约定。
2. **写完必自问**："三个月后别人搜到这条，还有用吗？" 没用就不写。
3. **补记优先于堆积**。发现"应该记但没记"的关键结论（如称呼约定、重大决策），当场补，别等。
4. **垃圾即清**。发现压测/测试类假数据混入真源，立即清理并备份（`sink.json` 备份到 `.backup/`）。

### 程序闸（已内置，无需自律）

- **入口质检（`add` 自动拦截）**：正文过短(<4字)、tag 命中 test/ping/压测/并发/benchmark 等、正文含"压测/连通性/自检/冒烟"等特征词 → 直接 `REJECTED` 拒绝入库。确为有效结论才加 `--force` 放行。
- **无锁直写已废**：`memory_sink.py` 不再裸写 sink.json，改为转发到带锁 + 质检的 `mem.py`。所有写入统一走 `mem.py add`。

### 蒸馏（智能提炼，`distill` 命令）

```
python mem.py distill --since "2026-09-01" [--source S] [--tag T] [--model gptx_astra] [--commit]
```

- 用最强模型（默认 `gptx_astra` = GPT-6-Astra）把散条**提炼成 1~3 条结论级记忆**：去流水账、合并近义重复、去过时、一句话说清结论。
- 默认**预览**（只打印不写回）；加 `--commit` 才写回中枢（走锁 + 质检 + 脱敏）。
- 实测（2026-09-12）：14 条散条 → 3 条结论（流程约定/安全治理/架构约定），质量高。
- 建议：定期（如每周）对新增条目跑一次 `distill --commit`，把散条压成结论，防止中枢膨胀。

## 为什么要有 mem.py（v2 的四个硬伤）

| 硬伤 | v3 解法 |
|---|---|
| 无归属：三个 agent 写进去分不清谁记的 | 每条带 `id / source / sources / seq` |
| 丢更新：读-改-写无锁，并发写互相覆盖 | msvcrt 跨进程文件锁 + `os.replace` 原子写（实测 12 并发写 0 丢失；旧写法丢 1） |
| 双真源：`experience.md` 与 `sink.json` 双写会漂移 | sink.json 为唯一真源，md/投影全部由它渲染 |
| 重复：同一教训被三个 agent 各记一遍 | 同（类型+规范化文本）自动合并，累积 `sources`/`count` |

另加：**增量水位**——每个 agent 记录 `last_seq`，`drain` 只吐它没读过的，避免重复注入。

## 常用命令

```bat
:: 沉淀（三 agent 通用，--source 必填且需已注册）
python mem.py add --type experience --text "..." --source openclaw --tag 网络 --ref 实验5

python mem.py list   --type experience --tail 20 [--source openclaw] [--tag 网络]
python mem.py search "关键词"
python mem.py since  "2026-09-12 12:00"
python mem.py drain  --agent openclaw            :: 取未读并推进水位（--peek 只看不推）
python mem.py recall --agent openclaw            :: 生成会话注入块（不推进水位）
python mem.py agents / verify / stats / render / migrate
```

> 兼容：旧脚本 `memory_sink.py add` 仍可用（等价于 `mem.py add --source legacy` 之外的 legacy 路径）；
> 但**新代码请一律走 `mem.py`**，否则拿不到锁与归属。

## 与会话注入的关系

- 新会话注入对应 agent 的投影文件（`projections\agent_<name>.md`；豆包保留 `account_a/b_prompt.md` 旧名）。
- 投影是轻量快照；要完整增量用 `drain`，要完整注入块用 `recall`。
- 系统偏好库（manage_preference）与中枢并存：**若系统偏好工具不可用，本中枢即权威记忆源**。
