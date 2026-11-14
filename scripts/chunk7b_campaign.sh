#!/usr/bin/env bash
# Chunk 7b (LOG.md 2026-11-14 pre-registration): 100 Mbit/s, spine delays
# 1/1/20/50 ms, cubic, RACK on, rootless. Resumable (the runner skips runs that
# already have results).
#
#   scripts/chunk7b_campaign.sh dataset   # 60 deliberate-reroute pairs (~2.5 h)
#   scripts/chunk7b_campaign.sh models    # damage models -> models/chunk7b
#   scripts/chunk7b_campaign.sh w1        # 5 controllers x 10 seeds, longpath_stagger
#   scripts/chunk7b_campaign.sh w2        # 5 controllers x 10 seeds, ecmp_mixed
set -euo pipefail
cd "$(dirname "$0")/.."
SYS=net.ipv4.tcp_congestion_control=cubic,net.ipv4.tcp_recovery=1
DEL=1,1,20,50
DS=results/dataset/dataset7b-2026-11-14
CTRLS=ecmp,hedera,cooldown,costaware_formula,costaware_ml
case "${1:-}" in
  dataset)
    [ -f $DS/jobs.json ] || python3 experiments/damage_design.py --trials-per-pair 5 --seed 77 \
      --cap-mode fq --cap-scale 10 --t-move-range 6,14 --placed-only --sysctl $SYS \
      --spine-delays $DEL --out $DS/jobs.json
    python3 experiments/runner.py replay --spec $DS/jobs.json --mbps 100 --queue 200 \
      --keep-pcaps E1 --out $DS ;;
  models)
    .venv/bin/python analysis/damage_dataset.py --design $DS --out $DS/dataset
    .venv/bin/python analysis/damage_model.py --data $DS/dataset/dataset.csv \
      --out results/model/model7b-2026-11-14 --models models/chunk7b --group group --fig ;;
  w1|w2)
    sc=longpath_stagger; [ "$1" = w2 ] && sc=ecmp_mixed
    python3 experiments/runner.py campaign --controllers $CTRLS --scenario $sc --runs 10 \
      --seed 1 --mbps 100 --queue 200 --spine-delays $DEL --sysctl $SYS --keep-pcaps none \
      --cost-models models/chunk7b --out results/compare7b/$1-$sc ;;
  *) echo "usage: $0 dataset|models|w1|w2" >&2; exit 2 ;;
esac
