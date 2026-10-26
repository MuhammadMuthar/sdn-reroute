#!/usr/bin/env bash
# Chunk 4 campaigns, in order, as run on 2026-10-26 (rootless, about 2.5 h).
# Console output -> results/<x>.console.log. The session ran each step by hand
# under `systemd-inhibit --what=idle:sleep` so the laptop could not suspend;
# the replay must run on the same boot as the live campaign (ECMP's dp_hash is
# seeded at boot, and the replay keeps the mice on their live ECMP paths).
set -u
cd "$(dirname "$0")/.."
R=python3
D=2026-10-26
$R experiments/runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown \
  --scenario ecmp_mixed --runs 10 --seed 1 \
  --out results/compare/compare-$D > results/compare/compare-$D.console.log 2>&1
mkdir -p results/damage/damage-$D
$R experiments/replay.py --live results/compare/compare-$D \
  --out results/damage/damage-$D/jobs.json > results/damage/damage-$D.jobs.log 2>&1
$R experiments/runner.py replay --spec results/damage/damage-$D/jobs.json \
  --out results/damage/damage-$D > results/damage/damage-$D.console.log 2>&1
.venv/bin/python analysis/chunk4.py --compare results/compare/compare-$D \
  --damage results/damage/damage-$D
# post hoc: per-flow reordering inside l1 (needs the local uplink pcaps)
.venv/bin/python analysis/mice_reorder.py results/compare/compare-$D
echo ALL DONE
