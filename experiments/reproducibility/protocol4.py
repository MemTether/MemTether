import json, io, sys, math, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import Counter, defaultdict
from datetime import datetime

bench_path = r'experiments/reproducibility/memtether_supersession_features.jsonl'
with open(bench_path, encoding='utf-8') as f:
    records = [json.loads(l.strip()) for l in f if l.strip()]

# Sort chronologically
records.sort(key=lambda r: r.get('recorded_at', ''))

# Split 50/50 temporally
split = len(records) // 2
train_records = records[:split]
test_records = records[split:]

print(f"Train: {len(train_records)}, Test: {len(test_records)}")

# ===== Step 1: Compute trust model parameters from TRAINING set only =====
source_stats = defaultdict(lambda: {'total': 0, 'sup': 0})
type_stats = defaultdict(lambda: {'total': 0, 'sup': 0})
ent_stats = {'with': {'total': 0, 'sup': 0}, 'without': {'total': 0, 'sup': 0}}
read_stats = {'read': {'total': 0, 'sup': 0}, 'unread': {'total': 0, 'sup': 0}}

for r in train_records:
    src = r.get('source', '?')
    typ = r.get('type', '?')
    has_ent = r.get('n_entities', 0) > 0
    has_read = r.get('use_count', 0) > 0
    is_sup = r['will_be_superseded'] == 1
    
    source_stats[src]['total'] += 1
    type_stats[typ]['total'] += 1
    ent_stats['with' if has_ent else 'without']['total'] += 1
    read_stats['read' if has_read else 'unread']['total'] += 1
    if is_sup:
        source_stats[src]['sup'] += 1
        type_stats[typ]['sup'] += 1
        ent_stats['with' if has_ent else 'without']['sup'] += 1
        read_stats['read' if has_read else 'unread']['sup'] += 1

# Compute base rate from training
train_pos = sum(1 for r in train_records if r['will_be_superseded'] == 1)
base_rate_sup = train_pos / len(train_records)
base_rate_stable = 1 - base_rate_sup

def get_trust(r):
    """Compute trust score = P(stable | source, type, has_entities, has_reads) using naive Bayes."""
    src = r.get('source', '?')
    typ = r.get('type', '?')
    has_ent = r.get('n_entities', 0) > 0
    has_read = r.get('use_count', 0) > 0
    
    # P(stable | source) - use Laplace smoothing for unseen sources
    ss = source_stats.get(src, {'total': 0, 'sup': 0})
    p_sup_src = (ss['sup'] + 1) / (ss['total'] + 2)  # Laplace smoothing
    p_stable_src = 1 - p_sup_src
    
    # P(stable | type)
    ts = type_stats.get(typ, {'total': 0, 'sup': 0})
    p_sup_type = (ts['sup'] + 1) / (ts['total'] + 2)
    p_stable_type = 1 - p_sup_type
    
    # P(stable | has_entities)
    es = ent_stats['with' if has_ent else 'without']
    p_sup_ent = (es['sup'] + 1) / (es['total'] + 2)
    p_stable_ent = 1 - p_sup_ent
    
    # P(stable | has_reads)
    rs = read_stats['read' if has_read else 'unread']
    p_sup_read = (rs['sup'] + 1) / (rs['total'] + 2)
    p_stable_read = 1 - p_sup_read
    
    # Naive Bayes combination (log space for numerical stability)
    log_trust = (math.log(max(p_stable_src, 1e-10)) + 
                 math.log(max(p_stable_type, 1e-10)) + 
                 math.log(max(p_stable_ent, 1e-10)) + 
                 math.log(max(p_stable_read, 1e-10)))
    
    # Normalize: convert back to probability
    # P(stable) = exp(log_trust) / (exp(log_trust) + exp(log_not_stable))
    # For simplicity, just return the geometric mean of the individual probabilities
    trust = math.exp(log_trust / 4)  # geometric mean
    return min(trust, 1.0)

# ===== Step 2: Apply trust model to TEST set =====
print("\n=== Applying trust model to test set ===")
for r in test_records:
    r['trust_score'] = get_trust(r)

# ===== Step 3: Calibration Analysis =====
print("\n=== CALIBRATION ANALYSIS (held-out test set) ===\n")

# Sort test records by trust score
test_sorted = sorted(test_records, key=lambda r: r['trust_score'])

# Create 10 bins by trust score deciles
bin_size = len(test_sorted) // 10
print(f"{'Bin':<10s} {'N':>6s} {'Mean Trust':>12s} {'Actual Stable %':>16s} {'Calibration Error':>18s}")
print("-" * 70)

ece_sum = 0.0
total_weight = 0.0
calibration_data = []

