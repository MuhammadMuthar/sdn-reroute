# LOG (lab notebook, append only)

## 2026-10-19 Chunk 1: frozen environment

Run in a cloud Ubuntu 24.04.5 container (Python 3.13.16, root, kernel
6.18.44 without loadable modules) because the laptop Claude Code session had
no credits. Laptop facts relayed from a later Remote Control session: Ubuntu
26.04.1, kernel 7.0.0-38, Python 3.14.4 (python3.11 also present), 8 cores,
14 GB RAM, openvswitch kernel module available, apt has mininet 2.3.0,
openvswitch-switch 3.7.1, iperf3 3.20, tshark 4.6.6, python3-os-ken 4.1.1.

### Decision: os-ken 4.2.2 in a venv, with our own launcher

Options considered:
1. Ryu 4.34 in a Python 3.9 venv with eventlet 0.30.2. Rejected: the laptop has
   no 3.9, pyenv adds a toolchain build, Ryu is unmaintained since 2020, and
   docs/research_gap.md cites reports that the eventlet pin does not fix 3.10+.
2. Distro `python3-os-ken` 4.1.1. Rejected: ties us to the system Python,
   cannot be pinned in requirements.txt, and also has no manager CLI.
3. `pip install os-ken==4.2.2` (Aug 2026, classifiers 3.10-3.13). Chosen.

Finding while testing: os-ken 4.0.0 and later ship no `osken-manager` console
script and no `os_ken.cmd` package (checked the wheels for 3.1.1, 4.0.0, 4.1.2,
4.2.2; only 3.1.1 has them). The library part (app_manager, controller,
ofproto, packet) is unchanged. So `controllers/run.py` reimplements the ~30
lines of the CLI (hub.patch, cfg.CONF, AppManager.load_apps / create_contexts /
instantiate_apps, hub.joinall). It accepts the usual flags.

Both stacks were exercised with `scripts/fake_switch.py` (handshake, table-miss
flow, flood, learn, unicast flow): PASS with os-ken 4.2.2 + run.py and with
os-ken 3.1.1 + osken-manager, both on Python 3.13.16.

### Mininet in the container

- `apt install mininet openvswitch-switch iperf3 iperf ethtool iputils-ping
  telnet` worked. `ovs-ctl start` works without the kernel module; Mininet
  switches must then use `OVSSwitch(datapath='user')`. The kernel-datapath run
  hangs at bridge creation ("failed to create datapath ovs-system").
- First pingall showed 100 % loss: `ping` was not installed in the container.
  Not a networking fault. Added iputils-ping to setup.sh.
- Mininet's built-in `--test iperf` hung: it waits for `telnet` to confirm the
  server is listening. Added telnet to setup.sh; our smoke test uses iperf3
  with its own retry loop instead.
- TCP did not connect at all although ICMP did: flows showed h1->h2 SYNs
  forwarded (12 packets) and nothing back from h2. Cause: tx checksum offload
  on the host veth with the userspace datapath. `ethtool -K <intf> tx off rx
  off tso off gso off gro off` on every host and switch port fixed it. We wanted
  offloads off anyway for captures (CLAUDE.md pitfalls), so smoke.py now does
  it for all ports at start; later topologies should reuse `disable_offloads`.
- Available qdiscs here: htb, tbf, pfifo. Missing: netem, fq_codel. So TCLink
  bandwidth works in the container, delay does not. Chunk 2 cannot run here.

### Finish line

`sudo ./scripts/verify.sh` (controller from the venv, smoke topology, pingall,
10 s idle iperf3 on 10 Mbps TCLinks), cloud container, userspace datapath:
pingall 0 % loss, iperf3 9.53 Mbps = 95.3 % of 10 Mbps. Row in
results/verify/vm.csv, logs beside it, env snapshot in results/env/. An earlier
manual run of the same script gave 9.42 Mbps (94.2 %). Pass threshold in the
script is 90 % so a slower laptop does not fail on noise; the number is always
printed and logged.

The laptop run (kernel datapath, real sysctls, netem present) is still owed.

## 2026-10-19 Chunk 1 (laptop): native run on the HP EliteBook

Remote Control session on the laptop itself. Machine: Ubuntu 26.04.1 LTS,
kernel 7.0.0-38-generic, Intel i5-1145G7 (8 threads), 15.5 GB RAM,
Python 3.14.4. Snapshot: `results/env/muhammad-muthar-HP-EliteBook-840-G8-Notebook-PC-2026-10-19T171202.txt`.

### What was run

- System packages: Mutahar ran by hand (I have no sudo password)
  `sudo apt-get install -y mininet openvswitch-switch openvswitch-testcontroller iperf3 tshark python3-os-ken`
  before the repo existed. Afterwards every package in `setup.sh`'s APT_PKGS
  was present (`dpkg -s`), the openvswitch kernel module was loaded and
  `openvswitch-switch` active, so `sudo ./setup.sh system` itself was not run
  on the laptop. python3-os-ken from that command is unused (the venv has its
  own os-ken); harmless.
- `./setup.sh venv` on Python 3.14.4: passed first try in 70 s. All pins in
  requirements.txt have cp314 wheels; no python3.11 fallback needed.
- `scripts/fake_switch.py 127.0.0.1 6655` against `controllers/run.py` +
  `learning_switch.py`: PASS on 3.14.
