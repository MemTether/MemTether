#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full 500Q EAF Ablation: Baseline vs ECO vs ECO+SSI on all 500 questions.
Uses the same hybrid retrieval pipeline but with EAF enhancements applied.
"""
import sys, os, json, time, gc
from collections import defaultdict

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run(use_llm=True, k=12, batch_save=50):
    import memsearch
    from bench_longmemeval import judge_strict, build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION
    from llm_helper import call_llm

    data = json.load(open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8'))

    # Load session summaries
    summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
    summaries = {}
    if os.path.exists(summaries_path):
        summaries = json.load(open(summaries_path, encoding='utf-8'))

    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)

    per_type = defaultdict(lambda: {'n': 0, 'strict': 0, 'strict_n': 0, 'strict_na': 0, 'llm': 0, 'llm_n': 0})
    strict_hits, strict_total, strict_na = 0, 0, 0
    llm_hits, llm_total, llm_skipped = 0, 0, 0
    errs = []
    ckpt_path = r'E:\Temp\eaf_500q_f1_checkpoint.json'

    # Resume from checkpoint
    done = {}
    if os.path.exists(ckpt_path):
        try:
            ck = json.load(open(ckpt_path, encoding='utf-8'))
            for r in ck.get('results', []):
                done[r['idx']] = r
            print('Resuming from checkpoint: %d done' % len(done))
        except:
            pass

    results = list(done.values())
    for i, item in enumerate(data):
        if i in done:
            continue
        q = item['question']
        gold = str(item['answer']).strip()
        q_type = item['question_type']

        r = {'idx': i, 'type': q_type}
        try:
            # Build scratch DB with all sessions
            build_scratch_db(item)

            memsearch.DB = BENCH_DB
            memsearch.CHROMA_PATH = BENCH_CHROMA
            memsearch.COLLECTION = BENCH_COLLECTION
            try:
                memsearch.rebuild_vector_index(verbose=False, reuse=True)
            except:
                pass

            # EAF: full evidence (no truncation) + session summaries
            results_search = memsearch.search_hybrid(q, limit=k, decay=False)
            # ECO: no 300-char truncation - use full content
            pred = '\n'.join((x.get('content') or '') for x in results_search.get('results', [])[:k])

            # SSI: inject session summaries
            item_summaries = summaries.get(str(i), summaries.get(i, None))
            if item_summaries and isinstance(item_summaries, list):
                summary_text = '\n'.join(['[Session %d]: %s' % (j+1, s) for j, s in enumerate(item_summaries[:5])])
                pred = '[SESSION SUMMARIES]\n%s\n\n[EVIDENCE]\n%s' % (summary_text, pred)

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

            # LLM generation + judge
            if use_llm:
                prompt = 'Answer the QUESTION using ONLY the CONTEXT below. Be concise - give the direct answer.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:' % (pred[:4000], q)
                answer = call_llm(prompt, max_tokens=2000)
                if answer:
                    # Judge
                    judge_prompt = 'Question: %s\nExpected answer: %s\nGiven answer: %s\n\nIs the given answer semantically correct? Reply only YES or NO.' % (q, gold, answer[:200])
                    judge_result = call_llm(judge_prompt, max_tokens=10)
                    if judge_result:
                        ok = 'YES' in judge_result.upper()
                        llm_total += 1
                        per_type[q_type]['llm_n'] += 1
                        if ok:
                            llm_hits += 1
                            per_type[q_type]['llm'] += 1
                            r['judge'] = 1
                        else:
                            r['judge'] = 0
                        r['judge_n'] = 1
                        r['answer'] = answer[:100]
                    else:
                        llm_skipped += 1
                else:
                    llm_skipped += 1

            r['status'] = 'done'
            results.append(r)
            done[i] = r

        except Exception as e:
            errs.append('idx=%d: %s: %s' % (i, type(e).__name__, str(e)[:70]))
            r['status'] = 'error'
            r['error'] = str(e)[:100]
            results.append(r)
            done[i] = r
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved

        # Progress
        if (i+1) % 10 == 0:
            sr = strict_hits / strict_total if strict_total else 0
            lr = llm_hits / llm_total if llm_total else 0
            print('  [%d/500] strict=%d/%d (%.1f%%) llm=%d/%d (%.1f%%)' % (
                i+1, strict_hits, strict_total, sr*100, llm_hits, llm_total, lr*100))

        # Save checkpoint
        if (i+1) % batch_save == 0:
            with open(ckpt_path, 'w', encoding='utf-8') as f:
                json.dump({'results': results}, f, ensure_ascii=False)

        gc.collect()
        time.sleep(0.2)

    # Final save
    with open(ckpt_path, 'w', encoding='utf-8') as f:
        json.dump({'results': results}, f, ensure_ascii=False)

    sr = strict_hits / strict_total if strict_total else 0
    lr = llm_hits / llm_total if llm_total else 0

    output = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'system': 'MemTether EAF (ECO+SSI)',
        'benchmark': 'LongMemEval oracle 500Q',
        'strict': round(sr, 4),
        'strict_hits': strict_hits,
        'strict_n': strict_total,
        'strict_na': strict_na,
        'llm': round(lr, 4) if llm_total else 0,
        'llm_hits': llm_hits,
        'llm_n': llm_total,
        'llm_skipped': llm_skipped,
        'per_type': dict(per_type),
        'errors': errs[:20],
    }
    outpath = r'E:\Temp\eaf_500q_f1_result.json'
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('\n' + '=' * 72)
    print('EAF 500Q COMPLETE')
    print('  strict: %d/%d = %.1f%%  [NA: %d]' % (strict_hits, strict_total, sr*100, strict_na))
    print('  judge:  %d/%d = %.1f%%' % (llm_hits, llm_total, lr*100))
    print('Saved to %s' % outpath)

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    run(use_llm=not a.no_llm)
