"""
Generate session summaries for multi-session questions using glm-5.3-flash.
One-time preprocessing - saves summaries to a JSON file.
"""
import sys, os, json, time
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, 'E:/RUANJIAN/memory_hub')

import requests

def llm_call(prompt, max_tokens=500):
    data = json.dumps({'model': 'cn:glm-5.3-flash', 'input': prompt,
                       'max_output_tokens': max_tokens, 'temperature': 0}).encode('utf-8')
    req = requests.Request('POST', 'http://127.0.0.1:8814/v1/responses',
                          data=data, headers={'Content-Type': 'application/json'})
    prepared = req.prepare()
    with requests.Session() as s:
        resp = s.send(prepared, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        for item in result.get('output', []):
            if item.get('type') == 'message':
                for c in item.get('content', []):
                    if c.get('type') == 'output_text' and c['text'].strip():
                        return c['text'].strip()
        return None

data = json.load(open('E:/RUANJIAN/memory_hub/bench_data/longmemeval_oracle', encoding='utf-8'))
ms_indices = list(range(len(data)))
print(f"Generating summaries for {len(ms_indices)} questions...")

# Check existing summaries
outpath = 'E:/RUANJIAN/memory_hub/bench_data/session_summaries.json'
existing = {}
if os.path.exists(outpath):
    existing = json.load(open(outpath, encoding='utf-8'))
    print(f"Found {len(existing)} existing summaries")

summaries = dict(existing)
generated = 0
for idx in ms_indices:
    qid = str(idx)
    if qid in summaries:
        continue
    
    item = data[idx]
    sessions = item.get('haystack_sessions', [])
    session_sums = []
    
    for sid, session in enumerate(sessions):
        # Concatenate session turns
        text = '\n'.join([t.get('content', '') for t in session if t.get('content')])
        if not text.strip():
            session_sums.append('')
            continue
        
        # Truncate to 2000 chars for summary prompt
        text = text[:2000]
        prompt = f"Summarize this conversation segment in 2-3 sentences. Focus on key facts, numbers, dates, and decisions.\n\n{text}\n\nSummary:"
        
        summary = llm_call(prompt, max_tokens=300)
        session_sums.append(summary or text[:200])
        time.sleep(1)
    
    summaries[qid] = session_sums
    generated += 1
    
    if generated % 10 == 0:
        print(f"Generated {generated}/{len(ms_indices)} summaries...", flush=True)
        # Save checkpoint
        with open(outpath, 'w', encoding='utf-8') as f:
            json.dump(summaries, f, ensure_ascii=False)

# Final save
with open(outpath, 'w', encoding='utf-8') as f:
    json.dump(summaries, f, ensure_ascii=False)

print(f"\nDone! Generated {generated} new summaries, total {len(summaries)}")
print(f"Saved to {outpath}")


