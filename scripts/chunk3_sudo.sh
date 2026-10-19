#!/usr/bin/env bash
# Optional root cross-check for Chunk 3 (everything in Chunk 3 ran rootless in
# user namespaces; this repeats a small part as real root on the system OVS):
#   1. the TSQ check (does the artifact and its fix look the same as root?)
#   2. two ECMP runs with seeds 1 and 2 (placement and metrics vs the rootless
#      campaign results/ecmp/ecmp-2026-10-19)
# Usage:  sudo ./scripts/chunk3_sudo.sh        (about 4 minutes)
set -uo pipefail
cd "$(dirname "$0")/.."
[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo $0" >&2; exit 1; }
echo "== 1/2 TSQ check as root"
python3 experiments/tsq_check.py --out results/tsq/sudo-check-2026-10-19; rc1=$?
echo "== 2/2 ECMP seeds 1-2 as root"
python3 experiments/runner.py campaign --controller ecmp --scenario ecmp_mixed --runs 2 \
  --seed 1 --out results/ecmp/sudo-check-2026-10-19; rc2=$?
[ -n "${SUDO_UID:-}" ] && chown -R "$SUDO_UID:$SUDO_GID" results/tsq results/ecmp
echo "== done: tsq rc=$rc1, ecmp rc=$rc2"
