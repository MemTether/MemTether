#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAR Evaluation: Compare standard retrieval vs AAR on multi-session questions.
Runs on LongMemEval oracle multi-session subset.
"""
import sys, os, json, time, argparse
from collections import defaultdict

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run(sample=20, variant='oracle', use_llm=True, k=12):
    import memsearch
    from bench_longmemeval import judge_strict, build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION
    from aar import aar_pipeline
    from llm_helper import call_llm

    data = json.load(open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_%s' % variant, encoding='utf-8'))

    # Filter to multi-session only
    ms_items = [(i, x) for i, x in enumerate(data) if x['question_type'] == 'multi-session']
    if sample < len(ms_items):
        ms_items = ms_items[:sample]

    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)

    stats = {'baseline': {'strict': 0, 'strict_n': 0, 'na': 0},
             'aar': {'strict': 0, 'strict_n': 0, 'na': 0}}

    for bi, (idx, item) in enumerate(ms_items, 1):
        q = item['question']
        gold = str(item['answer']).strip()
        q_type = item['question_type']

        print('  [%d/%d] idx=%d type=%s' % (bi, len(ms_items), idx, q_type))
        print('    Q: %s' % q[:80])
        print('    Gold: %s' % gold[:60])

        # Build scratch DB
        build_scratch_db(item)

        def search_fn(query, limit=k):
            return memsearch.search_hybrid(query, limit=limit, decay=False).get('results', [])

        # --- Baseline: standard single-query retrieval + LLM ---
        memsearch.DB = BENCH_DB; memsearch.CHROMA_PATH = BENCH_CHROMA; memsearch.COLLECTION = BENCH_COLLECTION
        try:
            memsearch.rebuild_vector_index(verbose=False, reuse=True)
            results = search_fn(q, k)
            pred_base = ' | '.join(r.get('content', '')[:400] for r in results[:k])

            s_base = judge_strict(pred_base, gold)
            if s_base is not None:
                stats['baseline']['strict_n'] += 1
                if s_base:
                    stats['baseline']['strict'] += 1
                    print('    BASELINE strict: HIT')
                else:
                    print('    BASELINE strict: miss')
            else:
                stats['baseline']['na'] += 1
                print('    BASELINE strict: N/A')

            # LLM generation for baseline
            if use_llm:
                prompt_base = 'Answer the QUESTION using ONLY the CONTEXT below. Be concise.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:' % (pred_base[:3000], q)
                answer_base = call_llm(prompt_base, max_tokens=200)
                if answer_base:
                    s_gen = judge_strict(answer_base, gold)
                    if s_gen is not None:
                        print('    BASELINE gen strict: %s (ans=%s)' % (s_gen, answer_base[:50]))

            # --- AAR: multi-query + structured extraction ---
            session_summaries = None
            aar_result = aar_pipeline(q, search_fn, session_summaries=session_summaries, k=k)

            pred_aar_raw = ' | '.join(r.get('content', '')[:400] for r in [])
            # For strict eval: check if computed answer or raw evidence matches gold
            # The AAR computed answer is the key output
            s_aar = None
            if aar_result.get('computed_answer') is not None:
                # Strict match computed answer against gold
                s_aar = judge_strict(aar_result['computed_answer'], gold)
                print('    AAR computed: %s (type=%s, detail=%s)' % (
                    aar_result['computed_answer'], aar_result['comp_type'],
                    (aar_result.get('computation_detail') or '')[:60]))
            elif aar_result.get('structured_data') is not None:
                # No computed answer but structured data available
                print('    AAR structured data found but no computed answer')
            else:
                print('    AAR: no aggregation detected (type=None)')

            if s_aar is not None:
                stats['aar']['strict_n'] += 1
                if s_aar:
                    stats['aar']['strict'] += 1
                    print('    AAR strict: HIT')
                else:
                    print('    AAR strict: miss')
            else:
                # Fallback: use standard retrieval evidence for AAR too
                aar_evidence = aar_result.get('enhanced_prompt', '')
                # Check if gold terms are in the enhanced prompt
                if gold.lower() in aar_evidence.lower():
                    stats['aar']['strict_n'] += 1
                    stats['aar']['strict'] += 1
                    print('    AAR strict: HIT (gold in evidence)')
                else:
                    stats['aar']['na'] += 1
                    print('    AAR strict: N/A')

        except Exception as e:
            print('    ERROR: %s: %s' % (type(e).__name__, str(e)[:80]))
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved

        import gc; gc.collect()
        time.sleep(0.3)

    # Report
    print('=' * 72)
    print('AAR Evaluation | %d multi-session questions' % len(ms_items))
    print('=' * 72)
    for mode in ['baseline', 'aar']:
        s = stats[mode]
        sr = s['strict'] / s['strict_n'] if s['strict_n'] else 0
        print('  %-10s strict: %d/%d = %.1f%%  [NA: %d]' % (mode, s['strict'], s['strict_n'], sr*100, s['na']))
    print('=' * 72)

    # Save
    output = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'benchmark': 'LongMemEval multi-session',
        'sample': len(ms_items),
        'baseline': stats['baseline'],
        'aar': stats['aar'],
    }
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aar_eval_result.json')
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)
    print('Saved to %s' % outpath)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=20)
    ap.add_argument('--variant', default='oracle')
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    run(sample=a.sample, variant=a.variant, use_llm=not a.no_llm)


