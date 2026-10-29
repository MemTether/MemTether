import json, sys, os, time, gc
sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\experiments\paper1')

from bench_longmemeval import build_scratch_db, BENCH_DB, BENCH_CHROMA, BENCH_COLLECTION
import memsearch
from llm_helper import call_llm
from token_f1 import f1_score, exact_match_score

data = json.load(open(r'E:\RUANJIAN\memory_hub\bench_data\longmemeval_oracle', encoding='utf-8'))
summaries_path = r'E:\RUANJIAN\memory_hub\bench_data\session_summaries.json'
summaries = json.load(open(summaries_path, encoding='utf-8')) if os.path.exists(summaries_path) else {}

_saved = (memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION)
ckpt_path = r'E:\Temp\f1_500q_v2_checkpoint.json'
out_path = r'E:\Temp\f1_500q_v2_result.json'

# Resume from checkpoint
results = []
done_set = set()
if os.path.exists(ckpt_path):
    try:
        ck = json.load(open(ckpt_path, encoding='utf-8'))
        for r in ck.get('results', []):
            results.append(r)
            done_set.add(r['idx'])
        print('Resuming: %d done' % len(done_set))
    except:
        pass

from collections import defaultdict
per_type_f1 = defaultdict(list)
per_type_em = defaultdict(list)

for i, item in enumerate(data):
    if i in done_set:
        continue
    
    q = item['question']
    gold = str(item['answer']).strip()
    q_type = item['question_type']
    
    try:
        build_scratch_db(item)
        memsearch.DB = BENCH_DB
        memsearch.CHROMA_PATH = BENCH_CHROMA
        memsearch.COLLECTION = BENCH_COLLECTION
        try:
            memsearch.rebuild_vector_index(verbose=False, reuse=True)
        except:
            pass
        
        search_results = memsearch.search_hybrid(q, limit=12, decay=False)
        pred = '\n'.join((x.get('content') or '') for x in search_results.get('results', [])[:12])
        
        item_summaries = summaries.get(str(i), summaries.get(i, None))
        if item_summaries and isinstance(item_summaries, list):
            summary_text = '\n'.join(['[Session %d]: %s' % (j+1, s) for j, s in enumerate(item_summaries[:5])])
            pred = '[SESSION SUMMARIES]\n%s\n\n[EVIDENCE]\n%s' % (summary_text, pred)
        
        prompt = 'Answer the QUESTION using ONLY the CONTEXT below. Be concise - give the direct answer.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:' % (pred[:4000], q)
        answer = call_llm(prompt, max_tokens=2000, temperature=0.1)
        
        if answer:
            f1 = f1_score(answer, gold)
            em = exact_match_score(answer, gold)
            per_type_f1[q_type].append(f1)
            per_type_em[q_type].append(1 if em else 0)
            
            results.append({
                'idx': i, 'type': q_type, 'f1': round(f1, 4), 'em': em,
                'answer': answer[:200], 'gold': gold[:100], 'status': 'done'
            })
        else:
            results.append({'idx': i, 'type': q_type, 'status': 'gen_failed'})
        
    except Exception as e:
        results.append({'idx': i, 'type': q_type, 'status': 'error', 'error': str(e)[:100]})
    finally:
        memsearch.DB, memsearch.CHROMA_PATH, memsearch.COLLECTION = _saved
    
    if (i+1) % 50 == 0:
        # Save checkpoint
        with open(ckpt_path, 'w', encoding='utf-8') as f:
            json.dump({'results': results}, f, ensure_ascii=False)
        
        # Progress report
        all_f1 = [x for lst in per_type_f1.values() for x in lst]
        mean_f1 = sum(all_f1) / len(all_f1) if all_f1 else 0
        print('[%d/500] mean_f1=%.4f' % (i+1, mean_f1))
    
    gc.collect()
    time.sleep(0.2)

# Final save
with open(ckpt_path, 'w', encoding='utf-8') as f:
    json.dump({'results': results}, f, ensure_ascii=False)

# Compute per-type summary
summary = {}
for q_type in sorted(per_type_f1.keys()):
    f1s = per_type_f1[q_type]
    ems = per_type_em[q_type]
    summary[q_type] = {
        'mean_f1': round(sum(f1s)/len(f1s), 4) if f1s else 0,
        'mean_em': round(sum(ems)/len(ems), 4) if ems else 0,
        'n': len(f1s)
    }

all_f1 = [x for lst in per_type_f1.values() for x in lst]
all_em = [x for lst in per_type_em.values() for x in lst]
summary['overall'] = {
    'mean_f1': round(sum(all_f1)/len(all_f1), 4) if all_f1 else 0,
    'mean_em': round(sum(all_em)/len(all_em), 4) if all_em else 0,
    'n': len(all_f1)
}

output = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'per_type': summary}
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print('\nF1 500Q COMPLETE')
for t, s in summary.items():
    print('  %s: F1=%.4f EM=%.4f (n=%d)' % (t, s['mean_f1'], s['mean_em'], s['n']))
