#!/usr/bin/env bash
# Rebuild every table, result and figure from the raw CSVs, in order.
# Data location: $CR_DATA (default ~/cr_data), with the raw CSVs in $CR_DATA/archive/<name>/<name>.csv
set -euo pipefail
cd "$(dirname "$0")/src"
PY="${PYTHON:-python}"
$PY build_parquet.py   # 10 CSVs -> Parquet (about 8 min, once)
$PY build_battles.py   # dedupe on the battle key, ladder modes only (about 1 min)
$PY audit.py           # Phase 1 audit (about 2 min)
$PY requeue.py         # Phase 2 re-queue analysis and robustness checks (about 1 min)
$PY level_gap.py       # Phase 3 level-gap models (about 1 min)
$PY power.py           # Phase 4 baselines, variances, MDEs (about 1 min)
$PY figures.py         # all figures
