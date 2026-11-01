import json, io, sys, math, random
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from datetime import datetime
random.seed(42)

bench_path = r'E:\RUANJIAN\workdocs\memtether\cac-artifact\memtether_supersession_benchmark.jsonl'
with open(bench_path, encoding='utf-8') as f:
    records = [json.loads(l.strip()) for l in f if l.strip()]

# Sort chronologically
records.sort(key=lambda r: r.get('recorded_at', ''))
n = len(records)

# Split 50/50 temporally
split = n // 2
train = records[:split]
test = records[split:]

test_superseded = sum(1 for r in test if r['will_be_superseded'] == 1)
test_total = len(test)

print(f"Test set: {test_total} facts, {test_superseded} superseded ({test_superseded/test_total*100:.1f}%)")
print()

# ===== BASELINE COMPARISON =====
print("=== GOVERNANCE STRATEGY COMPARISON ===\n")

# Strategy 0: No governance (do nothing)
# Catches 0% of supersessions, operator effort = 0
print("Strategy 0: No governance")
print(f"  Supersessions caught: 0/{test_superseded} = 0%")
print(f"  Facts reviewed: 0/{test_total} = 0%")
print()

# Strategy 1: Review ALL facts (gold standard but expensive)
caught_all = test_superseded
print("Strategy 1: Review ALL facts")
print(f"  Supersessions caught: {caught_all}/{test_superseded} = 100%")
print(f"  Facts reviewed: {test_total}/{test_total} = 100%")
print()

# Strategy 2: Flag all facts older than 3 days
# From survival analysis: 95.6% of supersessions happen within 3 days
# So flagging old facts catches most supersessions
# But we need to know: how many test facts are "old" (>3 days since recording)?
# In temporal split, ALL test facts are "old" relative to training. This doesn't work.
# Instead: flag facts with 0 reads after 24 hours (from our data)
no_read = [r for r in test if r.get('use_count', 0) == 0]
no_read_sup = sum(1 for r in no_read if r['will_be_superseded'] == 1)
no_read_pct = len(no_read)/test_total*100
precision_nr = no_read_sup/max(1,len(no_read))
recall_nr = no_read_sup/max(1,test_superseded)
print(f"Strategy 2: Flag never-read facts")
print(f"  Facts flagged: {len(no_read)}/{test_total} = {no_read_pct:.1f}%")
print(f"  Supersessions caught: {no_read_sup}/{test_superseded} = {recall_nr*100:.1f}%")
print(f"  Precision: {precision_nr:.3f}")
print(f"  Effort saved: {100-no_read_pct:.1f}%")
print()

# Strategy 3: Flag all procedure-type facts (from our taxonomy)
procedures = [r for r in test if r.get('type') == 'procedure']
proc_sup = sum(1 for r in procedures if r['will_be_superseded'] == 1)
proc_pct = len(procedures)/test_total*100
precision_proc = proc_sup/max(1,len(procedures))
recall_proc = proc_sup/max(1,test_superseded)
print(f"Strategy 3: Flag all procedure-type facts")
print(f"  Facts flagged: {len(procedures)}/{test_total} = {proc_pct:.1f}%")
print(f"  Supersessions caught: {proc_sup}/{test_superseded} = {recall_proc*100:.1f}%")
print(f"  Precision: {precision_proc:.3f}")
print()

# Strategy 4: Flag entity-free facts
entity_free = [r for r in test if r.get('n_entities', 0) == 0]
ef_sup = sum(1 for r in entity_free if r['will_be_superseded'] == 1)
ef_pct = len(entity_free)/test_total*100
precision_ef = ef_sup/max(1,len(entity_free))
recall_ef = ef_sup/max(1,test_superseded)
print(f"Strategy 4: Flag entity-free facts")
print(f"  Facts flagged: {len(entity_free)}/{test_total} = {ef_pct:.1f}%")
print(f"  Supersessions caught: {ef_sup}/{test_superseded} = {recall_ef*100:.1f}%")
print(f"  Precision: {precision_ef:.3f}")
print()

# Strategy 5: ML model (logistic regression, threshold optimized on training)
# Train on training set, apply to test set
# We already computed this: F1=0.884 at threshold=0.75, reviews 17% of facts

# Train the model
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

X_train = [engineer(r) for r in train]
y_train = [r['will_be_superseded'] for r in train]
X_test = [engineer(r) for r in test]
y_test = [r['will_be_superseded'] for r in test]
d = len(X_train[0])

for j in range(d):
    col = [x[j] for x in X_train]
    mean = sum(col)/len(X_train)
    std = math.sqrt(sum((v-mean)**2 for v in col)/len(X_train))+1e-8
    for i in range(len(X_train)): X_train[i][j] = (X_train[i][j]-mean)/std
    for i in range(len(X_test)): X_test[i][j] = (X_test[i][j]-mean)/std

def sigmoid(z):
    if z > 500: return 1.0
    if z < -500: return 0.0
    return 1.0 / (1.0 + math.exp(-z))

def train_probs(X_tr, y_tr, X_te, threshold=0.75):
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

ml_preds = train_probs(X_train, y_train, X_test, threshold=0.75)
ml_flagged = sum(1 for p in ml_preds if p == 1)
ml_caught = sum(1 for p, t in zip(ml_preds, y_test) if p == 1 and t == 1)
ml_pct = ml_flagged/test_total*100
ml_precision = ml_caught/max(1,ml_flagged)
ml_recall = ml_caught/max(1,test_superseded)
print(f"Strategy 5: ML model (logistic regression, threshold=0.75)")
print(f"  Facts flagged: {ml_flagged}/{test_total} = {ml_flagged/test_total*100:.1f}%")
print(f"  Supersessions caught: {ml_caught}/{test_superseded} = {ml_recall*100:.1f}%")
print(f"  Precision: {ml_precision:.3f}")
print()

# Strategy 6: ML model + entity-free heuristic (combined)
combined_flagged = []
for i, (r, p) in enumerate(zip(test, ml_preds)):
    # Flag if: ML predicts superseded OR fact has no entities AND no reads
    has_ent = r.get('n_entities', 0) > 0
    has_read = r.get('use_count', 0) > 0
    if p == 1 or (not has_ent and not has_read):
        combined_flagged.append((i, r, p))

combined_n = len(combined_flagged)
combined_caught = sum(1 for i, r, p in combined_flagged if r['will_be_superseded'] == 1)
combined_pct = combined_n/test_total*100
combined_precision = combined_caught/max(1,combined_n)
combined_recall = combined_caught/max(1,test_superseded)
print(f"Strategy 6: Combined (ML + entity-free + never-read)")
print(f"  Facts flagged: {combined_n}/{test_total} = {combined_pct:.1f}%")
print(f"  Supersessions caught: {combined_caught}/{test_superseded} = {combined_recall*100:.1f}%")
print(f"  Precision: {combined_precision:.3f}")