- `sudo ./scripts/verify.sh` (run by Mutahar, kernel datapath):
  pingall 0 % loss, iperf3 9.452 Mbps = 94.5 % of 10 Mbps, SMOKE PASS.
  Row in `results/verify/muhammad-muthar-HP-EliteBook-840-G8-Notebook-PC.csv`.
- sysinfo confirms netem, htb, tbf and fq_codel all work here, so Chunk 2's
  unequal path delays can run on this machine.

### Fixes

- `scripts/sysinfo.sh` dumped `net.ipv4.tcp_fastopen_key`, a per-boot secret,
  into results/env (the repo is public). It now skips that key. The one laptop
  snapshot already written had the value replaced with a `<redacted ...>`
  marker; nothing else in it was touched. Also silenced tshark's
  "running as root" warning that replaced its version line.
- `scripts/verify.sh` ran as root, so its results/ files came out root-owned
  and git/analysis could not edit them as the user. It now chowns
  results/verify and results/env back to `$SUDO_USER` on exit. (Today's files
  were re-owned by copying them within the user-owned directory.)
- `setup.sh` needed no change on Ubuntu 26.04 / Python 3.14.

### Not done

- verify.sh was run once on the laptop (plus the cloud run). The fixed
  verify.sh/sysinfo.sh have only been syntax-checked and run unprivileged,
  not re-run under sudo.

### Re-run after the fixes (2026-10-19T17:33)

`sudo ./scripts/verify.sh` again: pingall 0 % loss, iperf3 9.512 Mbps =
95.1 % of 10 Mbps, SMOKE PASS (second row in the laptop CSV). The new env
snapshot has no tcp_fastopen_key line. Result files came out owned by
muhammad-muthar but with group root: Ubuntu 26.04's `chown` is the Rust
uutils one (/usr/lib/cargo/bin/coreutils/chown), which does not give
`user:` the GNU meaning "user's login group". verify.sh now passes the group
explicitly (`$OWNER:$(id -gn $OWNER)`). Not re-run under sudo after that edit.

## 2026-10-19 Chunk 2: the reordering gate

Remote Control session on the laptop. Plan: CHUNKS.md section B (approved).

### Running without sudo: user namespaces

I have no sudo password. Unprivileged user namespaces work on this laptop
(`unshare --user --map-root-user --net ...`), and inside one the namespace's
"root" can create veths, netem/htb qdiscs, and, unexpectedly, kernel-datapath
OVS bridges (the openvswitch module checks CAP_NET_ADMIN against the network
namespace's owner). `scripts/userns.sh` wraps a command in fresh user, net,
pid and mount namespaces with a private /tmp and a private ovsdb-server and
ovs-vswitchd, then Mininet runs unchanged. Check: `topologies/smoke.py` inside
it gave pingall 0 % and iperf3 9.44 Mbps (sudo run on the same laptop: 9.45).
Same kernel TCP, same qdiscs, same OVS kernel datapath; every trial gets brand
new namespaces and an empty OVS database, which is a stronger reset than
`mn -c`. Under sudo the runner instead runs on the system OVS with `mn -c`
before each trial. A short sudo cross-check is planned at the end.

Two AppArmor surprises from running under the desktop app's confinement
profile: tcpdump refuses signals from us (fixed by letting it stop itself with
`-G/-W 1`) and tshark cannot open files under this repo's path (fixed by
feeding the pcap on stdin). Neither affects a run from a normal terminal.

### Design decisions (before any gate run)

- Topology `topologies/leafspine.py`: 2 leaves, 4 spines, 2 hosts per leaf.
  Every switch egress towards a host or a spine is HTB 10 Mbit/s with a pfifo
  of 20 packets; spine egress ports carry only netem delay (limit 100, no
  rate). Delays one way: s1 = s2 = 1 ms, s3 = 5 ms, s4 = 10 ms (gaps 4 and
  9 ms to s1). Host NICs are not shaped, so the flow's queue sits on the leaf
  uplink of its current path, as in a real fabric, and not inside the sending
  host where TCP small queues would hide it. Consequence: at the moment of a
  move the old path still holds the flow's standing queue, so even fast->slow
  and fast->fast moves can reorder. That is real rerouting behaviour, and R0
  (fast->fast, no delay gap) isolates it.
- Queue 20 packets = about 2x BDP of the slowest path (10 Mbit/s x ~12 ms RTT
  is ~10 packets), per CHUNKS.md B. Same everywhere.
- Controller `controllers/gate_ctl.py`: proactive rules, spines and egress
  leaf forward by destination MAC, the ingress leaf has a 5-tuple rule for the
  elephant. Reroute = one OFPFC_MODIFY_STRICT on l1, timed at the barrier
  reply. Control runs send the same flow-mod with the unchanged port (sham
  move). ACKs always return via s1.
- Flow: iperf3 h1 -> h3, cubic (kernel default), 20 s, fixed --cport 40001,
  move at t = 10 s. tcp_metrics flushed in both hosts before each trial; the
  sender namespace's sysctls are stored in every trial's meta.json.

### Pre-registered pass criterion (written before the first gate run)