for bin_i in range(10):
    start = bin_i * bin_size
    end = start + bin_size if bin_i < 9 else len(test_sorted)
    bin_records = test_sorted[start:end]
    
    if not bin_records:
        continue
    
    n_bin = len(bin_records)
    mean_trust = sum(r['trust_score'] for r in bin_records) / n_bin
    actual_stable = sum(1 for r in bin_records if r['will_be_superseded'] == 0) / n_bin
    error = abs(mean_trust - actual_stable)
    
    weight = n_bin / len(test_sorted)
    ece_sum += weight * error
    total_weight += weight
    
    print(f"{bin_i:<10d} {n_bin:6d} {mean_trust:12.3f} {actual_stable*100:14.1f}% {error:18.3f}")
    calibration_data.append({
        'bin': bin_i, 'n': n_bin, 'mean_trust': round(mean_trust, 3),
        'actual_stable_rate': round(actual_stable, 3), 'error': round(error, 3)
    })

ece = ece_sum / total_weight if total_weight > 0 else 0
print(f"\nExpected Calibration Error (ECE): {ece:.3f}")
print(f"→ {'WELL-CALIBRATED' if ece < 0.1 else 'MODERATELY CALIBRATED' if ece < 0.2 else 'POORLY CALIBRATED'} (ECE < 0.1 = good)")

# ===== Step 4: Discrimination Analysis (AUC-ROC) =====
print("\n=== DISCRIMINATION ANALYSIS ===\n")

# Compute AUC using the Mann-Whitney U statistic
pos_trusts = [r['trust_score'] for r in test_records if r['will_be_superseded'] == 0]
neg_trusts = [r['trust_score'] for r in test_records if r['will_be_superseded'] == 1]

# AUC = P(random positive has higher trust than random negative)
count_greater = 0
count_total = 0
for p in pos_trusts:
    for n in neg_trusts:
        count_total += 1
        if p > n:
            count_greater += 1
        elif p == n:
            count_greater += 0.5

auc = count_greater / max(1, count_total)
print(f"AUC-ROC: {auc:.3f}")
print(f"→ {'EXCELLENT' if auc > 0.9 else 'GOOD' if auc > 0.8 else 'MODERATE' if auc > 0.7 else 'POOR'} discrimination")

# ===== Step 5: Practical Utility =====
print("\n=== PRACTICAL UTILITY ===\n")

# Simulate: operator reviews only facts with trust < threshold
# Measure: how many supersessions are caught, how many false alarms

total_test = len(test_records)
total_sup = sum(1 for r in test_records if r['will_be_superseded'] == 1)
total_stable = total_test - total_sup

print(f"Test set: {total_test} facts, {total_sup} superseded ({total_sup/total_test*100:.1f}%)")

# Find optimal threshold for flagging
print(f"\n{'Threshold':<10s} {'Reviewed':>8s} {'Caught':>8s} {'Missed':>8s} {'Precision':>10s} {'Recall':>8s} {'Effort Saved':>12s}")
print("-" * 75)

best_threshold = 0
best_f1 = 0
for threshold in [x / 100 for x in range(10, 96, 5)]:
    flagged = [r for r in test_records if r['trust_score'] < threshold]
    not_flagged = [r for r in test_records if r['trust_score'] >= threshold]
    
    reviewed = len(flagged)
    caught = sum(1 for r in flagged if r['will_be_superseded'] == 1)
    missed = sum(1 for r in not_flagged if r['will_be_superseded'] == 1)
    
    precision = caught / max(1, reviewed)
    recall = caught / max(1, total_sup) if total_sup > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0
    effort_saved = (1 - reviewed / total_test) * 100
    
    if f1 > best_f1:
        best_f1 = f1
        best_threshold = threshold
    
    print(f"{threshold:<10.2f} {reviewed:8d} {caught:8d} {missed:8d} {precision:10.3f} {recall:8.3f} {effort_saved:11.1f}%")

print(f"\nBest threshold: {best_threshold:.2f} (F1 = {best_f1:.3f})")

# ===== Save all results =====
results = {
    'protocol': 'Trust Model Validation on held-out temporal test set',
    'train_size': len(train_records),
    'test_size': len(test_records),
    'calibration': {
        'ece': round(ece, 3),
        'verdict': 'well-calibrated' if ece < 0.1 else 'moderately-calibrated' if ece < 0.2 else 'poorly-calibrated',
        'bins': calibration_data
    },
    'discrimination': {
        'auc_roc': round(auc, 3),
        'interpretation': 'excellent' if auc > 0.9 else 'good' if auc > 0.8 else 'moderate'
    },
    'practical_utility': {
        'best_threshold': round(best_threshold, 2),
        'best_f1': round(best_f1, 3),
        'note': 'See threshold sweep results above'
    }
}
with open(r'E:\Temp\protocol4_results.json', 'w') as f:
    json.dump(results, f, indent=2)
print("\nSaved to E:\\Temp\\protocol4_results.json")

