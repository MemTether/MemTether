#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive Evidence Assembly (AEA) for CCF-B."""
import sys, os, json, re, time, gc, argparse
from collections import defaultdict

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Use separate DB to avoid lock conflicts
AEA_DB = r'E:\Temp\aea_scratch.db'
AEA_CHROMA = r'E:\Temp\aea_chroma'
AEA_COLLECTION = 'aea_bench'


def classify_question_complexity(question):
    q = question.lower().strip()
    if re.search(r'how many\b|number of\b|count\b|total\b|sum\b|altogether\b|combined\b', q):
        return 'aggregation'
    if re.search(r'difference\b|how much (more|less|longer|shorter)\b|compared to\b|percentage\b|average\b', q):
        return 'comparison'
    if re.search(r'when (did|was|is|were)\b|first time\b|last time\b|how long\b|duration\b|how many (days|weeks|months|years)\b', q):
        return 'temporal'
    return 'simple'


def route_to_strategy(tier):
    routes = {
        'simple': 'flat_truncated',
        'temporal': 'graph_evidence',
        'comparison': 'eaf',
        'aggregation': 'eaf',
    }
    return routes.get(tier, 'eaf')


def _extract_entities(item):
    entities = set()
    for session in item.get('haystack_sessions', []):
        for turn in session:
            content = turn.get('content', '')
            for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', content):
                entities.add(m.group(0))
    return ', '.join(sorted(entities)[:30])


def build_evidence_for_tier(item, results, tier, summaries=None, k=12):
    if tier == 'simple':
        return ' | '.join((r.get('content', ''))[:500] for r in results[:k])
    elif tier == 'temporal':
        ev = '\n'.join(r.get('content', '') for r in results[:k])
        entities = set()
        timestamps = set()
        for session in item.get('haystack_sessions', []):
            for turn in session:
                content = turn.get('content', '')
                for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', content):
                    entities.add(m.group(0))
                for m in re.finditer(
                    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}|'
                    r'\d{4}-\d{2}-\d{2}|'
                    r'(yesterday|last week|last month|next week|next month|today|tomorrow)',
                    content, re.IGNORECASE
                ):
                    timestamps.add(m.group(0))
        entity_list = ', '.join(sorted(entities)[:30])
        time_list = ', '.join(sorted(timestamps)[:20])
        return '[KNOWN ENTITIES]\n%s\n\n[TEMPORAL MARKERS]\n%s\n\n[EVIDENCE]\n%s' % (entity_list, time_list, ev)
    else:
        ev = '\n'.join(r.get('content', '') for r in results[:k])
        if summaries:
            sum_text = '\n'.join(['[Session %d]: %s' % (j+1, s) for j, s in enumerate(summaries[:5])])
            prefix = '[KNOWN ENTITIES]\n%s\n\n' % _extract_entities(item) if tier == 'comparison' else ''
            return '%s[SESSION SUMMARIES]\n%s\n\n[EVIDENCE]\n%s' % (prefix, sum_text, ev)
        return ev


def run_aea(sample=133, k=12):
    import memsearch
    import bench_longmemeval as bl
    from bench_longmemeval import judge_strict
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
    
    stats = {'aea': {'strict': 0, 'strict_n': 0, 'na': 0}}
    tier_counts = defaultdict(int)
    per_question = {}
    
    for bi, (idx, item) in enumerate(ms_items, 1):
        q = item['question']
        gold = str(item['answer']).strip()
        
        tier = classify_question_complexity(q)
        strategy = route_to_strategy(tier)
        tier_counts[tier] += 1
        
        if bi <= 10:
            print('  [%d/%d] idx=%d tier=%s -> %s' % (bi, len(ms_items), idx, tier, strategy))
        
        for _retry in range(3):
            try:
                bl.build_scratch_db(item, db_path=AEA_DB)
                break
            except Exception:
                gc.collect()
                import time as _t
                _t.sleep(2)
        else:
            raise RuntimeError('build_scratch_db failed after 3 retries')
        memsearch.DB = AEA_DB
        memsearch.CHROMA_PATH = AEA_CHROMA
        memsearch.COLLECTION = AEA_COLLECTION
        try:
            try:
                memsearch.rebuild_vector_index(verbose=False, reuse=True)
            except:
                pass
            results = memsearch.search_hybrid(q, limit=k, decay=False).get('results', [])
            
            item_summaries = summaries.get(str(idx), summaries.get(idx, None))
            evidence = build_evidence_for_tier(item, results, tier, item_summaries, k)
            
            prompt = 'Based on the context below, answer the question. Give ONLY the direct answer (a number, name, or short phrase). If the context does not contain enough information, reply exactly: NOT_ENOUGH_INFO.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:' % (evidence[:4000], q)
            answer = call_llm(prompt, max_tokens=200)
            
            per_question[idx] = {'tier': tier, 'strategy': strategy}
            
            if not answer:
                stats['aea']['na'] += 1
                per_question[idx]['score'] = None
                continue
            
            s = judge_strict(answer, gold)
            if s is None:
                stats['aea']['na'] += 1
                per_question[idx]['score'] = None
            else:
                stats['aea']['strict_n'] += 1
                per_question[idx]['score'] = 1 if s else 0
                if s:
                    stats['aea']['strict'] += 1
            
            if bi % 10 == 0:
                d = stats['aea']
                sr = d['strict'] / d['strict_n'] if d['strict_n'] else 0
                print('    AEA %d/%d = %.1f%%' % (d['strict'], d['strict_n'], sr*100))
                
        except Exception as e:
            print('    ERROR: %s' % str(e)[:80])
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
        gc.collect()
        time.sleep(0.5)
    
    print('\n' + '=' * 72)
    print('ADAPTIVE EVIDENCE ASSEMBLY (AEA) | %d multi-session questions' % len(ms_items))
    print('=' * 72)
    d = stats['aea']
    sr = d['strict'] / d['strict_n'] if d['strict_n'] else 0
    print('  AEA strict: %d/%d = %.1f%%  [NA: %d]' % (d['strict'], d['strict_n'], sr*100, d['na']))
    print('  Tier distribution: %s' % dict(tier_counts))
    print()
    
    prev = json.load(open(r'E:\Temp\strategy_ablation_result.json', encoding='utf-8'))
    print('  Comparison (variable denominator):')
    for s in ['flat_truncated', 'eaf']:
        p = prev['stats'][s]
        psr = p['strict'] / p['strict_n'] if p['strict_n'] else 0
        print('    %-20s %d/%d = %.1f%%' % (s, p['strict'], p['strict_n'], psr*100))
    print('    %-20s %d/%d = %.1f%%' % ('AEA', d['strict'], d['strict_n'], sr*100))
    print('=' * 72)
    
    output = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'system': 'AEA (Adaptive Evidence Assembly)',
        'benchmark': 'LongMemEval multi-session',
        'sample': len(ms_items),
        'stats': stats,
        'tier_distribution': dict(tier_counts),
        'per_question': per_question,
    }
    outpath = r'E:\Temp\aea_result.json'
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('Saved to %s' % outpath)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=133)
    a = ap.parse_args()
    run_aea(sample=a.sample)

