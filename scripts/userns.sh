#!/usr/bin/env bash
# Run a command as "root" inside a throwaway user + network + pid + mount
# namespace, with a private Open vSwitch, so Mininet works without sudo.
#
#   ./scripts/userns.sh python3 topologies/smoke.py
#   ./scripts/userns.sh python3 experiments/gate.py --plan ...
#
# Why this is equivalent to `sudo` for our experiments: the hosts are network
# namespaces either way, the TCP stack, netem/htb and the OVS kernel datapath
# are the same kernel code, and the openvswitch module accepts datapath
# commands from a namespace's own root (GENL_UNS_ADMIN_PERM). What differs:
#   - "root" here is only root over these namespaces; the real host network,
#     the system OVS and the host's files are untouched (files are written as
#     the invoking user).
#   - a private ovsdb-server/ovs-vswitchd runs in a private /tmp and dies with
#     the namespace, so every invocation starts from an empty switch database;
#     `mn -c` inside only ever sees this namespace.
#   - the host's net.ipv4.tcp_* sysctls are NOT inherited by a new network
#     namespace in every case; experiments must log sysctls from inside the
#     Mininet hosts (experiments/gate.py does).
# Needs unprivileged user namespaces (Ubuntu: allowed for `unshare` here).
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 command [args...]" >&2; exit 2; }
if [ "${SDN_USERNS_INNER:-}" != 1 ]; then
  exec unshare --user --map-root-user --net --pid --fork --mount --mount-proc \
       env SDN_USERNS_INNER=1 bash "$0" "$@"
fi

# --- inside the namespaces ---------------------------------------------------
mount -t tmpfs -o mode=1777 tmpfs /tmp          # Mininet's cleanup rm's /tmp/*.log
export OVS_RUNDIR=/tmp/ovs OVS_LOGDIR=/tmp/ovs OVS_DBDIR=/tmp/ovs OVS_SYSCONFDIR=/tmp/ovs
mkdir -p /tmp/ovs
ip link set lo up
ovsdb-tool create /tmp/ovs/conf.db /usr/share/openvswitch/vswitch.ovsschema
ovsdb-server --remote=punix:/tmp/ovs/db.sock --pidfile --detach \
  --log-file=/tmp/ovs/ovsdb-server.log /tmp/ovs/conf.db
ovs-vsctl --no-wait init
ovs-vswitchd --pidfile --detach --log-file=/tmp/ovs/ovs-vswitchd.log unix:/tmp/ovs/db.sock
for _ in $(seq 1 50); do ovs-vsctl show >/dev/null 2>&1 && break; sleep 0.1; done
rc=0
"$@" || rc=$?
ovs-appctl -t ovs-vswitchd exit >/dev/null 2>&1 || true
ovs-appctl -t ovsdb-server exit >/dev/null 2>&1 || true
exit "$rc"
