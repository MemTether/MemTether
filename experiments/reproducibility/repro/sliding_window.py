import json, io, sys, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from datetime import datetime
from collections import Counter

random_seed = 42

bench_path = r'E:\RUANJIAN\workdocs\memtether\cac-artifact\memtether_supersession_benchmark.jsonl'
with open(bench_path, encoding='utf-8') as f:
    records = [json.loads(l.strip()) for l in f if l.strip()]

# Sort chronologically
records.sort(key=lambda r: r.get('recorded_at', ''))
n = len(records)

def engineer(r):
    recorded = datetime.fromisoformat(r['recorded_at']) if r.get('recorded_at') else datetime(2026,1,1)
    return [
        r.get('confidence',0.5), min(r.get('content_length',0)/500,1.0),
        min(r.get('n_entities',0)/10,1.0), r.get('q_value',0.5),
        min(r.get('use_count',0)/100,1.0), recorded.hour/24.0,
        recorded.weekday()/7.0, min((datetime(2026,10,1)-recorded).days/30.0,1.0),
        1.0 if r.get('type')=='fact' else 0.0,
        1.0 if r.get('type')=='decision' else 0.0,
        1.0 if r.get('scope')=='shared' else 0.0,
    ]

X = [engineer(r) for r in records]
y = [r['will_be_superseded'] for r in records]
d = len(X[0])

# Standardize globally
for j in range(d):
    col = [x[j] for x in X]
    mean = sum(col)/n; std = math.sqrt(sum((v-mean)**2 for v in col)/n)+1e-8
    for i in range(n): X[i][j] = (X[i][j]-mean)/std

def sigmoid(z):
    if z > 500: return 1.0
    if z < -500: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def f1_macro(y_true, y_pred):
    f1s = []
    for cls in [0,1]:
        tp = sum(1 for t,p in zip(y_true,y_pred) if t==cls and p==cls)
        fp = sum(1 for t,p in zip(y_true,y_pred) if t!=cls and p==cls)
        fn = sum(1 for t,p in zip(y_true,y_pred) if t==cls and p!=cls)
        prec = tp/(tp+fp) if tp+fp>0 else 0
        rec = tp/(tp+fn) if tp+fn>0 else 0
        f1s.append(2*prec*rec/(prec+rec) if prec+rec>0 else 0)
    return sum(f1s)/2

def train_predict(X_tr, y_tr, X_te, threshold=0.5):
    d_tr = len(X_tr[0]); n_tr = len(X_tr)
    w = [0.0]*d_tr; b = 0.0
    pos_count = sum(y_tr)
    w_pos = n_tr/(2*pos_count) if pos_count>0 else 1
    w_neg = n_tr/(2*(n_tr-pos_count)) if n_tr>pos_count else 1
    for _ in range(500):
        grad_w = [0.0]*d_tr; grad_b = 0.0
        for i in range(n_tr):
            z = sum(X_tr[i][j]*w[j] for j in range(d_tr)) + b
            p = sigmoid(z)
            weight = w_pos if y_tr[i]==1 else w_neg
            err = weight*(p-y_tr[i])
            for j in range(d_tr): grad_w[j] += err*X_tr[i][j]
            grad_b += err
        for j in range(d_tr): w[j] -= 0.01*(grad_w[j]/n_tr + 0.01*w[j])
        b -= 0.01*grad_b/n_tr
    return [1.0 if sigmoid(sum(x[j]*w[j] for j in range(d_tr))+b)>=threshold else 0.0 for x in X_te]

# Sliding window temporal CV
# Train on [0, train_end], test on [train_end + gap, train_end + gap + test_size]
# Slide train_end and gap simultaneously

print("=== SLIDING WINDOW TEMPORAL CV ===\n")
print(f"{'Train %':>8s} {'Gap %':>8s} {'Train N':>8s} {'Test N':>8s} {'Test Pos':>8s} {'F1':>8s} {'Precision':>10s} {'Recall':>8s}")
print("-" * 80)

results = []

# Sliding window: vary the training end point
# Test window is always 10% of data, positioned right after a gap
test_size = int(n * 0.10)  # test window = 10% of data

