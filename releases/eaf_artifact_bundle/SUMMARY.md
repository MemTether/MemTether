# EAF 500Q Artifact Bundle

## Result
EAF strict accuracy: **75.7%** (253/334 applicable, 166 prose NA)
Baseline strict: 56.4% (177/314)
Delta: +19.4pp
Paired McNemar: p=7.84e-07 (highly significant)

## Files
- eaf_500q_checkpoint.json: EAF strict per-question (500/500)
- eaf_500q_pq_final.json: EAF vs baseline paired per-question
- eaf_500q_f1_checkpoint.json: EAF F1 variant

## Reproduce
```bash
cd E:\\RUANJIAN\\memory_hub\\experiments\\paper1
python eaf_500q.py
```

## Methodology
- Dataset: LongMemEval oracle, 500 questions
- Pipeline: memsearch.search_hybrid + EAF (ECO + SSI + AFN)
- Judge: LLM-as-grader (CORRECT/WRONG)
- Strict: substring match lower bound
