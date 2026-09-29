---
title: MemTether Demo
emoji: 🧠
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: "4.44.1"
app_file: app.py
pinned: true
license: apache-2.0
short_description: 跨客户端 AI 记忆中枢 — 文件级指针共享同一物理 SQLite
---

# MemTether — 跨客户端 AI 记忆中枢

多个异构 AI 客户端通过 **文件级指针**（junction/symlink）共享同一份物理 SQLite —— 不做同步、不做云、不做副本，物理上消灭"各存一份"。

## 这个 Space 演示什么

| Tab | 演示的生产链路 |
|---|---|
| 🔍 检索 | keyword 路（生产版：五路召回 + RRF + cross-encoder 精排 + 拒答闸门） |
| ✍️ 写入 | gateway.remember() 最小行为：来源归属强制 + 双时间轴 |
| 📊 统计 | hub_score 四维评分卡的数据口径 |
| 🔗 治理 | supersession 替代链（不删旧）+ 冲突检测（人工复核候选生成器） |

运行在**全合成演示库**上（100 facts + 10 assets，零个人数据）。

## 与同类项目的核心差异

- **文件级指针而非同步** — 无同步逻辑所以不漂移
- **双时间轴** — 能回答"09-15 那天系统认为什么为真"
- **supersession 不删旧** — 纠正是版本链不是覆盖
- **评测诚信** — 分数连失真声明一起报，"分数高先怀疑卷子"

## 本地完整体验

```bash
pip install "memtether[vector]"
python -m memsearch --rebuild
uvicorn api_server:app --port 8080
```

GitHub: https://github.com/MemTether/MemTether · PyPI: https://pypi.org/project/memtether/
