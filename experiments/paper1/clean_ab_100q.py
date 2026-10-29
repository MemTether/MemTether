#!/usr/bin/env python3
"""
Clean 3-condition controlled A/B on 100 stratified questions.
Conditions: raw_chunks / flat_truncated (baseline) / EAF_ON
All with per-question logging for exact McNemar.
"""
import sys, os, json, time, gc
sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\experiments\paper1')

def run_clean_ab():
    import memsearch
    from bench_longmemeval import judge_strict, generate_answer, judge_llm, build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION, _assert_sandboxed, stratified_sample

    # Load dataset
    data_path = r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle'
    with open(data_path, encoding='utf-8') as f:
        items = json.load(f)

    # Stratified sample 100
    todo = stratified_sample(items, 100, seed=42)
    print(f'Running {len(todo)} questions x 3 conditions')

    # Load session summaries
    summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
    summaries = {}
    if os.path.exists(summaries_path):
        summaries = json.load(open(summaries_path, encoding='utf-8'))

    _saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)

    per_question = []

    for i, item in enumerate(todo, 1):
        qid = item.get('question_id', f'q{i}')
        qtype = item.get('question_type', 'unknown')
        gold = item.get('answer', '')
        question = item.get('question', '')

        entry = {
            'idx': i-1, 'question_id': qid, 'type': qtype,
            'gold': str(gold)[:200],
            'question': question[:200],
            'conditions': {}
        }

        # Build scratch db once per question
        build_scratch_db(item)

        # Switch to bench sandbox
        memsearch.DB = BENCH_DB
        memsearch.CHROMA_PATH = BENCH_CHROMA
        memsearch.COLLECTION = BENCH_COLLECTION

        try:
            _assert_sandboxed('post')

            # Rebuild vector index
            try:
                memsearch.rebuild_vector_index(verbose=False, reuse=True)
            except:
                pass

            # Search
            q_text = item['question']
            if item.get('question_date'):
                q_text += f" (date: {item['question_date']})"

            # Condition 1: RAW CHUNKS - get all turns, no curation
            all_turns = []
            for sess in item.get('haystack_sessions', []):
                for turn in sess:
                    role = turn.get('role', '?')
                    content = (turn.get('content') or '').strip()
                    if content:
                        all_turns.append(f'[{role}] {content}')
            raw_context = ' | '.join(t[:200] for t in all_turns)

            # Condition 2: BASELINE (truncated top-k)
            res_baseline = memsearch.search_hybrid(q_text, limit=12, decay=False)
            baseline_evidence = ' | '.join(
                (r.get('content') or '')[:300] for r in res_baseline.get('results', [])[:12]
            )

            # Condition 3: EAF ON (full evidence + session summaries)
            res_eaf = memsearch.search_hybrid(q_text, limit=12, decay=False)
            sess_ids = item.get('haystack_session_ids', [])
            sess_summaries = []
            for sid in sess_ids:
                if sid in summaries:
                    sess_summaries.append(summaries[sid][:200])
            ssi_text = ''
            if sess_summaries:
                ssi_text = '=== Session Summaries ===\n' + '\n'.join(sess_summaries) + '\n\n'
            eaf_evidence = ssi_text + ' | '.join(
                (r.get('content') or '') for r in res_eaf.get('results', [])[:12]
            )

            # Generate answer for each condition
            conditions = {
                'raw_chunks': raw_context,
                'baseline': baseline_evidence,
                'eaf_on': eaf_evidence
            }

            for cond_name, evidence in conditions.items():
                ans, gen_err = generate_answer(question, evidence, question_type=qtype)
                if ans is None:
                    entry['conditions'][cond_name] = {'strict': None, 'judge': None, 'error': gen_err}
                    continue

                # Judge
                s = judge_strict(ans, gold)
                ok, note = judge_llm(question, gold, ans)

                entry['conditions'][cond_name] = {
                    'strict': s,  # True/False/None (None=NA)
                    'judge': ok,   # True/False/None
                    'answer': ans[:200] if ans else None,
                    'judge_note': note
                }

            per_question.append(entry)

        except Exception as e:
            entry['error'] = f'{type(e).__name__}: {str(e)[:100]}'
            per_question.append(entry)
        finally:
            memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
            gc.collect()
            time.sleep(0.2)

        if i % 10 == 0:
            print(f'  [{i}/{len(todo)}] done')
            # Save checkpoint
            with open(r'E:\Temp\clean_ab_checkpoint.json', 'w', encoding='utf-8') as f:
                json.dump({'ts': datetime.now().isoformat(), 'results': per_question}, f, ensure_ascii=False, indent=1)

    # Final save
    with open(r'E:\Temp\clean_ab_final.json', 'w', encoding='utf-8') as f:
        json.dump({'ts': datetime.now().isoformat(), 'n': len(todo), 'results': per_question}, f, ensure_ascii=False, indent=1)

    # Compute summary
    print('\n=== RESULTS ===')
    for cond in ['raw_chunks', 'baseline', 'eaf_on']:
        strict_hits, strict_total, strict_na = 0, 0, 0
        judge_hits, judge_total, judge_na = 0, 0, 0
        for pq in per_question:
            c = pq.get('conditions', {}).get(cond, {})
            s = c.get('strict')
            j = c.get('judge')
            if s is None:
                strict_na += 1
            else:
                strict_total += 1
                if s: strict_hits += 1
            if j is None:
                judge_na += 1
            else:
                judge_total += 1
                if j: judge_hits += 1

        sr = strict_hits / strict_total * 100 if strict_total else 0
        jr = judge_hits / judge_total * 100 if judge_total else 0
        print(f'  {cond:15s} strict={strict_hits}/{strict_total}={sr:.1f}% (NA={strict_na})  judge={judge_hits}/{judge_total}={jr:.1f}% (NA={judge_na})')

    # Per-type
    print('\n  Per-type (strict):')
    types = sorted(set(pq['type'] for pq in per_question))
    for t in types:
        for cond in ['raw_chunks', 'baseline', 'eaf_on']:
            hits, total, na = 0, 0, 0
            for pq in per_question:
                if pq['type'] != t: continue
                c = pq.get('conditions', {}).get(cond, {})
                s = c.get('strict')
                if s is None: na += 1
                else:
                    total += 1
                    if s: hits += 1
            sr = hits / total * 100 if total else 0
            print(f'    {t:25s} {cond:15s} {hits}/{total}={sr:.1f}% (NA={na})')

    # Exact McNemar for multi-session: baseline vs eaf_on (strict)
    ms_paired = [(pq['conditions'].get('baseline',{}).get('strict'), pq['conditions'].get('eaf_on',{}).get('strict'))
                 for pq in per_question if pq['type']=='multi-session']
    valid_pairs = [(s,j) for s,j in ms_paired if s is not None and j is not None]
    b = sum(1 for s,j in valid_pairs if j and not s)
    c = sum(1 for s,j in valid_pairs if s and not j)
    from math import comb
    n_disc = b + c
    if n_disc > 0:
        k = min(b, c)
        p_val = sum(comb(n_disc, i) for i in range(0, k+1)) / (2**(n_disc-1)) * 2
        p_val = min(p_val, 1.0)
    else:
        p_val = 1.0
    print(f'\n  Exact McNemar (multi-session, baseline vs eaf_on strict):')
    print(f'    valid pairs: {len(valid_pairs)}, b={b}, c={c}, p={p_val:.4f}')

if __name__ == '__main__':
    run_clean_ab()