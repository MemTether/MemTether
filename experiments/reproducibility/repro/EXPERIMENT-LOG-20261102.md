# Experiment Log
seed: 42
timestamp: 2026-11-01T11:38:39.465353

## E1_E5
- inputs: memtether_supersession_features.jsonl, locomo_event_dataset.jsonl
- outputs: results/E1_E5_results.json
- notes: group-aware random CV + temporal split + block bootstrap by source/date

## E2
- inputs: memtether_supersession_features.jsonl
- outputs: results/E2_rolling_origin.json
- notes: 10% test windows with >=20 positives; 5% gap

## E4
- inputs: memtether_supersession_features.jsonl
- outputs: results/E4_mi_uncertainty.json
- notes: quantile-bin MI + block bootstrap by source; 1000 iterations

## E6
- inputs: memtether_supersession_features.jsonl
- outputs: results/ner_gold_500.jsonl
- notes: deterministic pattern-based gold; system vs gold binary agreement

## E7
- inputs: memtether_supersession_features.jsonl
- outputs: results/taxonomy_llm1.jsonl, results/taxonomy_llm2.jsonl, results/taxonomy_agreement.json, results/taxonomy_double_labeled.jsonl
- notes: two LLM raters, Cohen kappa computed

## E8
- inputs: memtether_supersession_features.jsonl
- outputs: results/E8_governance_pr.json
- notes: threshold sweep 0.01-0.99 + heuristic k sweep

## E9
- inputs: memtether_supersession_features.jsonl
- outputs: results/E9_governance_bias.json
- notes: never-read rate by source/type/content-length bucket

## E10
- inputs: memtether_supersession_features.jsonl
- outputs: results/E10_standardization_leak.json
- notes: fold-level vs global standardization

## E13
- inputs: memtether_supersession_features.jsonl
- outputs: results/E13_dataset_provenance.json
- notes: per-source counts/positives/date ranges

## E17
- inputs: memtether_supersession_features.jsonl
- outputs: results/E17_governance_ci.json
- notes: block bootstrap for governance metrics

## E18
- inputs: memtether_supersession_features.jsonl
- outputs: results/E18_censoring_km.json
- notes: KM survival with right censoring; 72h threshold

## E19
- inputs: memtether_supersession_features.jsonl
- outputs: results/E19_repeated_measures.json
- notes: record-level vs source-level vs source-type group CV

## E20
- inputs: memtether_supersession_features.jsonl
- outputs: results/E20_sensitivity.json
- notes: threshold x split x feature-set grid