Disclosure: three short debugging trials (8 s flows, condition R2) were run
before this entry to get the runner working (results/gate/dev-pilot1..3, not
committed and not analysed). The last one completed: 11 retransmissions, 9
DSACKs and 19 out-of-order segments queued at the receiver in the 2 s after
the move, and no visible dip in 100 ms goodput beyond one bin. Nothing below
was changed because of it; the criterion is the one in CHUNKS.md B made
operational.

Conditions, 10 repetitions each, block-randomised order (seed 20261019):
R1 s3->s1 and R2 s4->s1 (slow->fast, gaps 4 and 9 ms), R3 s1->s3 and R4
s1->s4 (fast->slow), R0 s1->s2 (fast->fast); controls C1, C3, C4 stay on
s1, s3, s4 with a sham move. Each reroute is compared with the control on
its destination path (R1, R2, R0 vs C1; R3 vs C3; R4 vs C4).

Window W = [t_move, t_move + 2 s), t_move = barrier reply of the flow-mod.

(a) Reorder/spurious event in a trial: the sender's counter deltas over W
    (snapshots just before the flow-mod and 2 s after the barrier reply) of
    TcpExtTCPDSACKRecv + TcpExtTCPSACKReorder + TcpExtTCPTSReorder +
    TcpExtTCPRenoReorder + TcpExtTCPSpuriousRTOs sum to >= 1. (This kernel
    has no separate TCPRACKReorder counter; RACK reordering is counted in
    TCPSACKReorder.) A condition meets (a) if >= 6 of its 10 trials have an
    event.
(b) Goodput G = in-order bytes delivered in W, from the receiver's
    cumulative ACK number in the receiver-side capture, in Mbit/s. Deficit =
    mean G(control on destination path) - mean G(reroute); a condition meets
    (b) if the Welch 95 % CI of the deficit lies entirely above zero.

Verdict: PASS if R1 and R2 both meet (a) and (b). R0, R3 and R4 are reported
but not required (no delay-induced reordering is expected fast->slow).
Anything else is FAIL, and I stop and report without changing the setup,
the buffer or the kernel. Whatever the verdict, the deficit is reported in
bytes and as a fraction of the control's 2 s volume so that "significant but
tiny" is visible; the decision what to do about a tiny effect is Mutahar's.
Secondary, not part of the verdict: tshark tcp.analysis flags in W,
retransmissions and reorder counts from the packet traces, the within-trial
change G(W) - G(2 s before), cwnd from `ss -tin`.

### Campaign run (2026-10-19, 17:41 to 18:33)

`python3 experiments/gate.py campaign --out results/gate/gate-2026-10-19 --reps 10`,
rootless (user namespaces). 80 of 80 trials ok, none skipped. The session that
started it was cut off ~4 min in; the campaign kept running and a new session
picked it up, so nothing was restarted or reused. One edit landed in
experiments/gate.py during the run (17:44, moving the sudo-only chown after
postprocess); it is in the campaign driver, which was already loaded, and
never runs rootless, so all 80 trials ran the same trial code.

### Result: PASS on the pre-registered criterion, but the effect is small

From `results/gate/gate-2026-10-19/verdict.md` (analysis/gate.py):

- (a) Reorder/spurious events in W: R1 10/10, R2 10/10, R0 10/10, R3 10/10,
  R4 2/10; controls C1, C3, C4 0/30. The events are caused by the move.
- (b) Goodput deficit in W vs the destination-path control (Welch 95 % CI):
  R1 0.028 Mbit/s [0.015, 0.040] = 6.9 KB, 0.29 % of the 2 s volume;
  R2 0.268 [0.008, 0.529] = 67 KB, 2.8 %. Both exclude zero, so PASS.
  R0 7.5 KB, R3 5.0 KB, R4 1.1 KB (all also above zero).
- Retransmissions in W are almost all spurious: retransmitted segments ≈
  DSACKs received ≈ duplicate arrivals in the receiver trace (R1 5.2/5.0/5.0,
  R2 8.6/6.3/6.3, R0 5.2/5.2/5.2).
- R2 is bimodal (post-hoc table in verdict.md, not part of the verdict):
  3 of 10 R2 trials hit an RTO in W and lose ~0.8 Mbit/s over the window;
  the other 7 lose 10.8 KB [7.5, 14.0]. R2's 67 KB mean and its wide CI come
  from those three timeouts.
- cwnd: slow->fast moves cut the sender's cwnd (R1 35 -> 28, R2 43 -> 22
  segments, median before vs minimum in W) and the kernel never undid the cut
  (TCPDSACKUndo and TCPLossUndo are 0 in every trial), yet goodput barely
  moves. Inference, not measured: with one flow on an idle 10 Mbit/s path and
  a 20-packet buffer, cwnd stays far above the 1 ms path's BDP after the cut,
  so the cut only drains the standing queue. Under competing traffic the same
  cut would cost throughput share; this setup cannot show that.
- R0 (fast->fast, no delay gap) reorders 10/10, as predicted in the design
  notes: the standing queue on the old uplink is left behind at the move.

Not done: the sudo cross-check (`sudo ./scripts/chunk2_sudo.sh`, R2 and C1 x3
on the system OVS) needs Mutahar. Stopping here per the gate rule because the
damage is small; whether that is enough to build on is Mutahar's call.

### Gate decision

