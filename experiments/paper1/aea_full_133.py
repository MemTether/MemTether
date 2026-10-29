#!/usr/bin/env python3
"""
AEA on all 133 multi-session questions with per-question logging.
Routes each question by complexity tier:
- aggregation -> EAF (full evidence + session summaries)
- comparison -> EAF + entity graph overlay (approximated as full evidence + SSI)
- temporal -> graph evidence (approximated as SSI + retrieved evidence)
- simple -> flat truncated evidence
"""
import sys, os, json, time, gc, re
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\experiments\paper1')

def classify_tier(question, gold):
    """Route question to evidence assembly strategy."""
    q_lower = question.lower()
    gold_lower = str(gold).lower()
    
    # Aggregation: how many, total, sum, combined
    agg_kw = ['how many', 'how much', 'total', 'sum', 'average', 'difference', 'combined', 'altogether']
    if any(kw in q_lower for kw in agg_kw):
        return 'aggregation'
    
    # Temporal: when, how long, what time, duration
    temporal_kw = ['when did', 'how long', 'what time', 'duration', 'how many days', 'how many weeks']
    if any(kw in q_lower for kw in temporal_kw):
        return 'temporal'
    
    # Comparison: more, less, better, worse
    comp_kw = ['more', 'less', 'better', 'worse', 'compare', 'prefer']
    if any(kw in q_lower for kw in comp_kw):
        return 'comparison'
    
    # Simple: entity lookup
    return 'simple'

def build_evidence(item, tier, memsearch, summaries):
    """Build evidence based on routing tier."""
    q_text = item['question']
    if item.get('question_date'):
        q_text += f" (date: {item['question_date']})"
    
    res = memsearch.search_hybrid(q_text, limit=12, decay=False)
    results = res.get('results', [])
    
    if tier == 'aggregation':
        # EAF: full evidence + session summaries
        sess_ids = item.get('haystack_session_ids', [])
        sess_summaries = [summaries.get(sid, '')[:200] for sid in sess_ids if sid in summaries]
        ssi = '=== Session Summaries ===\n' + '\n'.join(sess_summaries) + '\n\n' if sess_summaries else ''
        return ssi + ' | '.join((r.get('content') or '') for r in results[:12])
    elif tier == 'temporal':
        # Graph evidence: SSI + evidence (temporal markers)
        sess_ids = item.get('haystack_session_ids', [])
        sess_summaries = [summaries.get(sid, '')[:150] for sid in sess_ids if sid in summaries]
        ssi = '=== Session Summaries (with dates) ===\n' + '\n'.join(sess_summaries) + '\n\n' if sess_summaries else ''
        return ssi + ' | '.join((r.get('content') or '') for r in results[:12])
    elif tier == 'comparison':
        # EAF + entity overlay: same as aggregation for now
        sess_ids = item.get('haystack_session_ids', [])
        sess_summaries = [summaries.get(sid, '')[:200] for sid in sess_ids if sid in summaries]
        ssi = '=== Session Summaries ===\n' + '\n'.join(sess_summaries) + '\n\n' if sess_summaries else ''
        return ssi + ' | '.join((r.get('content') or '') for r in results[:12])
    else:
        # Simple: flat truncated
        return ' | '.join((r.get('content') or '')[:300] for r in results[:12])

