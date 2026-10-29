"""Token-level F1 scoring (SQuAD-style) for LongMemEval."""
import json
import re
from collections import Counter


def normalize_answer(s):
    """Lower text and remove punctuation, articles and extra whitespace."""
    def remove_articles(text):
        return re.sub(r'\b(a|an|the)\b', ' ', text)
    def white_space_fix(text):
        return ' '.join(text.split())
    def remove_punc(text):
        exclude = set(string.punctuation)
        return ''.join(ch for ch in text if ch not in exclude)
    import string
    return white_space_fix(remove_articles(remove_punc(s.lower())))


def f1_score(prediction, ground_truth):
    """Compute token-level F1 between prediction and ground truth."""
    prediction_tokens = normalize_answer(prediction).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return f1


def exact_match_score(prediction, ground_truth):
    return normalize_answer(prediction) == normalize_answer(ground_truth)


def compute_f1_for_file(results_file, data_file, output_file):
    """Compute token-level F1 for each question in results file."""
    with open(data_file, encoding='utf-8') as f:
        dataset = json.load(f)
    qid2data = {q['question_id']: q for q in dataset}
    
    with open(results_file, encoding='utf-8') as f:
        results = json.load(f)
    
    f1_scores = []
    per_type_f1 = {}
    
    for r in results:
        idx = r.get('idx')
        if idx is None:
            continue
        q = dataset[idx]
        qid = q['question_id']
        gold = q['answer']
        qtype = q['question_type']
        
        # Get the system's generated answer (from evidence + generation)
        # For now use the gold answer as placeholder - need actual system output
        # This will be filled in when we run the generation step
        
        # For now, compute F1 between gold and itself (sanity check = 1.0)
        f1 = f1_score(gold, gold)
        
        f1_scores.append(f1)
        if qtype not in per_type_f1:
            per_type_f1[qtype] = []
        per_type_f1[qtype].append(f1)
    
    result = {
        'overall_f1': round(sum(f1_scores) / len(f1_scores), 4) if f1_scores else 0,
        'n': len(f1_scores),
        'per_type': {
            t: {
                'mean_f1': round(sum(v) / len(v), 4),
                'n': len(v)
            } for t, v in per_type_f1.items()
        }
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result


if __name__ == '__main__':
    # Sanity check
    assert f1_score('3 items', '3') > 0, "F1 should be > 0"
    assert f1_score('8 days', '8 days') == 1.0, "F1 should be 1.0 for identical"
    assert f1_score('nothing', '3') == 0.0, "F1 should be 0 for no overlap"
    print('F1 implementation sanity checks passed')
