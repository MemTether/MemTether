# -*- coding: utf-8 -*-
"""
extract.py — T4-A: LLM 自动记忆抽取

从对话记录（transcript）中用 LLM 提取可复用的长期记忆，写入 gateway.remember()。
设计原则：
  1. 只依赖标准库（urllib.request），不新增任何 pip 依赖
  2. OpenAI 兼容端点（DeepSeek / OpenAI / Ollama / vLLM / 本地反代均可）
  3. 配置通过环境变量，不硬编码任何 key：
     - MEM_EXTRACT_BASE_URL  默认 https://api.deepseek.com/v1
     - MEM_EXTRACT_MODEL     默认 deepseek-chat
     - MEM_EXTRACT_API_KEY   必填（fail-closed）
  4. 输出严格 JSON，由 gateway.remember() 做去重 + supersession + 归属校验
  5. --dry-run 只展示不写入

用法：
  python extract.py transcript.txt                    # 从文件读取
  cat transcript.txt | python extract.py -            # 从 stdin
  python extract.py transcript.txt --dry-run          # 只展示不写入
  python extract.py transcript.txt --source myagent   # 指定来源（必填）
  python extract.py transcript.txt --max-chars 12000  # 截断输入

环境变量配置：
  MEM_EXTRACT_BASE_URL=https://api.deepseek.com/v1
  MEM_EXTRACT_MODEL=deepseek-chat
  MEM_EXTRACT_API_KEY=sk-...
"""
import os
import sys
import json
import time
import argparse

sys.stdout.reconfigure(encoding='utf-8')

EXTRACT_SYSTEM = (
    '你是长期记忆抽取器。从对话记录中提取值得长期保存的结论性记忆。\n'
    '只输出 JSON，不要输出任何其他文字。\n'
    '格式：\n'
    '{"memories": [\n'
    '  {"content": "一句话结论（首句就是结论，≤160字）",\n'
    '   "type": "fact|decision|incident|experience",\n'
    '   "tags": "逗号分隔的关键词，可选",\n'
    '   "confidence": 0.0-1.0}\n'
    ']}\n'
    '\n'
    '规则：\n'
    '- fact = 客观事实/工具路径/配置项；decision = 拍板结论/否决/选型；\n'
    '  incident = 踩坑 + 修复方案；experience = 经验教训/方法论。\n'
    '- 只保留未来可能复用的结论：不记临时状态、过程步骤、闲聊、猜测。\n'
    '- 每条 content 的第一句必须是完整结论（投影系统只取首句）。\n'
    '- 不要记录 API key、密码、token 等敏感信息。\n'
    '- 如果对话中没有值得记录的内容，返回 {"memories": []}。\n'
    '- 最多提取 20 条，超过只保留最重要的。'
)


def call_llm(text, base_url, api_key, model, max_tokens=4000):
    """调用 OpenAI 兼容 chat/completions，返回 dict 或 None。"""
    import urllib.request
    import urllib.error
    try:
        url = base_url.rstrip('/') + '/chat/completions'
        body = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': EXTRACT_SYSTEM},
                {'role': 'user', 'content': text},
            ],
            'temperature': 0.1,
            'max_tokens': max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode('utf-8'),
            headers={
                'Authorization': 'Bearer ' + api_key,
                'Content-Type': 'application/json',
            })
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read().decode('utf-8'))
        content = d['choices'][0]['message']['content']
        content = content.strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[-1]
            if content.endswith('```'):
                content = content[:-3]
        return json.loads(content)
    except Exception as e:
        return {'_error': str(e)[:300]}


def extract_memories(text, base_url=None, api_key=None, model=None, max_tokens=4000):
    """从文本中提取记忆候选。返回 list of dict。"""
    base_url = base_url or os.environ.get('MEM_EXTRACT_BASE_URL', 'https://api.deepseek.com/v1')
    api_key = api_key or os.environ.get('MEM_EXTRACT_API_KEY', '')
    model = model or os.environ.get('MEM_EXTRACT_MODEL', 'deepseek-chat')
    if not api_key:
        return [], 'MEM_EXTRACT_API_KEY not set (fail-closed)'
    if not text.strip():
        return [], 'empty input'

    # 截断保护
    if len(text) > 32000:
        text = text[:32000] + '\n...[truncated]'

    res = call_llm(text, base_url, api_key, model, max_tokens)
    if res is None or '_error' in res:
        return [], (res or {}).get('_error', 'llm_failed')
    memories = res.get('memories', [])
    # 基本校验：必须有 content
    valid = []
    for m in memories:
        c = (m.get('content') or '').strip()
        if c and len(c) >= 10:
            valid.append(m)
    return valid, None


def write_memories(memories, source, dry_run=False):
    """把候选记忆写入 gateway。返回写入结果列表。"""
    results = []
    if dry_run:
        for m in memories:
            results.append({'ok': True, 'op': 'dry_run', 'content': m['content'][:80]})
        return results

    import gateway
    for m in memories:
        try:
            r = gateway.remember(
                content=m['content'],
                type=m.get('type', 'fact'),
                source=source,
                tags=m.get('tags', ''),
                confidence=float(m.get('confidence', 0.8)),
            )
            results.append({'ok': r.get('ok', False), 'op': r.get('op', ''),
                           'uid': r.get('uid', ''), 'content': m['content'][:80]})
        except Exception as e:
            results.append({'ok': False, 'error': str(e)[:200], 'content': m['content'][:80]})
    return results


def main():
    parser = argparse.ArgumentParser(description='LLM 自动记忆抽取')
    parser.add_argument('input', help='对话记录文件路径，或 - 表示 stdin')
    parser.add_argument('--source', required=True, help='写入来源（如 codex、workbuddy）')
    parser.add_argument('--dry-run', action='store_true', help='只展示不写入')
    parser.add_argument('--max-chars', type=int, default=32000, help='输入截断上限')
    args = parser.parse_args()

    # 读取输入
    if args.input == '-':
        text = sys.stdin.read()
    else:
        with open(args.input, encoding='utf-8', errors='replace') as f:
            text = f.read()
    if len(text) > args.max_chars:
        text = text[:args.max_chars] + '\n...[truncated]'

    print(f'[extract] input: {len(text)} chars, source={args.source}, dry_run={args.dry_run}', file=sys.stderr)

    memories, err = extract_memories(text)
    if err:
        print(f'[extract] ERROR: {err}', file=sys.stderr)
        sys.exit(1)

    print(f'[extract] extracted {len(memories)} memories', file=sys.stderr)
    for m in memories:
        print(f'  [{m.get("type", "?")}] {m["content"][:100]}')

    if not memories:
        print('[extract] nothing worth remembering', file=sys.stderr)
        return

    results = write_memories(memories, args.source, dry_run=args.dry_run)
    ok = sum(1 for r in results if r.get('ok'))
    print(f'[extract] written: {ok}/{len(results)}', file=sys.stderr)
    for r in results:
        status = 'OK' if r.get('ok') else 'FAIL'
        print(f'  {status} {r.get("op", r.get("error", ""))} {r.get("content", "")[:80]}')


if __name__ == '__main__':
    main()
