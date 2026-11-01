import json, io, sys, math, random
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from datetime import datetime
random.seed(42)

bench_path = r'E:\RUANJIAN\workdocs\memtether\cac-artifact\memtether_supersession_benchmark.jsonl'
with open(bench_path, encoding='utf-8') as f:
    records = [json.loads(l.strip()) for l in f if l.strip()]

# Sort by time
records.sort(key=lambda r: r.get('recorded_at', ''))

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
n = len(X); d = len(X[0])

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

def train_probs(X_tr, y_tr, X_te):
    d = len(X_tr[0]); n_tr = len(X_tr)
    w = [0.0]*d; b = 0.0
    pos_count = sum(y_tr)
    w_pos = n_tr/(2*pos_count) if pos_count>0 else 1
    w_neg = n_tr/(2*(n_tr-pos_count)) if n_tr>pos_count else 1
    for _ in range(300):
        grad_w = [0.0]*d; grad_b = 0.0
        for i in range(n_tr):
            z = sum(X_tr[i][j]*w[j] for j in range(d)) + b
            p = sigmoid(z)
            weight = w_pos if y_tr[i]==1 else w_neg
            err = weight*(p-y_tr[i])
            for j in range(d): grad_w[j] += err*X_tr[i][j]
            grad_b += err
        for j in range(d): w[j] -= 0.01*(grad_w[j]/n_tr + 0.01*w[j])
        b -= 0.01*grad_b/n_tr
    return [sigmoid(sum(x[j]*w[j] for j in range(d))+b) for x in X_te]

# Try multiple temporal splits to find one with enough positives
print("=== TEMPORAL SPLIT: SEARCH FOR ADEQUATE SAMPLE SIZE ===\n")

results = []
for split_pct in [0.3, 0.4, 0.5, 0.6]:
    split = int(n * split_pct)
    X_tr, y_tr = X[:split], y[:split]
    X_te, y_te = X[split:], y[split:]
    test_pos = sum(y_te)
    
    print(f"Split {split_pct:.0%}: train={split}, test={n-split}, test_positives={test_pos}")
    
    if test_pos < 30:
        print(f"  → Too few positives, skipping\n")
        continue
    
    probs = train_probs(X_tr, y_tr, X_te)
    
    # Use threshold 0.66 (from nested CV on training data)
    y_pred = [1 if p >= 0.66 else 0 for p in probs]
    
    tp = sum(1 for t,p in zip(y_te, y_pred) if t==1 and p==1)
    fp = sum(1 for t,p in zip(y_te, y_pred) if t==0 and p==1)
    fn = sum(1 for t,p in zip(y_te, y_pred) if t==1 and p==0)
    tn = sum(1 for t,p in zip(y_te, y_pred) if t==0 and p==0)
    
    f1 = f1_macro(y_te, y_pred)
    precision = tp/(tp+fp) if tp+fp>0 else 0
    recall = tp/(tp+fn) if tp+fn>0 else 0
    
    # Bootstrap CI
    f1_boot = []
    for _ in range(100):
        boot_idx = [random.randint(0, len(y_te)-1) for _ in range(len(y_te))]
        y_te_boot = [y_te[i] for i in boot_idx]
        y_pred_boot = [y_pred[i] for i in boot_idx]
        f1_boot.append(f1_macro(y_te_boot, y_pred_boot))
    f1_boot.sort()
    ci_low = f1_boot[int(0.025*100)]
    ci_high = f1_boot[int(0.975*100)]
    
    print(f"  F1 = {f1:.3f} [95% CI: {ci_low:.3f}, {ci_high:.3f}]")
    print(f"  Precision = {precision:.3f}, Recall = {recall:.3f}")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")
    
    results.append({
        'split_pct': split_pct, 'test_pos': test_pos,
        'f1': round(f1,3), 'ci': [round(ci_low,3), round(ci_high,3)],
        'precision': round(precision,3), 'recall': round(recall,3)
    })
    print()

print("=== COMPARISON: RANDOM CV vs TEMPORAL ===")
print(f"Random CV F1 = 0.692 (from 5-fold CV)")
for r in results:
    print(f"Temporal split {r['split_pct']:.0%}: F1 = {r['f1']}")

# Save
with open(r'E:\Temp\temporal_validation_proper.json', 'w') as f:
    json.dump({
        'method': 'Temporal split with bootstrap CI, multiple split points',
        'random_cv_f1': 0.692,
        'temporal_results': results,
        'note': 'Temporal split with adequate sample size (>=30 positives in test set)'
    }, f, indent=2)