Recorded as PASSED with a caveat: the criterion is met, but the damage of a
single move on an idle path is small (0.3 % at 4 ms, 2.8 % at 9 ms, mostly
from 3 RTO trials). The project recommendation, open to Mutahar's override,
is to go to Chunk 3 rather than extend the gate. The mechanism to test under
load there: spurious retransmissions after a slow->fast move trigger a cwnd
cut that the kernel never undoes; on an idle path it only drains the standing
queue, but with competing flows it should cost throughput share. Chunk 2
closes after the sudo cross-check (`sudo ./scripts/chunk2_sudo.sh`).

### Sudo cross-check (Mutahar ran `sudo ./scripts/chunk2_sudo.sh`, 18:41)

- verify.sh: SMOKE PASS, pingall 0 %, iperf3 9.54 Mbit/s (95.4 %), row
  appended to results/verify/...csv. The chown fix works: every file it
  wrote is owned by muhammad-muthar:muhammad-muthar.
- Gate cross-check as root on the system OVS (no user namespaces), R2 and C1,
  3 trials each, `results/gate/sudo-check-2026-10-19/verdict.md`. It agrees
  with the rootless campaign: R2 events 3/3 (rootless 10/10), C1 0/3
  (0/10); C1 goodput in W 9.557 Mbit/s (9.565); R2 cwnd 44 -> 19 (43 -> 22);
  R2 RTO in 1 of 3 trials (3 of 10); deficit in R2 trials without an RTO
  13.0 KB (rootless 10.8 KB [7.5, 14.0]). n = 3 is too small for its own CI,
  so this is a consistency check, not a second gate.
- analysis/gate.py printed "FAIL" for the 3-trial subset because R1 is
  missing; it now says "not applicable" unless R1 and R2 both have 10
  trials. The gate-2026-10-19 verdict is unchanged (PASS).

Chunk 2 closed. Next: Chunk 3.

## 2026-10-19 Chunk 3: ECMP baseline and the reusable runner

Remote Control session on the laptop, Opus 5.5. Plan: CHUNKS.md section B,
Chunk 3, plus the carry-over from Chunk 2: measure what the slow->fast cwnd
cut costs when the new path already carries traffic.

### Design

- `controllers/fabric.py`: shared base for every controller from now on
  (static spine/egress-leaf forwarding by destination MAC, barrier handling,
  the JSON command channel with pin/move/status/events). `gate_ctl.py` is left
  untouched so Chunk 2 reruns exactly. Gotcha found on the way: an os-ken app
  must not set `self.events`; OSKenApp uses that name for its event queue and
  overwriting it silently stops all event handling (no error anywhere).
- `controllers/ecmp.py`: one OF1.3 select group per leaf, 4 equal buckets
  (one per spine). OF1.3 cannot name a selection method, so OVS 3.7.1 uses its
  default, dp_hash with the kernel's symmetric L3/L4 hash (ovs-ofctl(8)). That
  hash is per 5-tuple and deterministic within a boot, but seeded randomly at
  boot, so the runner records the spine every flow actually used instead of
  predicting it. Symmetric: ACKs come back over the same spine as the data.
- `experiments/traffic.py`: scenarios as plain data and a seeded schedule
  (`random.Random(seed)` only). `ecmp_mixed`: 4 iperf3 elephants h1..h4 ->
  h5..h8 for 30 s with seeded --cport (so the seed decides the collision
  pattern), Poisson mice at 10/s from each of h1..h4 to a random h5..h8,
  bounded-Pareto sizes (alpha 1.2, 2 KB to 1 MB, mean 8.5 KB, about 2.6 Mbit/s
  offered in total), fixed seeded source ports. `loaded_move`: see below.
- `experiments/mice.py`: stdlib client/server; FCT = connect() to the
  server's 1-byte reply after the half-close.
- `experiments/runner.py`: one run = fresh namespaces + controller +
  schedule; nstat-style counters of every host, ss -tinm of elephant sockets,
  header captures on the four uplinks of l1 (path oracle and link utilisation,
  `experiments/spinetrace.py`), full traces of "tagged" elephants, group /
  flow / port stats, controller pin/move log. Rootless via userns.sh as in
  Chunk 2, or under sudo with `mn -c`. `experiments/metrics.py` (venv) turns
  each run into run_flows.csv / run_links.csv / run_summary.json and rebuilds
  the campaign's runs.csv, flows.csv and links.csv.
- Switch-port captures stop themselves like in Chunk 2 (-G/-W 1); the last
  packet is a raw UDP/9 frame the runner injects on each uplink.

Dev pilots (not committed, not analysed): results/ecmp/dev-pilot1..3 and
results/loaded/dev-pilot1..2, used to debug the runner. Two things they
showed are recorded here because they shaped the design: the same seed gave
the same spine for 1054 of 1054 flows across two runs, and the TSQ problem
below.

### Methods finding: TCP Small Queues counts the emulated switch queue