def main():
    import memsearch
    from bench_longmemeval import judge_strict, generate_answer, judge_llm, build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION, _assert_sandboxed

    # Load all questions, filter multi-session
    with open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8') as f:
        items = json.load(f)
    
    ms_items = [it for it in items if it.get('question_type') == 'multi-session']
    print(f'Multi-session questions: {len(ms_items)}')

    # Load summaries
    summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
    summaries = {}
    if os.path.exists(summaries_path):
        summaries = json.load(open(summaries_path, encoding='utf-8'))
    
    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    
    per_question = []
    tier_counts = defaultdict(int)
    
    ckpt_path = r'E:\Temp\aea_full_checkpoint.json'
    done = {}
    if os.path.exists(ckpt_path):
        ck = json.load(open(ckpt_path, encoding='utf-8'))
        done = {r['question_id']: r for r in ck.get('results', [])}
        print(f'Resuming from {len(done)} completed')

    for i, item in enumerate(ms_items, 1):
        qid = item.get('question_id', f'ms{i}')
        gold = str(item.get('answer', ''))
        question = item.get('question', '')
        
        if qid in done:
            per_question.append(done[qid])
            continue
        
        tier = classify_tier(question, gold)
        tier_counts[tier] += 1
        
        entry = {
            'question_id': qid, 'tier': tier,
            'gold': gold[:100], 'question': question[:100],
        }
        
        try:
            build_scratch_db(item)
            memsearch.DB = BENCH_DB
            memsearch.CHROMA_PATH = BENCH_CHROMA
            memsearch.COLLECTION = BENCH_COLLECTION
            _assert_sandboxed('post')
            
            try:
                memsearch.rebuild_vector_index(verbose=False, reuse=True)
            except:
                pass
            
            evidence = build_evidence(item, tier, memsearch, summaries)
            
            ans, gen_err = generate_answer(question, evidence, question_type='multi-session')
            
            if ans:
                s = judge_strict(ans, gold)
                ok, note = judge_llm(question, gold, ans)
                entry['strict'] = s
                entry['judge'] = ok
                entry['answer'] = ans[:150]
            else:
                entry['strict'] = None
                entry['judge'] = None
                entry['error'] = gen_err
            
            per_question.append(entry)
            
        except Exception as e:
            entry['strict'] = None
            entry['judge'] = None
            entry['error'] = f'{type(e).__name__}: {str(e)[:80]}'
            per_question.append(entry)
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
            gc.collect()
            time.sleep(0.2)
        
        if i % 20 == 0:
            print(f'  [{i}/{len(ms_items)}] done (tiers: {dict(tier_counts)})')
            with open(ckpt_path, 'w', encoding='utf-8') as f:
                json.dump({'ts': datetime.now().isoformat(), 'results': per_question}, f, ensure_ascii=False)
    
    # Final save
    with open(r'E:\Temp\aea_full_final.json', 'w', encoding='utf-8') as f:
        json.dump({'ts': datetime.now().isoformat(), 'n': len(ms_items), 'tier_distribution': dict(tier_counts), 'results': per_question}, f, ensure_ascii=False, indent=1)
    
    # Summary
    print(f'\n=== AEA Full Results ({len(ms_items)} multi-session) ===')
    print(f'Tier distribution: {dict(tier_counts)}')
    
    strict_hits, strict_total, strict_na = 0, 0, 0
    judge_hits, judge_total = 0, 0
    for pq in per_question:
        s = pq.get('strict')
        j = pq.get('judge')
        if s is None: strict_na += 1
        else:
            strict_total += 1
            if s: strict_hits += 1
        if j is not None:
            judge_total += 1
            if j: judge_hits += 1
    
    sr = strict_hits / strict_total * 100 if strict_total else 0
    jr = judge_hits / judge_total * 100 if judge_total else 0
    print(f'AEA strict: {strict_hits}/{strict_total} = {sr:.1f}% (NA={strict_na})')
    print(f'AEA judge: {judge_hits}/{judge_total} = {jr:.1f}%')
    
    # Per-tier
    print('\nPer-tier strict:')
    for tier in ['aggregation', 'comparison', 'temporal', 'simple']:
        hits, total, na = 0, 0, 0
        for pq in per_question:
            if pq.get('tier') != tier: continue
            s = pq.get('strict')
            if s is None: na += 1
            else:
                total += 1
                if s: hits += 1
        sr = hits / total * 100 if total else 0
        print(f'  {tier:15s}: {hits}/{total} = {sr:.1f}% (NA={na})')

if __name__ == '__main__':
    main()