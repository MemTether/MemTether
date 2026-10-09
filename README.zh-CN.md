<div align="center">

<img src="assets/demo.gif" width="100%" alt="MemTether — 跨客户端 AI 记忆演示"/>

# MemTether

**你的 AI agents 什么都忘。MemTether 给它们一个共享大脑。**

一份物理 SQLite 数据库，通过文件级指针被所有 AI 客户端共享。
零云端、零 API 费用、零同步进程。可 grep、可 git、永远属于你。

[![PyPI](https://img.shields.io/pypi/v/memtether)](https://pypi.org/project/memtether/)
[![CI](https://github.com/MemTether/MemTether/actions/workflows/ci.yml/badge.svg)](https://github.com/MemTether/MemTether/actions)

[**在线试用**](https://huggingface.co/spaces/lanbass/memtether-demo) · [文档站](https://memtether.github.io/MemTether/site/en/) · [复现指南](https://memtether.github.io/MemTether/site/en/reproduce.html) · [English](README.md)

</div>

---

## 快速开始

```bash
pip install memtether
memtether init    # 创建数据库 + 自动连接所有检测到的 AI 客户端
```

<details>
<summary>可选：本地语义检索（不联网、不付费）</summary>

```bash
pip install "memtether[vector]"          # 1. 依赖（chromadb、onnxruntime）
memtether download-models                # 2. 权重（bge-m3-int8 约560MB；--profile bge-small-zh 约46MB）
python -m memsearch --rebuild            # 3. 建向量索引
```

不装模型时检索自动降级为关键词 + BM25，功能不受影响。
</details>

就这样。MemTether 自动检测你机器上的 Claude Code、Cursor、Windsurf、Codex、Gemini CLI 等 23 个客户端，写入 MCP 配置并验证。

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

### 连接你的客户端

```bash
memtether init           # 全部自动
memtether setup claude-code   # 逐个接入
```

**支持 23 个客户端**：Claude Code / Desktop, Cursor, Windsurf, VS Code, Zed, JetBrains, Cline, Roo Code, Kilo Code, Continue, Cody, Amazon Q, Gemini CLI, Neovim, Codex CLI, WorkBuddy (CN + Intl), CodeBuddy, ZCode, DSH, OpenClaw

---

## 安全

- **记忆投毒防御**：OWASP LLM01/02/06（12 条规则）
- **输入验证**：content ≤10K / type 白名单 / source 格式
- **作用域隔离**：shared / private / restricted
- **并发写保护**：Hubguard 文件锁 + 原子替换
- **SQL**：全部参数化

---

## 许可证

[Apache-2.0](LICENSE)

## 评测与诚实边界

我们不掩盖弱点。以下数字全部可复现（复现指南见文档站）：

| 评测 | 口径 | 结果 |
|---|---|---|
| LongMemEval-S 500Q（MemTether+EAF） | strict | 60.0%（多会话子集 +11.2pp，配对 p=0.008） |
| LongMemEval-S 500Q（LangMem 基线，我们复跑） | strict / LLM judge | 21.8% / 6.6%，NOT_FOUND 79.2% |
| MemDaily（MemSim 官方数据集，纯检索无 LLM judge，k=15） | 答案子串命中 | simple 46.7% · noisy 0%（干扰前导致检索偏移——已知多会话弱点） |
| 合成多会话 SGM 对照（20 实体×25 会话） | 召回数 | SGM 84% vs embedding 64%；加 300+ 干扰消息后仍 84% vs 64% |

### 已知局限（附可执行证明）

- **初始事实注入无拦截**：local-first 不做写入前真实性判断——任何注册来源写入的新事实立即可被检索。防御是事后可审计（来源归属 + 哈希链 + supersession 法证链），见 `tests/test_poisoning_defense.py::test_F`（负例测试，把这条局限钉进测试）
- **记忆投毒威胁模型**：针对 persistent memory 的投毒攻击（覆盖/抹除/休眠/跨租户）的完整攻防映射见 [SECURITY.md](SECURITY.md)
- **审计锚点与数据同库**：防绕过应用层的静默篡改；对拥有全库写权限的攻击者无效（需外部锚点，roadmap）
- 多会话聚合仍是全行业难点——SGM 的 SQL 确定性路由是我们当前最有效的逃逸路径，但只覆盖「实体→属性」型查询，关系路径型问题（"通过 A 认识 B 的人"）需要多跳推理，尚未解决