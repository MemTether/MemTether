#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retrieval Strategy Ablation with LLM generation."""
import sys, os, json, time, gc, argparse, re
from collections import defaultdict

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def get_flat_truncated(results, k=12):
    return ' | '.join((r.get('content', ''))[:500] for r in results[:k])

def get_full_evidence(results, k=12):
    return '\n'.join(r.get('content', '') for r in results[:k])

def get_eaf(results, k=12, summaries=None):
    ev = get_full_evidence(results, k)
    if summaries:
        sum_text = '\n'.join(['[Session %d]: %s' % (j+1, s) for j, s in enumerate(summaries[:5])])
        return '[SESSION SUMMARIES]\n%s\n\n[EVIDENCE]\n%s' % (sum_text, ev)
    return ev

def get_graph_evidence(item, results, k=12):
    ev = get_full_evidence(results, k)
    entities = set()
    for session in item.get('haystack_sessions', []):
        for turn in session:
            content = turn.get('content', '')
            for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', content):
                entities.add(m.group(0))
    entity_list = ', '.join(sorted(entities)[:30])
    return '[KNOWN ENTITIES ACROSS SESSIONS]\n%s\n\n[EVIDENCE]\n%s' % (entity_list, ev)

def get_raw_chunks(item, k=12):
    chunks = []
    for si, session in enumerate(item.get('haystack_sessions', [])):
        chunk_text = '\n'.join(
            '[%s] %s' % (turn.get('role', '?'), turn.get('content', ''))
            for turn in session if turn.get('content')
        )
        chunks.append('[Session %d]\n%s' % (si+1, chunk_text))
    return '\n\n'.join(chunks)[:6000]

def run_ablation(sample=20, k=12):
    import memsearch
    from bench_longmemeval import judge_strict, build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION
    from llm_helper import call_llm
    
    data = json.load(open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8'))
    ms_items = [(i, x) for i, x in enumerate(data) if x['question_type'] == 'multi-session']
    if sample < len(ms_items):
        ms_items = ms_items[:sample]
    
    summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
    summaries = {}
    if os.path.exists(summaries_path):
        summaries = json.load(open(summaries_path, encoding='utf-8'))
    
    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    
    strategies = ['flat_truncated', 'full_evidence', 'eaf', 'graph_evidence', 'raw_chunks']
    stats = {s: {'strict': 0, 'strict_n': 0, 'na': 0} for s in strategies}
    per_question = {}  # idx -> {strategy: score/None}
    all_details = []
    
    for bi, (idx, item) in enumerate(ms_items, 1):
        q = item['question']
        gold = str(item['answer']).strip()
        
        print('  [%d/%d] idx=%d Q: %s' % (bi, len(ms_items), idx, q[:60]))
        
        build_scratch_db(item)
        memsearch.DB = BENCH_DB; memsearch.CHROMA_PATH = BENCH_CHROMA; memsearch.COLLECTION = BENCH_COLLECTION
        try:
            try:
                memsearch.rebuild_vector_index(verbose=False, reuse=True)
            except:
                pass
            results = memsearch.search_hybrid(q, limit=k, decay=False).get('results', [])
            
            item_summaries = summaries.get(str(idx), summaries.get(idx, None))
            
            evidence_map = {
                'flat_truncated': get_flat_truncated(results, k),
                'full_evidence': get_full_evidence(results, k),
                'eaf': get_eaf(results, k, item_summaries),
                'graph_evidence': get_graph_evidence(item, results, k),
                'raw_chunks': get_raw_chunks(item, k),
            }
            
            if idx not in per_question:
                per_question[idx] = {}
            for strategy, evidence in evidence_map.items():
                prompt = 'Based on the context below, answer the question. Give ONLY the direct answer (a number, name, or short phrase). If the context does not contain enough information, reply exactly: NOT_ENOUGH_INFO.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:' % (evidence[:4000], q)
                answer = call_llm(prompt, max_tokens=200)
                if not answer:
                    stats[strategy]['na'] += 1
                    per_question[idx][strategy] = None
                    continue
                s = judge_strict(answer, gold)
                if s is None:
                    stats[strategy]['na'] += 1
                    per_question[idx][strategy] = None
                else:
                    stats[strategy]['strict_n'] += 1
                    per_question[idx][strategy] = 1 if s else 0
                    if s:
                        stats[strategy]['strict'] += 1
            
            if bi % 5 == 0:
                for s in strategies:
                    d = stats[s]
                    sr = d['strict'] / d['strict_n'] if d['strict_n'] else 0
                    print('    %-20s %d/%d = %.1f%%' % (s, d['strict'], d['strict_n'], sr*100))
                    
        except Exception as e:
            print('    ERROR: %s' % str(e)[:80])
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
        gc.collect()
        time.sleep(0.5)
    
    print('\n' + '=' * 72)
    print('RETRIEVAL STRATEGY ABLATION | %d multi-session questions' % len(ms_items))
    print('=' * 72)
    print('  %-25s %-15s %-8s %-5s' % ('Strategy', 'Strict', 'Rate', 'NA'))
    print('-' * 72)
    for s in strategies:
        d = stats[s]
        sr = d['strict'] / d['strict_n'] if d['strict_n'] else 0
        print('  %-25s %d/%-5d %5.1f%%   %d' % (s, d['strict'], d['strict_n'], sr*100, d['na']))
    print('=' * 72)
    
    output = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'benchmark': 'LongMemEval multi-session', 'sample': len(ms_items), 'stats': stats, 'per_question': per_question}
    outpath = r'E:\Temp\strategy_ablation_result.json'
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('Saved to %s' % outpath)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=20)
    a = ap.parse_args()
    run_ablation(sample=a.sample)


