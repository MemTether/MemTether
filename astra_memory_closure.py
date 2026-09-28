# -*- coding: utf-8 -*-
"""
把"记忆何时自动记"这个根本缺口交给 astra 深度裁决。
问题：记忆中枢有"记什么/怎么存/怎么找"，但缺"何时自动记"——写入靠手动 remember，
任务做完后结论不会自动沉淀，导致下次遇到同类问题还得重新踩坑。
"""
import sys, os, json, urllib.request, time

sys.path.insert(0, r'<AUDIT>')
import cred_env
cred_env.env()

KEY = os.environ['GPTX_ASTRA_KEY']
URL = 'https://api.gptx.cc/v1/chat/completions'

PROMPT = """你是一位系统架构师，负责设计一个"跨 AI agent 长期记忆中枢"的最终方案。

# 背景

我已建成一个记忆中枢（SQLite 权威账本 + Mem0 语义引擎 + ChromaDB 向量库），
三个 agent（WorkBuddy/DeepSeek、OpenClaw/grok、豆包）通过 gateway.py 统一读写。
实测能力全部通过：
- 自动提取（Mem0 一句话拆多条事实）
- 冲突消解（改口自动 UPDATE/ADD/DELETE）
- 语义检索（近义匹配）
- 任务配方召回（resolve_task "生图" 秒返回 ComfyUI 路径+命令+已知坑）

# 核心缺口（要你解决的根本问题）

记忆中枢"记得住"，但"不会自动记"。具体表现：
1. 今天做了一件复杂的事（把豆包 9GB 数据从 C 盘转移到 E 盘，用 Junction 目录联接，
   踩了 robocopy 海量小文件极慢、cmd 被安全策略拦、COM 对象被拦等坑），
   但这件事没有被自动写进中枢——因为写入靠人工调 remember，没人调就不记。
2. 结果：下次遇到"转移数据/清理C盘"这类任务，还得重新踩一遍同样的坑。

# 本质矛盾

真正的记忆系统应该是"事件驱动的自动闭环"，但现在的架构是"人驱动的被动记录"。
问题在于：agent 干完活后，由谁、在什么时机、依据什么标准，把"值得记的结论"
自动提炼并沉淀进中枢？

# 请你裁决以下关键设计问题

1. **触发机制**：自动记录应该挂在哪个环节？
   - A. 任务结束后（agent 每次 run 完，强制反思"这轮有什么值得记"）
   - B. 出错时（遇到坑/报错/反复重试，立即记录 incident）
   - C. 检索miss时（用户问的问题中枢答不上来，触发"补录"）
   - D. 定时（每日/每周维护脚本扫工作日志，提炼结论）
   - 还是组合？各占什么权重？

2. **由谁提炼**：自动记录时，事实提取用哪个模型？
   - 便宜的 deepseek（快，1.3s）还是强的 astra（慢，110s）？
   - 什么场景用哪个？

3. **记什么不记什么**：怎么避免"什么垃圾都记进去"导致中枢膨胀？
   - 判定"值得记"的标准是什么？置信度阈值？重复度检测？
   - 哪些类型的结论值得进中枢（技术坑/工具路径/决策），哪些不值得（临时状态/一次性任务）？

4. **闭环的落点**：这些自动记录逻辑应该放在哪？
   - gateway.py 加新子命令（如 auto_reflect / incident / on_miss）？
   - 还是独立一个 reflector.py 守护进程/钩子？
   - 三个 agent 怎么各自接入？

5. **最务实的 MVP**：如果只能先做一件事，让"记忆自动闭环"先跑起来，
   你建议做哪个？给出具体的落地步骤（代码级/命令级），不要泛泛而谈。

# 约束

- 环境是 Windows，Python 3.13，凭据走 cred_env。
- 成本敏感：deepseek 便宜（¥1/M in, ¥2/M out），astra 贵（¥10/M in, ¥50/M out）。
- 不能引入需要常驻内存的复杂服务（优先轻量、可被计划任务/钩子触发）。
- 已有 gateway.py（SQLite+Mem0），尽量在它基础上扩展，不推倒重来。

请给出：
1. 一句话结论（方案核心）
2. 触发机制设计（明确选型+理由）
3. 提炼模型选型（明确场景分流）
4. "值得记"的判定标准（可操作）
5. MVP 落地步骤（具体到新增什么函数/子命令、怎么调用）
6. 你能预见的坑
"""

body = {
    'model': 'gpt-6-astra',
    'messages': [{'role': 'user', 'content': PROMPT}],
    'temperature': 0.2,
    'max_tokens': 4000,
}

print('=== 调用 astra 深度裁决（预计 60-120s）===')
t0 = time.time()
req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                             headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
try:
    r = urllib.request.urlopen(req, timeout=360)
    d = json.loads(r.read().decode())
    msg = d['choices'][0]['message']['content']
    usage = d.get('usage', {})
    elapsed = time.time() - t0
    print('耗时 %.1fs' % elapsed)
    print('usage:', json.dumps(usage, ensure_ascii=False))
    print('=' * 60)
    print(msg)
    # 落盘
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'astra_verdict_closure.md'), 'w', encoding='utf-8') as f:
        f.write('# astra 深度裁决：记忆自动闭环\n\n')
        f.write('> 耗时 %.1fs | prompt %s | completion %s\n\n' % (
            elapsed, usage.get('prompt_tokens', '?'), usage.get('completion_tokens', '?')))
        f.write(msg)
    print('\n\n[已落盘 astra_verdict_closure.md]')
except Exception as e:
    print('astra 调用失败:', str(e)[:300])
