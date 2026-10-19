#!/usr/bin/env bash
# Chunk 1 finish line. Run as root:  sudo ./scripts/verify.sh [--bw 10] [--duration 10]
# 1. records the machine state (scripts/sysinfo.sh -> results/env/<host>-<date>.txt)
# 2. starts the learning-switch controller from the venv
# 3. runs topologies/smoke.py: pingall + one idle iperf3 flow on 10 Mbps TCLinks
# Pass = 0% ping loss and iperf3 >= 90% of the configured rate (the brief expects ~95%).
# Appends one CSV row to results/verify/<host>.csv and keeps the full logs there.
set -uo pipefail
cd "$(dirname "$0")/.."
if [ "$(id -u)" -ne 0 ]; then echo "needs root (Mininet): sudo $0 $*" >&2; exit 1; fi
VENV=${VENV:-.venv}
[ -x "$VENV/bin/python" ] || { echo "no venv at $VENV: run ./setup.sh venv first" >&2; exit 1; }

HOST=$(hostname -s); STAMP=$(date +%Y-%m-%dT%H%M%S)
mkdir -p results/verify results/env
bash scripts/sysinfo.sh > "results/env/$HOST-$STAMP.txt"
echo "== sysinfo written to results/env/$HOST-$STAMP.txt"

DATAPATH=kernel
if ! (modprobe openvswitch 2>/dev/null || grep -q openvswitch /proc/modules 2>/dev/null \
      || [ -d /sys/module/openvswitch ]); then
  DATAPATH=user; echo "!! no openvswitch kernel module: using OVS userspace datapath"
fi

mn -c >/dev/null 2>&1
CTL_LOG="results/verify/$HOST-$STAMP-controller.log"
"$VENV/bin/python" controllers/run.py --ofp-tcp-listen-port 6653 controllers/learning_switch.py \
   > "$CTL_LOG" 2>&1 &
CTL_PID=$!
# Hand the results back to the user who ran sudo, so git and later scripts can edit them.
OWNER=${SUDO_USER:-}
trap 'kill $CTL_PID 2>/dev/null; mn -c >/dev/null 2>&1
      [ -n "$OWNER" ] && chown -R "$OWNER:$(id -gn "$OWNER")" results/verify results/env 2>/dev/null' EXIT
for i in $(seq 1 30); do
  "$VENV/bin/python" - <<'PYEOF' && break
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", 6653)) == 0 else 1)
PYEOF
  sleep 0.5
done
echo "== controller up (pid $CTL_PID), log: $CTL_LOG"

SMOKE_LOG="results/verify/$HOST-$STAMP-smoke.log"
CSV="results/verify/$HOST.csv"
[ -f "$CSV" ] || echo "timestamp,datapath,pingall_loss_pct,configured_mbps,iperf3_mbps,fraction,result" > "$CSV"
python3 -u topologies/smoke.py --datapath "$DATAPATH" --out "$CSV" "$@" 2>&1 | tee "$SMOKE_LOG" | grep -E "RESULT|SMOKE|Traceback|Error"
RC=${PIPESTATUS[0]}
echo "== full log: $SMOKE_LOG ; row appended to $CSV"
exit "$RC"
