# Reproducibility package for temporal-leakage paper

This folder contains the de-identified dataset, analysis scripts, and result JSONs for the paper:

`A Clocked-Temporal Case Study of Post-Hoc Leakage in Agent-Memory Supersession Prediction`

## Files

- `memtether_supersession_features.jsonl`: de-identified MemTether feature dataset (2,635 records)
- `locomo_event_dataset.jsonl`: LoCoMo-derived external event dataset (2,541 observations, 832 positives)
- `results/E1_E5_results.json`: group-aware random CV + temporal splits + block bootstrap on both datasets
- `results/E2_rolling_origin.json`: formal rolling-origin analysis
- `results/E4_mi_uncertainty.json`: MI with block-bootstrap CI
- `results/E6 NER`: `results/ner_gold_500.jsonl`
- `results/E7 taxonomy`: `results/taxonomy_llm1.jsonl`, `results/taxonomy_llm2.jsonl`, `results/taxonomy_agreement.json`
- `results/E8 governance PR`: `results/E8_governance_pr.json`
- `results/E9 governance bias`: `results/E9_governance_bias.json`
- `results/E10 standardization`: `results/E10_standardization_leak.json`
- `results/E13 provenance`: `results/E13_dataset_provenance.json`
- `results/E17 governance CI`: `results/E17_governance_ci.json`
- `results/E18 censoring`: `results/E18_censoring_km.json`
- `results/E19 repeated measures`: `results/E19_repeated_measures.json`
- `results/E20 sensitivity`: `results/E20_sensitivity.json`
- `EXPERIMENT-LOG-20261102.md`: seed, inputs, outputs, and notes for each experiment

## Reproducibility

All experiments use fixed seed 42. Scripts are located in `experiments/reproducibility/`.

## Anonymous artifact

For anonymous review, use the commit hash of this repository and anonymize authorship. No raw operational data is included.
- `results/joint_MI.json`: joint discretized MI over all features and write-time-only features
- `results/interaction_entity_type.json`: chi-square interaction between entity presence and type
- `results/content_type_analysis.json`: content-type classification and supersession rates
- `results/psm_entity_effect.json`: propensity-score matching and McNemar test
- `results/rd_analysis.json`: discontinuity check at entity cutoff