With two elephants sharing s1 (dev-pilot1, LC1/LR2), neither flow ever lost a
packet in 10 s, cwnd stayed frozen (25 and 16 segments), and the split was a
stable 6.4 / 3.2 Mbit/s that depended on history, not on congestion control.
`ss -tim` showed why: packets in flight (6 to 12) far below cwnd, and the
sender's sk_wmem_alloc ("skmem t") at 12 to 19 KB while those packets sat in
the l1-s1 queue. The skbs stay charged to the sending socket through the
veth into OVS and the HTB queue until the spine's netem orphans them
(netem_enqueue calls skb_orphan_partial). TCP Small Queues limits a socket to
about max(2 x truesize, pacing_rate >> 10) bytes below the TCP layer, so here
it caps each flow at a few packets *in the switch queue*: the 20-packet
bottleneck never fills, there is almost no loss, and cwnd does not decide
the rate. On a real host the NIC's TX completion frees the skb as soon as it
leaves the host, so TSQ only ever sees host-local queues. This is an
emulation artifact of veth + OVS in one kernel, and it likely explains the
Chunk 2 observation that a cwnd cut from 43 to 22 cost almost no goodput.

Fix: a 10 us netem on every host NIC (`tc qdisc replace dev hN-eth0 root
netem delay 0.01ms limit 10000`, topologies/leafspine.py
`host_qdisc="netem"`). netem orphans on enqueue, so the charge ends when the
packet leaves the host, as with a real NIC. The limit of 10000 packets is far
above any BDP here (about 25 packets on the slowest path at 10 Mbit/s), so
that qdisc never drops; it adds 10 us one way. With it: sk_wmem_alloc 0, in
flight == cwnd, the uplink drops (87 in 8 s), normal loss-based sharing (5.4 /
4.1 Mbit/s). Reproducible with `python3 experiments/tsq_check.py --out
results/tsq/<name>` (both variants back to back; results below).

Decision (Mutahar delegated it via the project coordinator, 14:13 UTC, on my
recommendation): every run from Chunk 3 on uses `host_qdisc=netem` (the
runner's default). The Chunk 2 gate stays as recorded (it ran with the
default qdisc; gate.py never passes the new argument). The gate's R2/C1 pair
is rerun inside the new runner under both settings (conditions IR2/IC1 of the
loaded-move campaign), and the whole loaded-move campaign is also run with
the default qdisc as an ablation.

### Loaded-move experiment: analysis plan (pre-registered before any campaign run)

Scenario `loaded_move` (experiments/traffic.py), ECMP controller with pins:
E0 = h1 -> h5 (moved), B = h2 -> h6 (competitor). Both start at t0, run 25 s;
the move is one MODIFY_STRICT at t0 + 10 s; ACKs of both pinned to s1 as in
the gate; no mice, so h1 carries only E0. Conditions:
IC1 E0 on s1, sham move (idle); IR2 E0 s4 -> s1 (idle, = gate R2);
LC1 E0 and B on s1, sham move; LR0 E0 s2 -> s1 with B on s1 (fast->fast,
no delay gap); LR2 E0 s4 -> s1 with B on s1 (slow->fast, 9 ms gap).
10 reps per condition, block-randomised order, `--seed 1`.

Goodput G = in-order bytes acknowledged by the receiver in a window
(receiver-side trace, as in the gate). Windows start at the move's barrier
reply: W2, W5, W10. Deficit = mean G(reference) - mean G(reroute), Welch
95 % CI. Contrasts (analysis/loaded_move.py):
- P1 (primary): E0 deficit in W10, LR2 vs LR0. This isolates the delay-gap
  effect (reordering, spurious retransmissions, cwnd cut) from the cost of
  joining a loaded path, which both conditions share. Prediction: deficit > 0;
  "supported" if the CI lies above zero.
- S1: E0 deficit LR2 vs LC1, LR0 vs LC1 (the cost of moving onto a loaded
  path at all) and IR2 vs IC1 (idle, gate replication), in W2, W5, W10; the
  same for B and for E0 + B, to tell lost capacity from a shift of share.
- S3: loaded minus idle slow->fast deficit, (LC1 - LR2) - (IC1 - IR2).
- Reported, not tested: reorder/spurious events and RTOs per run, cwnd
  before / minimum after / mean over W10, flow-mod latency.
Primary campaign with host_qdisc=netem; ablation with host_qdisc=default,
same plan. Anything else in the write-up is labelled post hoc.

### ECMP baseline: what the finish line checks

Descriptive, no hypothesis: `campaign --scenario ecmp_mixed --runs 10
--seed 1` (seeds 1..10). Checks: dump-group-stats bytes per bucket, mice per
spine against uniform, elephants per spine and the collision distribution,
no flow changes path, ACK path = data path; reproducibility by replaying
seeds 1..3 into a second campaign (same schedule sha256, same spine for every
flow); elephant goodput, mice FCT p50/p99 (also split by fast and slow spine
and by the number of elephants sharing the mouse's uplink, Chunk 0 review
1.5), max link utilisation, retransmissions.

### Campaigns run (2026-10-19, from 19:17 PKT; `scripts/chunk3_campaigns.sh`)

All rootless, one boot (boot_id b2163657-5325-447c-8649-05780f57dc59, up
since 14:22 PKT), host_qdisc=netem unless stated. In order: ECMP 10 runs,
ECMP replay of seeds 1-3, TSQ check, loaded move (50 runs), loaded move with
host_qdisc=default (ablation, 50 runs). Every run finished with status ok.

### Code review (independent agent, before any campaign result was read)

