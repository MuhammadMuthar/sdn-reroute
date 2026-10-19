"""Numbering of the 4-path leaf-spine, shared by topologies/leafspine.py and
the controllers (which run in the venv, without Mininet). See leafspine.py.
"""

N_SPINES = 4
N_LEAVES = 2
# One-way delay added at each spine's egress ports, ms. Two fast and two slow
# spines (CHUNKS.md section B); s3/s4 give delay gaps of 4 and 9 ms to s1.
DEFAULT_SPINE_DELAY_MS = (1, 1, 5, 10)


def host_ip(n):
    return "10.0.0.%d" % n


def host_mac(n):
    return "00:00:00:00:00:%02x" % n


def layout(hosts_per_leaf=2):
    """Plain-data description of the topology, shared with the controller."""
    hosts = []
    for j in range(1, N_LEAVES + 1):
        for i in range(1, hosts_per_leaf + 1):
            n = (j - 1) * hosts_per_leaf + i
            hosts.append(dict(name="h%d" % n, ip=host_ip(n), mac=host_mac(n),
                              leaf=j, port=N_SPINES + i))
    return dict(
        n_spines=N_SPINES, n_leaves=N_LEAVES, hosts=hosts,
        leaf_dpid={j: 0x100 + j for j in range(1, N_LEAVES + 1)},
        spine_dpid={k: 0x200 + k for k in range(1, N_SPINES + 1)},
    )
