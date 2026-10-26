#!/usr/bin/env bash
# Chunk 5 campaign, as run on 2026-10-26 (rootless, about 2.1 h), then the
# dataset. Console output -> results/dataset/<x>.console.log. The session ran
# the campaign under `systemd-inhibit --what=idle:sleep`.
set -u
cd "$(dirname "$0")/.."
D=2026-10-26
mkdir -p results/dataset/dataset-$D
python3 experiments/damage_design.py --trials-per-pair 5 --seed 5 \
  --out results/dataset/dataset-$D/jobs.json > results/dataset/dataset-$D.design.log 2>&1
python3 experiments/runner.py replay --spec results/dataset/dataset-$D/jobs.json \
  --out results/dataset/dataset-$D > results/dataset/dataset-$D.console.log 2>&1
.venv/bin/python analysis/damage_dataset.py --design results/dataset/dataset-$D \
  --live results/compare/compare-$D --replay results/damage/damage-$D \
  --out results/dataset/dataset-$D/dataset --figures figures
echo ALL DONE