No bug that changes a number in the pilots. Fixed in the analysis before the
campaigns finished: ss cwnd series now keyed to the exact socket (an iperf3
control port could fall into the ss filter range); the receiver out-of-order
trace count no longer counts loss repairs; a loaded run with a missing B
trace becomes NaN, not B = 0; every loaded run is checked against the
uplink captures (E0, B and ACKs on the intended spines) and excluded
otherwise (none were); ECMP runs with a recorded error or a failed
elephant are excluded (none were). Fixed in the runner, controller and
metrics only after the campaigns (each run re-imports them, so a mid-campaign
edit would mix code versions): exact-port ss filter, hosts'
ip_local_port_range moved to 41000-60999 (out of the elephant and mice port
ranges), OFPT_ERROR replies make a pin/move report ok=false, a counter
snapshot right after the barrier, boot_id in meta, clean run dir on retry,
failed mice counted as FCT = infinity in percentiles (0 failures in these
campaigns, so no number changes).

### Result: ECMP baseline (results/ecmp/ecmp-2026-10-19/summary.md)

Finish line met: `runner.py campaign --scenario ecmp_mixed --runs 10 --seed 1`
wrote runs.csv / flows.csv / links.csv for 10 of 10 runs.
- Spreading: dump-group-stats bytes per bucket s1..s4 35 / 25 / 21 / 19 %
  (elephants dominate); mice per spine 2596 / 2617 / 2657 / 2662 (chi-square
  vs uniform p = 0.76); elephants per spine 16 / 10 / 8 / 6. Busiest uplink
  carried 1 / 2 / 3 elephants in 1 / 6 / 3 runs (uniform hashing: 9 / 70 /
  19 %). No flow ever used two spines; ACKs always on the data's spine.
- Reproducibility: replaying seeds 1-3 gave identical schedule sha256 (3/3),
  identical elephant placement (3/3) and the same spine for 3169 of 3169
  flows. Metrics replay closely (mean elephant goodput 4.46 vs 4.48, 4.42 vs
  4.41, 6.53 vs 6.60; FCT p50 identical to 0.1 ms; p99 950 vs 1090 ms for
  seed 1, the tail is noisy).
- Elephant goodput is set by collisions: alone on an uplink 8.70 Mbit/s
  (n=19), sharing with one other 4.42 (n=12), with two others 3.09 (n=9).
  Mean of 4 per run 6.16 ± 0.93, worst 4.23 ± 1.16, sum 24.6 ± 3.7 Mbit/s.
- Mice FCT p50 50.8 ± 5.8 ms, p99 1210 ± 100 ms; 2.3 % of mice lose their
  SYN or SYN-ACK and wait for the 1 s initial RTO, and those are 87 % of the
  mice slower than 0.9 s, so p99 is mostly that. FCT depends on the mouse's
  own uplink (p50 40 ms with no elephant there, 248 ms with three) and on
  its last hop, which every right-hand host shares with one elephant.
  Fast vs slow spines barely differ (p50 58.8 vs 52.0 ms), because elephant
  load, not propagation delay, dominates FCT here (Chunk 0 review 1.5).
- Max link utilisation is 1.0 in every run (some uplink is always full), so
  it will not separate controllers on this workload; elephant goodput and
  FCT will.

### Result: TSQ check (results/tsq/tsq-2026-10-19/tsq_check.md)

Default host qdisc: sk_wmem_alloc 14-29 KB while packets sit in the switch,
packets out / cwnd 0.5-0.67 (ss unacked), 27 uplink drops in 10 s, split 6.3 / 3.2 Mbit/s.
netem host qdisc: 0 bytes, packets out / cwnd 1.00 (median; above 1 during loss recovery), 118 drops, 4.7 / 4.8.

### Result: loaded move, host_qdisc=netem (results/loaded/loaded-2026-10-19/summary.md)

50 of 50 runs analysed, all passed the path check. The pre-registered primary
prediction is **not supported; the effect has the opposite sign**:
- P1, E0 in W10, LR2 vs LR0: deficit -0.637 Mbit/s [-0.955, -0.319]
  (-796 KB, -13 %). Moving E0 from the slow s4 onto the shared s1 left it with
  *more* goodput than moving it from the fast s2. W2 -1.51 [-2.07, -0.95],
  W5 -0.91 [-1.43, -0.40].
- The other flow pays: B in W10 LR2 4.15 vs LC1 5.00 Mbit/s (deficit 0.86
  [0.50, 1.22], 17 %); vs LR0 0.63 [0.31, 0.95].
- Total goodput on s1 barely changes: E0 + B in W10 LR2 vs LC1 -0.025 Mbit/s
  [-0.037, -0.014] (-0.27 %), W2 -1.4 %. A move onto a loaded path mostly
  shifts share between flows; it destroys little capacity.
- Mechanism (inference from the counters, not tested): E0 arrives from the
  10 ms path with a larger cwnd (25 vs 19 segments before the move), keeps a
  larger one afterwards (mean over W10 11.1 vs 9.8 for LR0, 9.3 for LC1)
  and so takes share from B. Reordering still happens (reorder/spurious
  events in W2 in 9/10 LR2 runs vs 4/10 LR0, 0/10 LC1; DSACKs 2.7 vs 1.0 vs
  0) and cwnd is still cut (25 -> 8), but the cut lands on a flow that
  starts ahead; no RTOs under load.
