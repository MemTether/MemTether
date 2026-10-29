#!/usr/bin/env python3
"""
EAF ON on all 500 questions with per-question logging.
Same pipeline as bench_500q_pq.py but with EAF (ECO+SSI) enabled.
"""
import sys, os, json, time, gc
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\experiments\paper1')

def main():
    import memsearch
    from bench_longmemeval import (judge_strict, generate_answer, judge_llm,
        build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION,
        _assert_sandboxed)

    with open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8') as f:
        items = json.load(f)
    
    # Load session summaries for SSI
    summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
    summaries = {}
    if os.path.exists(summaries_path):
        summaries = json.load(open(summaries_path, encoding='utf-8'))
    
    print(f'Total: {len(items)}')
    
    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    
    ckpt_path = r'E:\Temp\eaf_500q_pq_checkpoint.json'
    done = {}
    if os.path.exists(ckpt_path):
        ck = json.load(open(ckpt_path, encoding='utf-8'))
        done = {r['idx']: r for r in ck.get('results', [])}
        print(f'Resuming from {len(done)}')
    
    per_question = []
    start_time = time.time()
    
    for i, item in enumerate(items, 1):
        idx = i - 1
        qid = item.get('question_id', f'q{idx}')
        qtype = item.get('question_type', 'unknown')
        gold = str(item.get('answer', ''))
        question = item.get('question', '')
        
        if idx in done:
            per_question.append(done[idx])
            continue
        
        entry = {'idx': idx, 'question_id': qid, 'type': qtype, 'gold': gold[:150]}
        
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
            
            # Retrieve with full evidence (ECO - no truncation)
            q_text = question
            if item.get('question_date'):
                q_text += f" (date: {item['question_date']})"
            
            res = memsearch.search_hybrid(q_text, limit=12, decay=False)
            results = res.get('results', [])
            
            # EAF: full evidence + session summaries (SSI)
            sess_ids = item.get('haystack_session_ids', [])
            sess_summaries = [summaries.get(sid, '')[:200] for sid in sess_ids if sid in summaries]
            ssi_text = '=== Session Summaries ===\n' + '\n'.join(sess_summaries) + '\n\n' if sess_summaries else ''
            
            # Full evidence (no 300char truncation - this is the ECO part)
            evidence = ssi_text + ' | '.join((r.get('content') or '') for r in results[:12])
            
            # Generate + score
            ans, gen_err = generate_answer(question, evidence, question_type=qtype)
            
            if ans:
                s = judge_strict(ans, gold)
                ok, jn = judge_llm(question, gold, ans)
                entry['strict_answer'] = s
                entry['judge'] = ok
                entry['answer'] = ans[:150]
            else:
                entry['strict_answer'] = None
                entry['judge'] = None
                entry['error'] = gen_err
            
            per_question.append(entry)
            
        except Exception as e:
            entry['strict_answer'] = None
            entry['judge'] = None
            entry['error'] = f'{type(e).__name__}: {str(e)[:80]}'
            per_question.append(entry)
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
            gc.collect()
            time.sleep(0.1)
        
        if i % 25 == 0:
            elapsed = time.time() - start_time
            print(f'  [{i}/500] {elapsed/60:.0f}min')
            with open(ckpt_path, 'w', encoding='utf-8') as f:
                json.dump({'ts': datetime.now().isoformat(), 'results': per_question}, f, ensure_ascii=False)
    
    # Final save
    with open(r'E:\Temp\eaf_500q_pq_final.json', 'w', encoding='utf-8') as f:
        json.dump({'ts': datetime.now().isoformat(), 'n': 500, 'results': per_question}, f, ensure_ascii=False, indent=1)
    
    # Now compute paired McNemar: baseline vs EAF ON
    print('\n=== PAIRED McNEMAR: BASELINE vs EAF ON (500Q) ===')
    
    # Load baseline per-question data
    with open(r'E:\Temp\bench_500q_pq_final.json', 'r', encoding='utf-8') as f:
        baseline_data = json.load(f)
    baseline = {r['idx']: r for r in baseline_data['results']}
    
    types = sorted(set(r['type'] for r in per_question))
    
    for t in types:
        t_eaf = [r for r in per_question if r['type'] == t]
        
        b = 0  # EAF correct, baseline wrong
        c = 0  # baseline correct, EAF wrong
        both_c = 0
        both_w = 0
        
        for r in t_eaf:
            idx = r['idx']
            eaf_s = r.get('strict_answer')
            base_r = baseline.get(idx, {})
            base_s = base_r.get('strict_answer')
            
            if eaf_s is None or base_s is None:
                continue
            
            if eaf_s and base_s: both_c += 1
            elif not eaf_s and not base_s: both_w += 1
            elif eaf_s and not base_s: b += 1
            elif base_s and not eaf_s: c += 1
        
        n_valid = both_c + both_w + b + c
        eaf_pct = (both_c + b) / n_valid * 100 if n_valid else 0
        base_pct = (both_c + c) / n_valid * 100 if n_valid else 0
        diff = eaf_pct - base_pct
        
        n_disc = b + c
        if n_disc > 0:
            from math import comb
            k = min(b, c)
            p_val = sum(comb(n_disc, i) for i in range(0, k+1)) / (2**(n_disc-1)) * 2
            p_val = min(p_val, 1.0)
        else:
            p_val = 1.0
        
        sig = 'YES' if p_val < 0.05 else 'No'
        print(f'  {t:28s} baseline={base_pct:5.1f}% EAF={eaf_pct:5.1f}% diff={diff:+5.1f}pp | b={b} c={c} p={p_val:.4f} {sig} n={n_valid}')
    
    # Overall
    b_all = sum(1 for r in per_question 
                if r.get('strict_answer') == True 
                and baseline.get(r['idx'], {}).get('strict_answer') == False)
    c_all = sum(1 for r in per_question 
                if r.get('strict_answer') == False 
                and baseline.get(r['idx'], {}).get('strict_answer') == True)
    bc_all = sum(1 for r in per_question 
                 if r.get('strict_answer') == True 
                 and baseline.get(r['idx'], {}).get('strict_answer') == True)
    bw_all = sum(1 for r in per_question 
                 if r.get('strict_answer') == False 
                 and baseline.get(r['idx'], {}).get('strict_answer') == False)
    n_all = bc_all + bw_all + b_all + c_all
    
    eaf_all = (bc_all + b_all) / n_all * 100 if n_all else 0
    base_all = (bc_all + c_all) / n_all * 100 if n_all else 0
    
    n_disc = b_all + c_all
    if n_disc > 0:
        from math import comb
        k = min(b_all, c_all)
        p_all = sum(comb(n_disc, i) for i in range(0, k+1)) / (2**(n_disc-1)) * 2
        p_all = min(p_all, 1.0)
    else:
        p_all = 1.0
    
    print(f'\n  OVERALL: baseline={base_all:.1f}% EAF={eaf_all:.1f}% diff={eaf_all-base_all:+.1f}pp | b={b_all} c={c_all} p={p_all:.2e} n={n_all}')
    
    # Save summary
    summary = {
        'ts': datetime.now().isoformat(),
        'comparison': 'baseline vs EAF ON, 500Q paired',
        'overall': {'b': b_all, 'c': c_all, 'p': p_all, 'n': n_all,
                    'baseline_pct': round(base_all, 1), 'eaf_pct': round(eaf_all, 1),
                    'diff': round(eaf_all - base_all, 1)},
    }
    with open(r'E:\Temp\eaf_vs_baseline_mcnemar.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)

if __name__ == '__main__':
    main()
