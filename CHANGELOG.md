# Changelog

## 0.3.0

- Each finding now has a stable id, classification, confidence, evidence, method, affected-record ids (capped, with a count), a recommended action and a disposition (`OPEN` until a dispositions file says otherwise). Scores and grades are computed the same way as in 0.2.0.
- A run writes a manifest (inputs with SHA-256, effective rules and their hash, counts) and a signature that depends only on the scores, layer counts and findings. `python run.py verify MANIFEST` replays the run and reports MATCH or MISMATCH.
- A JSON rules file, selected with `--rules`, `UNV_RULES` or the dashboard, deep-merges over the built-in thresholds, required fields, extra domain codes, weights and severities. Unknown keys are rejected.
- Asset groups and asset types are compared with a baseline derived from the Esri Utility Network Foundation models. Proposed matches, unmapped values and missing groups are findings plus `asset_mapping.csv`. This does not change the score.
- `python run.py profile` (and **Profile only** in the dashboard) reports fields, types, null rates, distinct values and key candidates without a score. Open questions are exported as CSV and XLSX.

## 0.2.0

- First public release. Six-stage validation, browser dashboard, HTML/CSV/JSON reports, and tests.
