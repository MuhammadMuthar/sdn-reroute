# Learning the Cost of Rerouting

Disruption-aware elephant-flow load balancing in an SDN controller, measured.
When a controller moves a long TCP flow to a less-loaded path, the move itself
causes reordering, spurious retransmissions and cwnd cuts. We measure that damage
per move, learn to predict it from controller-visible statistics, and compare a
damage-aware controller against ECMP and a Hedera-style controller in Mininet.

Mutahar and Uswa Fatima, NUST SEECS, EE-353 Computer Networks (instructor Ali Hassan).
Standing brief: [`CLAUDE.md`](CLAUDE.md). Current state: [`STATUS.md`](STATUS.md).
Lab notebook: [`LOG.md`](LOG.md). Plan: [`docs/CHUNKS.md`](docs/CHUNKS.md) and
[`docs/CHUNK0_REVIEW.md`](docs/CHUNK0_REVIEW.md). Proposal, course handout and the
prior-work analysis are in [`docs/`](docs/).

## Install (Ubuntu 22.04 to 26.04, native, needs sudo for Mininet and Open vSwitch)

```bash
git clone https://github.com/MuhammadMuthar/sdn-reroute && cd sdn-reroute
sudo ./setup.sh system      # apt: mininet, openvswitch, iperf3, tshark, ethtool ...
./setup.sh venv             # .venv with pinned os-ken 4.2.2 + analysis stack (no sudo)
sudo ./scripts/verify.sh    # finish line of Chunk 1, see below
```

`setup.sh venv` needs Python 3.10 or newer; on a machine whose default `python3`
is unsuitable run `PYTHON=python3.11 ./setup.sh venv`.

## Verify the environment (Chunk 1 finish line)

`sudo ./scripts/verify.sh` starts the learning-switch controller, builds
`h1 - s1 - s2 - h2` with 10 Mbps TCLinks, runs `pingall`, then one idle 10 s
iperf3 TCP flow, and prints:

```
RESULT pingall_loss_pct=0
RESULT iperf3_mbps=9.53 configured_mbps=10 fraction=0.953
SMOKE PASS (pingall loss 0%, iperf3 95% of 10 Mbps, pass >= 90%)
```

It also writes `results/env/<host>-<time>.txt` (OS, kernel, versions, available
qdiscs, all `net.ipv4.tcp_*` sysctls) and appends a row to
`results/verify/<host>.csv`. Commit those files: they are the evidence.

## Controller stack: os-ken instead of Ryu

Upstream Ryu 4.34 (2020) breaks on Python 3.10+. We use
[os-ken](https://opendev.org/openstack/os-ken), OpenStack's maintained fork, which
has the same API with `ryu` renamed to `os_ken`. os-ken 4.x removed the
`osken-manager` command, so the launcher is ours:

```bash
.venv/bin/python controllers/run.py controllers/learning_switch.py
# any ryu-manager/osken-manager flag works, e.g. --verbose --ofp-tcp-listen-port 6653
```

`scripts/fake_switch.py` checks the controller logic without Mininet or root: it
connects as a fake OpenFlow 1.3 switch, completes the handshake, sends packet-ins
and asserts the learning switch floods, learns and installs the unicast flow.
The reasoning behind the choice is in `LOG.md` (2026-10-19).

## The reordering gate (Chunk 2)

Does moving one long TCP flow between paths with different delays damage it?
Topology `topologies/leafspine.py`: 2 leaves, 4 spines (one-way delay 1, 1, 5,
10 ms at the spine egress), every switch egress to a host or spine shaped to
10 Mbit/s with a 20-packet pfifo. Controller `controllers/gate_ctl.py` pins the
flow with a 5-tuple rule at the ingress leaf; a reroute is one
`OFPFC_MODIFY_STRICT` there. Runner and analysis:

```bash
python3 experiments/gate.py campaign --out results/gate/<name> --reps 10   # ~55 min, 80 trials
.venv/bin/python analysis/gate.py results/gate/<name>                     # table + figure
```

As a normal user the runner puts every trial in fresh user/net/pid/mount
namespaces with a private Open vSwitch (`scripts/userns.sh`), so no sudo is
needed where unprivileged user namespaces are allowed; under `sudo` it uses the
system OVS and `mn -c`. Each trial keeps kernel TCP counters (nstat) at five
points, `ss -tin` every 50 ms, iperf3 JSON, tc and OVS stats, and compact
packet traces of both ends (`*_trace.csv.gz`; the pcaps are not committed).
The pass criterion was written in `LOG.md` before the first run; the result
is in `results/gate/<name>/verdict.md` and `figures/gate_timeseries.png`.

## Layout

```
controllers/   os-ken apps: run.py (launcher), learning_switch.py; later ecmp.py, hedera.py, costaware.py
topologies/    Mininet topologies: smoke.py now; leafspine.py, fattree.py later
experiments/   runners that launch topology + controller + traffic + capture
analysis/      parsing captures, statistics, plots, model training
results/       raw outputs (CSV, logs, env snapshots), never edited by hand
figures/       generated plots
scripts/       setup helpers: verify.sh, sysinfo.sh, fake_switch.py
docs/          proposal.pdf, handout.pdf, research_gap.md, CHUNKS.md, CHUNK0_REVIEW.md
```

## Rules we hold ourselves to

Every number and figure comes from a script in this repo that anyone can rerun.
Raw data in `results/` is never edited. Negative results are reported. Nothing is
claimed as new that Hermes (SIGCOMM'17) or FlexDATE (ToN'22) already did; see
`docs/research_gap.md`.
