#!/usr/bin/env bash
# Reproduce the experiment environment from scratch on Ubuntu (22.04 .. 26.04).
#
# Two steps because Mininet/OVS need root but the venv must not be root-owned:
#   sudo ./setup.sh system     # apt packages: mininet, openvswitch, iperf3, tshark...
#        ./setup.sh venv       # python venv + pinned controller/analysis packages
#   sudo ./scripts/verify.sh   # finish line: pingall + idle iperf3 at 10 Mbps
# `./setup.sh` with no argument runs both (asks for sudo once for the apt step).
set -euo pipefail
cd "$(dirname "$0")"

APT_PKGS=(mininet openvswitch-switch openvswitch-common iperf3 iperf
          tshark tcpdump ethtool iproute2 iputils-ping telnet net-tools
          python3-venv python3-pip git)

system_step() {
  if [ "$(id -u)" -ne 0 ]; then echo "system step needs root: sudo $0 system" >&2; exit 1; fi
  . /etc/os-release
  echo "== OS: $PRETTY_NAME, kernel $(uname -r)"
  export DEBIAN_FRONTEND=noninteractive
  # tshark asks whether non-root may capture; answer no (we run captures as root).
  echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections
  apt-get update
  apt-get install -y "${APT_PKGS[@]}"
  # Mininet's own python module is installed by the apt package for the system
  # python3; Mininet scripts (topologies/*.py) run with that interpreter under sudo.
  python3 -c "import mininet; print('mininet python module OK:', mininet.__file__)"
  systemctl enable --now openvswitch-switch 2>/dev/null || true
  ovs-vsctl show >/dev/null && echo "== Open vSwitch running: $(ovs-vsctl --version | head -1)"
  modprobe openvswitch 2>/dev/null && echo "== openvswitch kernel module loaded" \
    || echo "!! no openvswitch kernel module: verify.sh will fall back to the userspace datapath"
  echo "== system step done"
}

venv_step() {
  if [ "$(id -u)" -eq 0 ] && [ -z "${ALLOW_ROOT_VENV:-}" ]; then
    echo "run the venv step as your normal user (not sudo), or set ALLOW_ROOT_VENV=1" >&2; exit 1
  fi
  PY=${PYTHON:-python3}
  ver=$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  echo "== Python for the controller venv: $PY $ver"
  $PY - <<'PYEOF'
import sys
if sys.version_info < (3, 10):
    sys.exit("os-ken 4.2.2 needs Python >= 3.10; set PYTHON=python3.11 (or newer) and rerun")
PYEOF
  [ -d .venv ] || $PY -m venv .venv
  .venv/bin/pip install --upgrade pip wheel >/dev/null
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python -c "import os_ken; print('os-ken', os_ken.__version__, 'OK')"
  .venv/bin/python -c "import numpy, pandas, matplotlib, sklearn; print('analysis stack OK')"
  echo "== venv step done. Controller: .venv/bin/python controllers/run.py controllers/learning_switch.py"
}

case "${1:-all}" in
  system) system_step ;;
  venv)   venv_step ;;
  all)    sudo "$0" system && "$0" venv ;;
  *) echo "usage: $0 [system|venv|all]" >&2; exit 2 ;;
esac
