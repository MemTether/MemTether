import json, io, sys, re, math, random
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import Counter, defaultdict
random.seed(42)

locomo_path = r'E:\RUANJIAN\memory_hub\bench_data\locomo10.json'
with open(locomo_path, encoding='utf-8') as f:
    data = json.load(f)

# Extract all turns and QA pairs
all_turns = []  # (conv_id, session, turn_id, speaker, text, is_evidence)
all_qa = []

for conv_idx, conv in enumerate(data):
    conv_id = conv.get('sample_id', f'conv_{conv_idx}')
    convs = conv.get('conversation', {})
    
    # Get session keys in order
    session_keys = sorted([k for k in convs.keys() if k.startswith('session_') and 'date' not in k],
                         key=lambda x: int(x.split('_')[1]))
    
    for sk in session_keys:
        sess = convs[sk]
        session_num = int(sk.split('_')[1])
        if isinstance(sess, dict) and 'conversation' in sess:
            for turn_idx, turn in enumerate(sess['conversation']):
                all_turns.append({
                    'conv_id': conv_id,
                    'session': session_num,
                    'turn_idx': turn_idx,
                    'speaker': turn.get('speaker', ''),
                    'text': turn.get('text', ''),
                    'dia_id': turn.get('dia_id', ''),
                    'is_evidence': False  # will be filled from QA
                })
        elif isinstance(sess, list):
            for turn_idx, turn in enumerate(sess):
                all_turns.append({
                    'conv_id': conv_id,
                    'session': session_num,
                    'turn_idx': turn_idx,
                    'speaker': turn.get('speaker', ''),
                    'text': turn.get('text', ''),
                    'dia_id': turn.get('dia_id', ''),
                    'is_evidence': False
                })
    
    # Get QA pairs and mark evidence turns
    qa = conv.get('qa', [])
    evidence_dia_ids = set()
    for q in qa:
        for ev in q.get('evidence', []):
            evidence_dia_ids.add(ev)
    
    # Mark turns as evidence
    for t in all_turns:
        if t['conv_id'] == conv_id and t['dia_id'] in evidence_dia_ids:
            t['is_evidence'] = True

print(f"Total turns: {len(all_turns)}")
print(f"Evidence turns: {sum(1 for t in all_turns if t['is_evidence'])}")
print(f"Non-evidence turns: {sum(1 for t in all_turns if not t['is_evidence'])}")
print(f"QA pairs: {sum(len(d.get('qa', [])) for d in data)}")

# Now: predict which turns will be evidence for future questions
# Features: session number, turn position in session, text length, speaker, content keywords
# Label: is_evidence (will this turn be referenced by future QA?)

# Engineer features
def engineer_turn(t, conv_turns_before=0):
    text = t.get('text', '')
    session = t.get('session', 0)
    return [
        session / 20.0,  # normalized session number
        t.get('turn_idx', 0) / 20.0,  # position in session
        len(text) / 500.0,  # text length
        1.0 if t.get('speaker') == 'Caroline' else 0.0,  # speaker (conv-specific)
        1.0 if '?' in text else 0.0,  # is question
        1.0 if re.search(r'\d+', text) else 0.0,  # contains number
        1.0 if len(text) < 50 else 0.0,  # short turn
        min(conv_turns_before / 400.0, 1.0),  # how many turns came before
    ]

# Sort all turns by conv and session (chronological)
all_turns.sort(key=lambda t: (t['conv_id'], t['session'], t['turn_idx']))

# Engineer features
turn_counter = defaultdict(int)
X, y = [], []
for t in all_turns:
    conv_id = t['conv_id']
    turn_counter[conv_id] += 1
    X.append(engineer_turn(t, turn_counter[conv_id]))
    y.append(1.0 if t['is_evidence'] else 0.0)

n = len(X); d = len(X[0])
print(f"\nFeatures: {d}, Records: {n}, Evidence rate: {sum(y)/n*100:.1f}%")

# Standardize
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

# RANDOM CV
indices = list(range(n))
random.shuffle(indices)
fold_size = n // 5
f1_random_folds = []
for i in range(5):
    test_idx = set(indices[i*fold_size:(i+1)*fold_size])
    train_idx = [j for j in range(n) if j not in test_idx]
    X_tr = [X[j] for j in train_idx]; y_tr = [y[j] for j in train_idx]
    X_te = [X[j] for j in indices[i*fold_size:(i+1)*fold_size]]
    y_te = [y[j] for j in indices[i*fold_size:(i+1)*fold_size]]
    probs = train_probs(X_tr, y_tr, X_te)
    y_pred = [1.0 if p >= 0.5 else 0.0 for p in probs]
    f1_random_folds.append(f1_macro(y_te, y_pred))

mean_random = sum(f1_random_folds)/5
print(f"\n=== RANDOM CV ===")
print(f"F1 = {mean_random:.3f} ± {math.sqrt(sum((f-mean_random)**2 for f in f1_random_folds)/5):.3f}")

# TEMPORAL SPLIT (by session number: train on early sessions, test on late sessions)
# Sort by session number
session_order = sorted(range(n), key=lambda i: all_turns[i]['session'])
split_point = int(n * 0.7)
train_idx = session_order[:split_point]
test_idx = session_order[split_point:]

X_tr = [X[i] for i in train_idx]; y_tr = [y[i] for i in train_idx]
X_te = [X[i] for i in test_idx]; y_te = [y[i] for i in test_idx]

probs = train_probs(X_tr, y_tr, X_te)
y_pred = [1.0 if p >= 0.5 else 0.0 for p in probs]
f1_temporal = f1_macro(y_te, y_pred)

print(f"\n=== TEMPORAL SPLIT ===")
print(f"F1 = {f1_temporal:.3f}")
print(f"\nDiscrepancy: Random CV {mean_random:.3f} vs Temporal {f1_temporal:.3f}")
print(f"Ratio: {mean_random/max(0.001, f1_temporal):.1f}x")