- Idle replication of the gate's R2 under the fix (IR2 vs IC1): W2 deficit
  0.103 Mbit/s [0.034, 0.173] = 26 KB, 1.1 % (gate, default qdisc: 67 KB,
  2.8 %); events 9/10; an RTO in W10 in 6/10 runs (gate 3/10 in W2).
- Joining a loaded path at all (LR0 vs LC1, E0) is not distinguishable from
  no move in W10: -0.25 [-0.62, 0.12].
This contradicts the expectation in STATUS ("with competing flows the cut
should cost throughput share"). Reported to Mutahar with a question on how
damage should be defined from Chunk 4 on (moved flow only, or also the flows
on the destination path and the total).

### Definition change: damage has three terms (decided 15:16 UTC)

Decision (project coordinator, on delegated authority, following this
thread's recommendation): from Chunk 4 on, the damage of a reroute =
goodput deficit of the moved flow + deficit of the flows already on the
destination path + lost total goodput on that path, each against its matched
no-move control, over a fixed horizon; C_hat gets the three terms, and the
moved-flow-only deficit stays as a reported column so the write-up can show
why that definition fails under load. Evidence: the loaded-move result above
(LR2 vs the no-move control LC1 over W10: moved flow -0.88 Mbit/s, i.e. it
gained, bystander +0.86, total -0.03). The moved
flow's cwnd at arrival relative to the flows on the new path (25 vs about 10
segments here) is recorded as a candidate feature for the Chunk 5 dataset.
CHUNKS.md section B now carries this (amendment after Chunk 3; section A
untouched).

### Independent recomputation (second agent, own code, raw data only)

Every number above in the ECMP, loaded-move and TSQ results was recomputed
from the raw per-run files with independently written scripts (receiver ACK
traces, spine_flows.csv, iperf3 JSON, mice CSVs, counters.json,
tsq_check.json): all CONFIRMED to the reported precision, including the P1
CI, the path checks (50/50), mice per spine and the replay's 3169/3169
flows. Notes it raised: tsq_check.md's "in flight" is ss `unacked`
(relabelled "packets out"; 15 of 52 netem samples exceed 1.2 x cwnd during
loss recovery, the median ratio is 1.00); samples are about 0.23 s apart,
not 0.2 s (text fixed; report regenerated with `--report-only`); each trace
ends with the receiver's RST at about 25 s, outside every window; under load
E0's first packet appears on s1 15-33 ms after the barrier (queueing ahead of
the capture point), 1-2 ms when idle.

### Result: loaded-move ablation, host_qdisc=default (results/loaded/loaded-default-2026-10-19/summary.md)

Same plan, same code, the Chunk 2 host qdisc (TSQ artifact present). 50 of
50 runs, all passed the path check. The primary effect **flips sign**:
- P1, E0 in W10, LR2 vs LR0: deficit +2.456 Mbit/s [1.768, 3.144] (3.07 MB,
  47 %). LR2's E0 settles at 2.79 Mbit/s against B's 6.79, LR0's at 5.25.
- Almost nothing is lost or retransmitted under load (E0 retrans in W10:
  LR2 0.3, LR0 3.5, LC1 0); cwnd never moves (LR2 38 -> 38, LC1 17.5); the
  split is fixed within milliseconds of the move and then stays flat, and in
  the no-move control it depends on the run (LC1 E0 4.54 ± 1.29 Mbit/s). The
  shares are set by TSQ and pacing, not by congestion control.
- Idle (IR2 vs IC1), which is what the gate measured: W2 deficit 0.319
  [0.061, 0.578] = 80 KB, 3.3 %, events 10/10, an RTO in 4/10; the gate's R2
  (gate.py, same qdisc) was 67 KB, 2.8 %, 3/10 RTOs. So the new runner
  reproduces the gate under the old setting.
- Total goodput on s1 again barely changes (LR2 vs LC1 W10 -0.17 %).

Reading: with the artifact, a Mininet study would conclude that moving an
elephant from a slow path onto a shared fast path costs it about half its
throughput; on the corrected setup the same move makes it *gain* 13 % over a
no-gap move, at the bystander's expense. The single-flow idle numbers move
much less (2.8-3.3 % vs 1.1 %). The artifact matters most exactly where the
project's question lives, on loaded paths. This and the TSQ check are the
methods result of Chunk 3.

### Runner fixes applied after the campaigns, smoke-tested

`scratch/post_campaign_patch.py` (not committed; the changes are in the diff)
applied the staged review fixes. Smoke test: one LR2 and one ecmp_mixed run
(results/*/dev-postfix, not committed): status ok, every pin/move ok=true
under the OFPT_ERROR check, boot_id and ip_local_port_range (41000-60999)
recorded, iperf3 control ports now 43334-60008 (outside both flow port
ranges), the ss log holds only the elephant's socket, the new m0_post0
snapshot exists, seed 1 placement unchanged (E1:s2 E2:s1 E3:s2 E4:s2). One
bug found by the smoke test and fixed: the censored-FCT code failed on runs
without mice. All four campaigns' per-run metrics and CSVs were regenerated
with the final metrics.py; the ECMP runs.csv files came out byte-identical.

## 2026-10-19 Chunk 4 (part 1, stopped at the usage limit): controllers and runner

Remote Control session on the laptop, Opus 5.5. Stopped before any campaign;
nothing below is a result, the pilot numbers are dev runs (results/*/dev-*,
not committed).

