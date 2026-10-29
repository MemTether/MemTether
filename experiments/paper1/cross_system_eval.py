#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-System Evaluation: MemTether vs Mem0 vs LangMem on LongMemEval
Runs 100Q stratified sample for each system.
"""
import sys, os, json, time, gc, argparse
from collections import defaultdict

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def stratified_sample(items, n, seed=42):
    import random
    rnd = random.Random(seed)
    by_type = defaultdict(list)
    for i, x in enumerate(items):
        by_type[x['question_type']].append(i)
    total = len(items)
    out = []
    for t, lst in sorted(by_type.items()):
        cnt = max(1, round(n * len(lst) / total))
        out.extend(rnd.sample(lst, min(cnt, len(lst))))
    return sorted(out)

def run_langmem_100q(sample=100, k=10):
    """Run LangMem on stratified sample."""
    from langmem_adapter import create_langmem_memory, langmem_add_sessions, langmem_search
    from bench_longmemeval import judge_strict
    
    data = json.load(open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8'))
    idxs = stratified_sample(data, sample)
    
    per_type = defaultdict(lambda: {'n': 0, 'strict': 0, 'strict_n': 0, 'strict_na': 0})
    strict_hits, strict_total, strict_na = 0, 0, 0
    
    for bi, idx in enumerate(idxs):
        item = data[idx]
        q = item['question']
        gold = str(item['answer']).strip()
        q_type = item['question_type']
        user_id = f'lme_user_{idx}'
        
        r = {'idx': idx, 'type': q_type, 'status': 'unknown'}
        
        try:
            # Create memory for this question
            memory = create_langmem_memory()
            
            # Add sessions
            langmem_add_sessions(memory, item.get('haystack_sessions', []), user_id)
            
            # Search
            memories = langmem_search(memory, q, user_id, top_k=k)
            
            # Extract text from memories
            if isinstance(memories, list):
                pred = ' | '.join(str(m.get('content', m.get('memory', '')))[:400] for m in memories[:k])
            elif isinstance(memories, dict):
                results = memories.get('results', memories.get('memories', []))
                pred = ' | '.join(str(m.get('content', m.get('memory', '')))[:400] for m in results[:k])
            else:
                pred = str(memories)[:2000]
            
            # Strict scoring
            s = judge_strict(pred, gold)
            if s is None:
                strict_na += 1
                per_type[q_type]['strict_na'] += 1
            else:
                strict_total += 1
                per_type[q_type]['strict_n'] += 1
                if s:
                    strict_hits += 1
                    per_type[q_type]['strict'] += 1
                    r['strict'] = 1
                else:
                    r['strict'] = 0
                r['strict_n'] = 1
                r['status'] = 'done'
            
            if (bi+1) % 10 == 0:
                sr = strict_hits / strict_total if strict_total else 0
                print(f'  LangMem [{bi+1}/{len(idxs)}] strict={strict_hits}/{strict_total} ({sr*100:.1f}%)')
                
        except Exception as e:
            r['status'] = 'error'
            r['error'] = str(e)[:100]
            print(f'  [{bi+1}] ERROR: {str(e)[:80]}')
        
        gc.collect()
        time.sleep(0.5)
    
    sr = strict_hits / strict_total if strict_total else 0
    print(f'\nLangMem 100Q: strict={strict_hits}/{strict_total} = {sr*100:.1f}%  [NA: {strict_na}]')
    print('Per-type:', json.dumps(dict(per_type), indent=2))
    
    output = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'system': 'LangMem',
        'benchmark': 'LongMemEval oracle',
        'sample': len(idxs),
        'strict': round(sr, 4),
        'strict_hits': strict_hits,
        'strict_n': strict_total,
        'strict_na': strict_na,
        'per_type': dict(per_type),
    }
    outpath = r'E:\Temp\langmem_100q_result.json'
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'Saved to {outpath}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=100)
    ap.add_argument('--system', default='langmem', choices=['langmem'])
    a = ap.parse_args()
    if a.system == 'langmem':
        run_langmem_100q(sample=a.sample)
