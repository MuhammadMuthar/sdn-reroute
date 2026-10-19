#!/usr/bin/env bash
# Chunk 3 campaigns, in order, as run on 2026-10-19 (rootless, about 2 h). Console output -> results/<x>.console.log
set -u
cd "$(dirname "$0")/.."
R=python3
$R experiments/runner.py campaign --controller ecmp --scenario ecmp_mixed --runs 10 --seed 1 \
  --out results/ecmp/ecmp-2026-10-19 > results/ecmp/ecmp-2026-10-19.console.log 2>&1
$R experiments/runner.py campaign --controller ecmp --scenario ecmp_mixed --runs 3 --seed 1 \
  --out results/ecmp/ecmp-2026-10-19-replay > results/ecmp/ecmp-2026-10-19-replay.console.log 2>&1
mkdir -p results/tsq
$R experiments/tsq_check.py --out results/tsq/tsq-2026-10-19 > results/tsq/tsq-2026-10-19.console.log 2>&1
$R experiments/runner.py campaign --controller ecmp --scenario loaded_move \
  --conditions IC1,IR2,LC1,LR0,LR2 --runs 10 --seed 1 \
  --out results/loaded/loaded-2026-10-19 > results/loaded/loaded-2026-10-19.console.log 2>&1
$R experiments/runner.py campaign --controller ecmp --scenario loaded_move --host-qdisc default \
  --conditions IC1,IR2,LC1,LR0,LR2 --runs 10 --seed 1 \
  --out results/loaded/loaded-default-2026-10-19 > results/loaded/loaded-default-2026-10-19.console.log 2>&1
echo ALL DONE
