# -*- coding: utf-8 -*-
"""
astra_dialogue.py — DeepSeek(V4.1) 与 Astra 的多轮技术协作对话引擎

用途：把"单方面提问"变成"真正的多轮交流"。
每轮：DeepSeek 发言 → 追加进对话历史 → 调 astra → astra 回答 → 存回历史。
下一轮 DeepSeek 可以看到完整历史（包括 astra 之前的回答），据此追问/质疑/补充。

用法：
  python astra_dialogue.py --say "我的发言"            # 追加一轮
  python astra_dialogue.py --say-file turn.txt        # 从文件读发言
  python astra_dialogue.py --show                     # 看当前对话历史
  python astra_dialogue.py --reset --system "..."     # 重置并设 system
"""
import os
import sys
import json
import time
import argparse
import urllib.request

sys.path.insert(0, r'<AUDIT>')
import cred_env
cred_env.env()

KEY = os.environ['GPTX_ASTRA_KEY']
URL = 'https://api.gptx.cc/v1/chat/completions'
HIST = r'<HUB>\astra_dialogue.json'


def load():
    if os.path.exists(HIST):
        with open(HIST, encoding='utf-8') as f:
            return json.load(f)
    return {'system': '', 'messages': []}


def save(d):
    with open(HIST, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def call_astra(system, messages, max_tokens=4000):
    msgs = []
    if system:
        msgs.append({'role': 'system', 'content': system})
    msgs.extend(messages)
    body = {'model': 'gpt-6-astra', 'messages': msgs, 'temperature': 0.3, 'max_tokens': max_tokens}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    t0 = time.time()
    r = urllib.request.urlopen(req, timeout=360)
    d = json.loads(r.read().decode())
    return d['choices'][0]['message']['content'], d.get('usage', {}), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--say', default=None)
    ap.add_argument('--say-file', default=None)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--reset', action='store_true')
    ap.add_argument('--system', default=None)
    ap.add_argument('--max-tokens', type=int, default=4000)
    a = ap.parse_args()

    d = load()

    if a.reset:
        d = {'system': a.system or '', 'messages': []}
        save(d)
        print('对话已重置。system 长度:', len(d['system']))
        return

    if a.show:
        print('=== SYSTEM ===')
        print(d.get('system', '')[:500])
        print('\n=== MESSAGES (%d) ===' % len(d['messages']))
        for i, m in enumerate(d['messages']):
            print('\n[%d][%s] %s' % (i, m['role'], m['content'][:300]))
        return

    say = a.say
    if a.say_file:
        with open(a.say_file, encoding='utf-8') as f:
            say = f.read()
    if not say:
        print('需要 --say 或 --say-file')
        return

    if a.system and not d.get('system'):
        d['system'] = a.system

    d['messages'].append({'role': 'user', 'content': say})
    print('>>> DeepSeek 发言 (%d 字)' % len(say))
    ans, usage, el = call_astra(d['system'], d['messages'], a.max_tokens)
    d['messages'].append({'role': 'assistant', 'content': ans})
    save(d)
    print('<<< Astra 回应 (%.1fs, prompt %s / completion %s)' % (
        el, usage.get('prompt_tokens', '?'), usage.get('completion_tokens', '?')))
    print('=' * 70)
    print(ans)


if __name__ == '__main__':
    main()
