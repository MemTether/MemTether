#!/usr/bin/env python3
"""
Full 500Q evaluation with per-question strict + judge logging.
Same pipeline as bench_longmemeval.py but saves per-question results
for exact McNemar cross-tabulation.
"""
import sys, os, json, time, gc
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')

def main():
    import memsearch
    from bench_longmemeval import (judge_strict, generate_answer, judge_llm, 
        build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION, 
        _assert_sandboxed, stratified_sample)

    # Load all questions
    with open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8') as f:
        items = json.load(f)
    
    print(f'Total questions: {len(items)}')
    
    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
    
    ckpt_path = r'E:\Temp\bench_500q_pq_checkpoint.json'
    done = {}
    if os.path.exists(ckpt_path):
        ck = json.load(open(ckpt_path, encoding='utf-8'))
        done = {r['idx']: r for r in ck.get('results', [])}
        print(f'Resuming from {len(done)} completed')
    
    per_question = []
    errors = []
    
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
        
        entry = {
            'idx': idx, 'question_id': qid, 'type': qtype,
            'gold': gold[:150],
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
            
            # Retrieve
            q_text = question
            if item.get('question_date'):
                q_text += f" (date: {item['question_date']})"
            
            res = memsearch.search_hybrid(q_text, limit=12, decay=False)
            results = res.get('results', [])
            evidence = ' | '.join((r.get('content') or '')[:400] for r in results[:12])
            
            # Strict scoring on raw evidence
            s_raw = judge_strict(evidence, gold)
            
            # Generate + score
            ans, gen_err = generate_answer(question, evidence, question_type=qtype)
            
            if ans:
                s_gen = judge_strict(ans, gold)
                ok, judge_note = judge_llm(question, gold, ans)
                entry['strict_evidence'] = s_raw
                entry['strict_answer'] = s_gen
                entry['judge'] = ok
                entry['answer'] = ans[:150]
                entry['judge_note'] = judge_note
            else:
                entry['strict_evidence'] = s_raw
                entry['strict_answer'] = None
                entry['judge'] = None
                entry['error'] = gen_err
            
            per_question.append(entry)
            
        except Exception as e:
            entry['strict_evidence'] = None
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
            rate = i / elapsed * 60
            eta_min = (500 - i) / rate if rate > 0 else 0
            print(f'  [{i}/500] {elapsed/60:.0f}min elapsed, ~{eta_min:.0f}min remaining')
            
            # Save checkpoint
            with open(ckpt_path, 'w', encoding='utf-8') as f:
                json.dump({'ts': datetime.now().isoformat(), 'results': per_question}, f, ensure_ascii=False)
    
    # Final save
    with open(r'E:\Temp\bench_500q_pq_final.json', 'w', encoding='utf-8') as f:
        json.dump({'ts': datetime.now().isoformat(), 'n': len(items), 'results': per_question}, f, ensure_ascii=False, indent=1)
    
    # Compute exact McNemar for all types
    print('\n=== EXACT McNEMAR (per-question strict_answer vs judge) ===')
    types = sorted(set(pq['type'] for pq in per_question))
    
    for t in types:
        t_pqs = [pq for pq in per_question if pq['type'] == t]
        
        # Count using strict_answer (post-generation)
        b = 0  # judge correct, strict wrong
        c = 0  # strict correct, judge wrong
        both_correct = 0
        both_wrong = 0
        strict_na = 0
        judge_na = 0
        
        for pq in t_pqs:
            s = pq.get('strict_answer')
            j = pq.get('judge')
            
            if s is None:
                strict_na += 1
                continue
            if j is None:
                judge_na += 1
                continue
            
            if s and j: both_correct += 1
            elif not s and not j: both_wrong += 1
            elif j and not s: b += 1
            elif s and not j: c += 1
        
        n_valid = both_correct + both_wrong + b + c
        strict_score = (both_correct + c) / n_valid * 100 if n_valid else 0
        judge_score = (both_correct + b) / n_valid * 100 if n_valid else 0
        gap = judge_score - strict_score
        
        n_disc = b + c
        if n_disc > 0:
            from math import comb
            k = min(b, c)
            p_val = sum(comb(n_disc, i) for i in range(0, k+1)) / (2**(n_disc-1)) * 2
            p_val = min(p_val, 1.0)
        else:
            p_val = 1.0
        
        print(f'  {t:28s} strict={strict_score:5.1f}% judge={judge_score:5.1f}% gap={gap:+5.1f}pp | b={b} c={c} p={p_val:.4f} | NA_s={strict_na} NA_j={judge_na}')
    
    # Overall
    total_b = sum(1 for pq in per_question if pq.get('judge') and not pq.get('strict_answer'))
    total_c = sum(1 for pq in per_question if pq.get('strict_answer') and not pq.get('judge'))
    total_bc = sum(1 for pq in per_question if pq.get('judge') and pq.get('strict_answer'))
    total_bw = sum(1 for pq in per_question if not pq.get('judge') and not pq.get('strict_answer'))
    
    n_valid_all = total_bc + total_bw + total_b + total_c
    if total_b + total_c > 0:
        from math import comb
        n_d = total_b + total_c
        k = min(total_b, total_c)
        p_all = sum(comb(n_d, i) for i in range(0, k+1)) / (2**(n_d-1)) * 2
        p_all = min(p_all, 1.0)
    else:
        p_all = 1.0
    
    print(f'\n  OVERALL: strict={(total_bc+total_c)/n_valid_all*100:.1f}% judge={(total_bc+total_b)/n_valid_all*100:.1f}% gap={((total_bc+total_b)-(total_bc+total_c))/n_valid_all*100:+.1f}pp')
    print(f'  b={total_b} c={total_c} p={p_all:.2e} n_valid={n_valid_all}')
    
    # Save summary
    summary = {
        'ts': datetime.now().isoformat(),
        'n': len(items),
        'exact_mcnemar': {
            'overall': {'b': total_b, 'c': total_c, 'p': p_all, 'n_valid': n_valid_all},
            'by_type': {}
        }
    }
    for t in types:
        t_pqs = [pq for pq in per_question if pq['type'] == t]
        b_t = sum(1 for pq in t_pqs if pq.get('judge') and not pq.get('strict_answer'))
        c_t = sum(1 for pq in t_pqs if pq.get('strict_answer') and not pq.get('judge'))
        bc_t = sum(1 for pq in t_pqs if pq.get('judge') and pq.get('strict_answer'))
        bw_t = sum(1 for pq in t_pqs if not pq.get('judge') and not pq.get('strict_answer'))
        n_t = bc_t + bw_t + b_t + c_t
        if b_t + c_t > 0:
            from math import comb
            nd = b_t + c_t
            k = min(b_t, c_t)
            p_t = sum(comb(nd, i) for i in range(0, k+1)) / (2**(nd-1)) * 2
            p_t = min(p_t, 1.0)
        else:
            p_t = 1.0
        summary['exact_mcnemar']['by_type'][t] = {
            'b': b_t, 'c': c_t, 'both_correct': bc_t, 'both_wrong': bw_t,
            'p': p_t, 'n_valid': n_t,
            'strict': (bc_t + c_t) / n_t * 100 if n_t else 0,
            'judge': (bc_t + b_t) / n_t * 100 if n_t else 0,
        }
    
    with open(r'E:\Temp\bench_500q_mcnemar.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    print(f'\nMcNemar summary saved to E:/Temp/bench_500q_mcnemar.json')

if __name__ == '__main__':
    main()