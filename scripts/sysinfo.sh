#!/usr/bin/env bash
# Print the machine state every result depends on. verify.sh saves this to results/env/.
echo "# sysinfo $(date -Is) host=$(hostname)"
. /etc/os-release 2>/dev/null; echo "os: ${PRETTY_NAME:-unknown}"
echo "kernel: $(uname -r)"; echo "cpu: $(nproc) cores, $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
echo "mem_kb: $(grep MemTotal /proc/meminfo | awk '{print $2}')"
echo "python3: $(python3 --version 2>&1)"
for v in .venv/bin/python; do [ -x $v ] && echo "venv python: $($v --version 2>&1); os-ken $($v -c 'import os_ken;print(os_ken.__version__)' 2>&1)"; done
echo "mininet: $(mn --version 2>&1)"; echo "ovs: $(ovs-vsctl --version 2>&1 | head -1)"
echo "ovs kernel module: $([ -d /sys/module/openvswitch ] && echo loaded || echo absent)"
echo "iperf3: $(iperf3 --version 2>&1 | head -1)"; echo "tshark: $(tshark --version 2>&1 | head -1)"
echo "ethtool: $(ethtool --version 2>&1 | head -1)"; echo "tc: $(tc -V 2>&1)"
echo "qdiscs:"; ip link add sysinfo0 type veth peer name sysinfo1 2>/dev/null && {
  for k in "htb" "netem delay 1ms" "tbf rate 10mbit burst 32kbit latency 400ms" "fq_codel"; do
    tc qdisc replace dev sysinfo0 root $k >/dev/null 2>&1 && echo "  ok: $k" || echo "  missing: $k"
    tc qdisc del dev sysinfo0 root 2>/dev/null; done; ip link del sysinfo0; }
echo "net.ipv4.tcp_* sysctls:"; sysctl -a 2>/dev/null | grep -E '^net\.ipv4\.tcp_' | sed 's/^/  /'
echo "net.core.default_qdisc: $(sysctl -n net.core.default_qdisc 2>/dev/null)"