for train_pct in range(20, 81, 5):
    train_end = int(n * train_pct / 100)
    
    # Gap between train and test: 5% of data
    gap = int(n * 0.05)
    
    # Test window starts after the gap
    test_start = train_end + gap
    test_end = test_start + test_size
    
    if test_end > n:
        break
    
    X_tr = X[:train_end]
    y_tr = y[:train_end]
    X_te = X[test_start:test_end]
    y_te = y[test_start:test_end]
    
    test_pos = sum(y_te)
    if test_pos < 5:
        continue
    
    # Train
    preds = train_predict(X_tr, y_tr, X_te)
    f1 = f1_macro(y_te, preds)
    
    # Precision and recall
    tp = sum(1 for t,p in zip(y_te, preds) if t==1 and p==1)
    fp = sum(1 for t,p in zip(y_te, preds) if t==0 and p==1)
    fn = sum(1 for t,p in zip(y_te, preds) if t==1 and p==0)
    prec = tp/(tp+fp) if tp+fp>0 else 0
    rec = tp/(tp+fn) if tp+fn>0 else 0
    
    # Temporal gap in days
    if train_end > 0 and test_start < n:
        t1_str = records[train_end-1].get('recorded_at', '')[:10]
        t2_str = records[min(test_start, n-1)].get('recorded_at', '')[:10]
        try:
            t1 = datetime.fromisoformat(t1_str)
            t2 = datetime.fromisoformat(t2_str)
            gap_days = (t2 - t1).days
        except:
            gap_days = 0
    else:
        gap_days = 0
    
    print(f"{train_pct:8d}% {5:8d}% {train_end:8d} {len(y_te):8d} {test_pos:8d} {f1:8.3f} {prec:10.3f} {rec:8.3f}")
    
    results.append({
        'train_pct': train_pct,
        'gap_pct': 5,
        'gap_days': gap_days,
        'train_n': train_end,
        'test_n': len(y_te),
        'test_positives': test_pos,
        'f1': round(f1, 4),
        'precision': round(prec, 4),
        'recall': round(rec, 4),
    })

# Also compute the reference: random 5-fold CV (no temporal ordering)
indices = list(range(n))
# Use a deterministic shuffle
random_seed = 42
_state = random_seed
def pseudo_random():
    global _state
    _state = (_state * 1103515245 + 12345) % (2**31)
    return _state / (2**31)

# Fisher-Yates shuffle
for i in range(n-1, 0, -1):
    j = int(pseudo_random() * (i+1)) % (i+1)
    indices[i], indices[j] = indices[j], indices[i]

fold_size = n // 5
f1_random_folds = []
for i in range(5):
    test_idx = set(indices[i*fold_size:(i+1)*fold_size])
    train_idx = [j for j in range(n) if j not in test_idx]
    X_tr = [X[j] for j in train_idx]; y_tr = [y[j] for j in train_idx]
    X_te = [X[j] for j in indices[i*fold_size:(i+1)*fold_size]]
    y_te = [y[j] for j in indices[i*fold_size:(i+1)*fold_size]]
    preds = train_predict(X_tr, y_tr, X_te)
    f1_random_folds.append(f1_macro(y_te, preds))

mean_random = sum(f1_random_folds)/5
print(f"\nReference (Random 5-fold CV): F1 = {mean_random:.3f}")

# Summary
print(f"\n=== SUMMARY ===")
print(f"Sliding window shows how F1 degrades as the temporal gap between training and test increases.")
f1_values = [r['f1'] for r in results]
if len(f1_values) > 1:
    # Check trend
    first_half = sum(f1_values[:len(f1_values)//2]) / (len(f1_values)//2)
    second_half = sum(f1_values[len(f1_values)//2:]) / (len(f1_values) - len(f1_values)//2)
    print(f"First half avg: {first_half:.3f}, Second half avg: {second_half:.3f}")
    if second_half < first_half:
        print(f"→ F1 DECREASES with temporal distance (confirms temporal leakage)")
    else:
        print(f"→ F1 does NOT decrease with temporal distance")

# Save results
output = {
    'experiment': 'Sliding window temporal CV',
    'method': 'Train on [0, i%], test on [i%+5%, i%+5%+10%], slide i from 20% to 80%',
    'reference_random_cv_f1': round(mean_random, 4),
    'sliding_window_results': results,
}
with open(r'E:\Temp\sliding_window_results.json', 'w') as f:
    json.dump(output, f, indent=2)
print("Saved")
