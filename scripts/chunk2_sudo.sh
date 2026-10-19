#!/usr/bin/env bash
# The only steps of Chunk 2 that need real root, batched so they need one sudo:
#   1. scripts/verify.sh again (confirms the sysinfo/chown fixes under sudo)
#   2. a small replication of the gate on the system OVS, outside any user
#      namespace (R2 and C1, 3 trials each), to check that the rootless runs
#      in results/gate/gate-2026-10-19 measure the same thing.
# Usage:  sudo ./scripts/chunk2_sudo.sh
set -uo pipefail
cd "$(dirname "$0")/.."
[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo $0" >&2; exit 1; }
echo "== 1/2 verify.sh"; ./scripts/verify.sh; rc1=$?
echo "== 2/2 gate cross-check as root (about 4 minutes)"
python3 experiments/gate.py campaign --out results/gate/sudo-check-2026-10-19 \
  --only R2,C1 --reps 3; rc2=$?
[ -n "${SUDO_UID:-}" ] && chown -R "$SUDO_UID:$SUDO_GID" results/gate results/verify results/env
echo "== done: verify rc=$rc1, cross-check rc=$rc2"
