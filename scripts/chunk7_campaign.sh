#!/usr/bin/env bash
# Chunk 7a: damage-regime campaign (LOG.md, 2026-11-13 pre-registration).
# Per setting (rate, congestion control, RACK, spine delays): one feasibility
# run (E1 alone on s4, E2 alone on s1; each must reach >= 90 % of the link and
# every host must read back the sysctls, else the setting is skipped), then 20
# matched move / no-move trials (s4->s1 and s1->s4, n_p = 1, n_q = 0 or 1, 5
# each). Then the repeated-move runs. Rootless (scripts/userns.sh via the
# runner). Resumable: the runner skips runs that already have results.
#
#   scripts/chunk7_campaign.sh [all|100|flap|10]
set -uo pipefail
cd "$(dirname "$0")/.."
OUT=results/regime
PART=${1:-all}
D1=1,1,5,10
D2=1,1,20,50

feasible() {   # dir rate cc rack -> 0 if both elephants >= 90 % and sysctls read back
  python3 - "$@" <<'EOF'
import csv, json, sys
d, rate, cc, rack = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
rows = [r for r in csv.DictReader(open(d + "/flows.csv")) if r["kind"] == "elephant"]
m = json.load(open(d + "/runs/alone/meta.json"))
ok_sys = all(v["net.ipv4.tcp_congestion_control"] == cc and v["net.ipv4.tcp_recovery"] == rack
             for v in m["sysctl_hosts"].values())
g = {r["id"]: float(r["goodput_steady_mbps"]) for r in rows}
ok_rate = len(g) == 2 and all(x >= 0.9 * rate for x in g.values())
print("feasibility %s: steady goodput %s Mbit/s (%s), sysctls read back in all hosts: %s, errors: %s"
      % (d, g, ", ".join("%.1f %%" % (100 * x / rate) for x in g.values()), ok_sys, m["errors"]))
sys.exit(0 if ok_sys and ok_rate and not m["errors"] else 1)
EOF
}

setting() {    # index rate cc rack delayset
  local idx=$1 rate=$2 cc=$3 rack=$4 dn=$5
  local delays; [ "$dn" = D1 ] && delays=$D1 || delays=$D2
  local queue; [ "$rate" = 100 ] && queue=200 || queue=20
  local name="$rate-$cc-rack$rack-$dn"
  local sys="net.ipv4.tcp_congestion_control=$cc,net.ipv4.tcp_recovery=$rack"
  echo "=== setting $idx: $name ($(date -u +%FT%TZ))"
  if [ "$cc" != cubic ] && ! grep -qw "$cc" /proc/sys/net/ipv4/tcp_available_congestion_control; then
    echo "NOT RUN: $cc not available (needs: sudo modprobe tcp_$cc)"; return
  fi
  local F=$OUT/feas-$name
  [ -f "$F/jobs.json" ] || python3 experiments/damage_design.py --alone --seed $((70 + idx)) --sysctl "$sys" \
    --spine-delays "$delays" --out "$F/jobs.json" >/dev/null
  python3 experiments/runner.py replay --spec "$F/jobs.json" --mbps "$rate" --queue "$queue" \
    --keep-pcaps none --out "$F" | grep -v "^results"
  if ! feasible "$F" "$rate" "$cc" "$rack"; then
    echo "NOT RUN: setting $name failed its feasibility check"; return
  fi
  local C=$OUT/regime-$name
  [ -f "$C/jobs.json" ] || python3 experiments/damage_design.py --grid "4>1,1>4;1;0,1;5" \
    --placed-only --seed $((70 + idx)) --sysctl "$sys" --spine-delays "$delays" --out "$C/jobs.json"
  python3 experiments/runner.py replay --spec "$C/jobs.json" --mbps "$rate" --queue "$queue" \
    --keep-pcaps E1 --out "$C"
}

flap() {       # rate
  local rate=$1 queue; [ "$rate" = 100 ] && queue=200 || queue=20
  local C=$OUT/flap-$rate-cubic-rack1-D1
  echo "=== repeated moves at $rate Mbit/s ($(date -u +%FT%TZ))"
  [ -f "$C/jobs.json" ] || python3 experiments/damage_design.py --flap 0.5,2 --flap-reps 5 \
    --seed $((90 + (rate == 100 ? 0 : 1))) --spine-delays "$D1" \
    --sysctl net.ipv4.tcp_congestion_control=cubic,net.ipv4.tcp_recovery=1 --out "$C/jobs.json"
  python3 experiments/runner.py replay --spec "$C/jobs.json" --mbps "$rate" --queue "$queue" \
    --keep-pcaps E1 --out "$C"
}

if [ "$PART" = all ] || [ "$PART" = 100 ]; then
  i=0
  for cc in cubic bbr; do for rack in 1 0; do for dn in D1 D2; do
    setting $i 100 $cc $rack $dn; i=$((i + 1))
  done; done; done
fi
if [ "$PART" = all ] || [ "$PART" = flap ]; then
  flap 100; flap 10
fi
if [ "$PART" = all ] || [ "$PART" = 10 ]; then
  i=8
  for cc in cubic bbr; do for rack in 1 0; do for dn in D1 D2; do
    setting $i 10 $cc $rack $dn; i=$((i + 1))
  done; done; done
fi
echo "=== done ($(date -u +%FT%TZ))"