### Built
- `controllers/hedera.py`: Hedera-style greedy on top of ECMP (docstring has
  the full spec and what was dropped from Hedera). Monitoring: a prio-20 leaf
  rule copies every SYN (tcp_flags=+syn-ack) to the controller while it still
  goes through the select group; on that packet-in a prio-150 5-tuple counting
  rule with the same group action (idle 5 s); spine counting rules once a flow
  passes 1 Mbit/s (they tell which dp_hash bucket it got); flow + port stats
  every 1 s, rates from duration deltas, path load = leaf uplink tx rate.
  Policy per round, most loaded path first: q* = least-loaded other uplink,
  gain = U_p - (U_q* + r_f); greedy moves if gain > 0; loads updated within
  the round. `controllers/cooldown.py` (gain > 1 Mbit/s and no move of that
  flow in the last 5 s; never-moved flows eligible at once) and
  `controllers/ecmp_mon.py` (same monitoring, never moves) are flag subclasses.
  Every move is in path_log (origin policy) and in policy_log.jsonl with its
  pre-move features. These parameters are the proposal to pre-register.
- `experiments/runner.py`: `campaign --controllers a,b,c` (block-randomised
  per seed; one controller keeps Chunk 3's order, checked against
  loaded-2026-10-19/plan.json), moves with the same time run as one batch,
  `replay --spec` mode, the controller gets FABRIC_LOG_DIR, boot_id in
  plan.json, shutdown timing steps.
- `experiments/moves.py` (reroutes and flaps: A -> B -> A within 10 s),
  `experiments/metrics.py` (reroutes/min, flaps/min, reorder events, FCT by
  size class, steady-window goodput, controller timing).
- `experiments/replay.py`: for every policy round that moved an elephant, a
  move run T (pre-move placement pinned, same flow-mods at the same time) and
  a matched no-move run C (post-move placement pinned, sham flow-mods), same
  seed, all elephants tagged. Damage terms come from C minus T.

### Pilot observations (dev runs, same boot as Chunk 3)
- SYN and data of every flow on the same spine (1058 / 1058); the controller's
  view of each moved elephant's path matched the uplink captures.
- ecmp_mon seed 2 reproduced Chunk 3 ECMP seed 2: same placement, goodput
  4.40 vs 4.42 Mbit/s, FCT p50 41.4 vs 40.8 ms.
- hedera seed 1: 3 moves in 2 rounds (1.8 s, 2.8 s), one elephant per uplink
  after that, mean / worst elephant goodput 8.49 / 8.34 (ECMP 4.46 / 2.83),
  but mice FCT p50 69 vs 41 ms, because no uplink is left elephant-free. One
  of the moves took a lone elephant off s1 only because s1's mice made it
  look busier than empty s3 (gain 1.4 Mbit/s); cooldown seed 2: 2 moves, 1 round.
- OVS updates OpenFlow rule counters from the datapath about every 0.5 s, so
  1 s per-flow rates have sd 0.4-0.7 Mbit/s in steady state and spike up to
  14 Mbit/s right after a move; port counters are exact. Kept OVS defaults.
- Runs take about 89 s instead of 68 s; the new runner steps will show where.

### Open point for the write-up
"moved + bystanders + lost total on the destination path" counts the moved
flow and the bystanders twice if summed, because the path total already
contains them. Proposal: report the three terms, and use the destination-path
total (moved + bystanders) as the single damage number.

### Next session (in order)
1. Pre-register the analysis plan here; independent code review.
2. `python3 experiments/runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown --scenario ecmp_mixed --runs 10 --seed 1 --out results/compare/compare-<date>` (about 1 h).
3. `python3 experiments/replay.py --live results/compare/compare-<date> --out results/damage/damage-<date>/jobs.json`, then `runner.py replay --spec ... --out results/damage/damage-<date>` (about 2 runs per event).
4. Write `analysis/stage2.py` (not started), figures, independent recomputation, STATUS.md.

## 2026-10-19 Chunks 5-6 cloud prep (stopped at the project credit limit)

Cloud thread, no Mininet, branch claude/chunk5-6-prep, PR #7. Code only, no
results. Done: `controllers/cost.py` (DamageEstimator interface: FormulaCost
with fixed structure and zero coefficients until fitted, ModelCost loading a
joblib model, `decide_move` with gain and cost in bytes over a 10 s horizon
plus the 5 s cooldown; `tests/test_cost.py`), `analysis/damage_dataset.py`
(one row per moved elephant: controller features from policy_log.jsonl,
host-side cwnd/unacked/rtt and cwnd asymmetry from the live ss log, labels
moved / bystander / destination-path total deficit over W2/W5/W10 from the
matched T/C replay pair, nstat deltas, data dictionary, Spearman table;
`tests/fixture.py` + `tests/test_damage_dataset.py`). Run tests with
`.venv/bin/python -m unittest discover tests`.
NOT done: analysis/damage_model.py (training, leave-seed-out CV, calibration),
controllers/costaware.py (wire decide_move into hedera._decide as policy
"cost"), docs/stage2_report.md skeleton, README section. Laptop still owes
the Chunk 4 campaign, the damage replays, then dataset -> model -> 5-way run.
