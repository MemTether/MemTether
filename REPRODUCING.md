# Reproducing the Paper Numbers

Every headline number in the merged paper maps to a data artifact or a command below. For items that require the production database (not releasable), we ship de-identified feature matrices instead and mark the command as `[artifact]`.

## Environment

**Verified 2026-11-02** (clean venv, Python 3.10.11):

```
python -m venv venv
venv\Scripts\pip install memtether          # v0.1.0a84 from PyPI
venv\Scripts\python -c "import memtether"   # OK, version 0.1.0a84
venv\Scripts\memtether stats                 # OK
venv\Scripts\memtether remember "test" --source test  # OK, returns uid
venv\Scripts\memtether search "test"         # OK, keyword fallback works
```

Note: vector search requires `pip install memtether[vector]` (chromadb); without it the system degrades gracefully to keyword-only with a clear warning. If pypi.org is blocked in your network, use `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`.

Pinned: Python >= 3.10, SQLite >= 3.39 (WAL mode), bge-m3 (int8) via sentence-transformers.

From source:
```
git clone https://github.com/MemTether/MemTether
cd MemTether
pip install -e ".[dev]"
```

## 1. LongMemEval-500: 62.4% strict / 68.8% judge

- Artifact: `cac-artifact/eaf_500q_full_v2_result.json` (per-question answers + judge scores)
- Judge: DeepSeek-V4-Flash; cross-validated with GPT-5.6-Terra, 96.1% agreement (318/331)
- Cross-validation artifact: `cac-artifact/eaf_500q_gptx_terra_judge.json` (331 items) and `eaf_500q_mixed_judge.json` (full 494)

```python
import json
d = json.load(open("cac-artifact/eaf_500q_full_v2_result.json"))
print(f"strict: {d['strict_hits']}/{d['strict_total']} = {d['strict_rate']:.3f}")
print(f"judge:  {d['judge_hits']}/{d['judge_total']} = {d['judge_rate']:.3f}")
# strict: 206/330 = 0.624
# judge:  340/494 = 0.688
```

## 2. Supersession Prediction: F1 = 0.692 (default) / 0.857 (threshold 0.66)

- Data: `repro/memtether_supersession_features.jsonl` (2,635 records, 386 positives)
- Logistic regression, 5-fold stratified CV, leak-free features (n_superseded_by / n_supersedes removed)

```python
# scripts/reproduce_supersession_f1.py in the companion artifact
# Expected output:
#   F1 (default 0.5):      0.692  CI [0.684, 0.701]
#   F1 (threshold 0.66):   0.857  CI [0.844, 0.869]
```

## 3. Ablation (random CV, full model F1 = 0.502)

- Artifact: `repro/results/ablation_random_cv.json`

```python
import json
d = json.load(open("repro/results/ablation_random_cv.json"))
print(d["full"])  # 0.502
```

## 4. ClockScore: 0.103 / 0.035 / 0.421

- Artifacts: `repro/results/cross_system_clockscore.json`, `repro/results/ablation_study.json`
- Computed per Section 5: mean |M_random - M_temporal(w)| over W temporal windows

## 5. Cross-System Growth (Table 8)

- Dataset: `repro/cross_system_benchmark_200.jsonl` (200 records)
- Results: `repro/results/cross_system_definitive.json`

```python
import json
d = json.load(open("repro/results/cross_system_definitive.json"))
for sys_name, info in d["systems"].items():
    print(f"{sys_name}: ratio={info['ratio']}, ceiling={info.get('ceiling', False)}")
# MemTether: ratio=1.0, ceiling=False
# mem0:      ratio=0.1, ceiling=True (20)
# Hindsight: ratio=0.5, ceiling=True (100)
# SimpleMem: ratio=1.185, ceiling=False
```

## 6. Dead Memories / Injection Coverage

- Source: production `memory.db` (not releasable; raw logs withheld)
- De-identified features: `repro/memtether_supersession_features.jsonl`
- Key numbers: 250/2,635 total dead (189/1,981 active-only), injection 3,980 / 825,032 chars = 0.48%

```sql
-- On your own memory.db (schema in paper Section 3):
SELECT COUNT(*) FROM facts WHERE status='active';        -- 1981
-- dead = active records with use_count = 0 after >= 7 days
SELECT COUNT(*) FROM facts WHERE status='active' AND use_count=0;  -- 189
-- Injection coverage = 3980 / SUM(LENGTH(content))       -- 0.0048
```

## 7. Latency (< 20 ms)

```bash
python benchmarks/bench_latency.py --n 200
# search 5.9ms / write 1.2ms / supersede 3.2ms / verify 19.9ms
```

## 8. Memory Court Tamper Detection (5/5)

```bash
python -m pytest tests/test_court.py -k tamper -v
# 5 scenarios: field edit, row delete, chain splice, anchor rewrite, full rebuild
```

## 9. GovernedMemory Bench (8,005 tasks)

- Schema and generation script: `benchmarks/governed/` (companion artifact)
- Dimensions: supersession (2,635), entity grounding (2,635), retrieval readiness (2,635), temporal ordering (100)

## Known Limitations of Reproducibility

1. **Production DB not released** (privacy): raw write logs contain user-identifying content. De-identified feature matrices are provided instead.
2. **mem0/Hindsight/SimpleMem runs** use each system's default embedding; exact pip pins are in `repro/results/*.json` metadata.
3. **The 331-item Terra cross-validation subset** is stratified but not the full 494; full-set mixed-judge results in `eaf_500q_mixed_judge.json`.
