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

## 2026-10-19 Chunk 4 (part 2, cloud): pre-registered analysis plan and analysis/chunk4.py

Cloud session (no Mininet), while the laptop is paused. Written before the
comparison campaign or any replay has run; the only Chunk 4 data that exist
are uncommitted dev pilots (results/*/dev-*, not read here). Nothing below is
a result.

### Pre-registered analysis plan for the Chunk 4 comparison (16:40 UTC)

Campaign: `runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown
--scenario ecmp_mixed --runs 10 --seed 1`, seeds 1..10, every controller on
every seed (paired), block-randomised order per seed, host_qdisc=netem, one
boot. Controller parameters as committed in controllers/hedera.py (poll 1 s,
elephant >= 1 Mbit/s = 10 % of the link, least-loaded other uplink, greedy
gain > 0; cooldown-only: gain > 1 Mbit/s and >= 5 s since that flow's last
move). These are frozen now; a change after seeing data is a new campaign.

Unit of analysis: the run (one seed x one controller), n = 10 per controller.
Exclusions, decided before looking at metrics: a run with a recorded error,
or with any elephant that did not finish (elephants_ok < n_elephants), is
dropped and listed; a seed missing for one controller drops that seed from
the paired contrasts only. Runs are never re-run to replace a bad one unless
the whole seed is re-run for all four controllers.

Per-run metrics (experiments/metrics.py, runs.csv), per controller as mean
± 95 % CI (t, n - 1 df, as analysis/ecmp.py and analysis/gate.py):
- Benefit: elephant goodput mean of 4 and worst of 4 (Mbit/s, whole run);
  mice FCT p50 and p99 (ms; failed mice count as infinite, so p99 cannot
  improve by timing mice out), also p50/p99 per size class (< 10 KB,
  10-100 KB, >= 100 KB), because the all-mice p99 is dominated by SYN loss
  (Chunk 3); max link utilisation reported but expected to be 1.0 everywhere.
- Rerouting behaviour: reroutes per run and per minute, reroute rounds,
  flaps per minute (A -> B -> A within 10 s), moves of non-elephant flows.
- Damage proxies, whole run: retransmitted segments (all senders), reorder /
  spurious events (DSACKRecv + SACKReorder + TSReorder + RenoReorder +
  SpuriousRTOs), RTOs.
- Controller cost: stats-collection time per round (p50, max).

Contrasts, paired by seed (difference per seed, mean with a 95 % t CI over
the 10 differences):
- Primary P1: Hedera-style minus ECMP, elephant goodput mean of 4.
  Prediction: > 0 (the dynamic baseline recovers the collision loss; the
  Chunk 3 ECMP mean was 6.16 Mbit/s with a ceiling near 8.7 when every
  elephant has its own uplink). "Supported" if the CI lies above zero.
- Primary P2: Hedera-style minus ECMP, mice FCT p50. Prediction: > 0 (worse),
  because Hedera spreads the elephants over every uplink and no mouse keeps an
  elephant-free path (pilot: 69 vs 41 ms, one seed; this is the hypothesis
  that pilot suggested, pre-registered here before the campaign).
- Secondary S1: monitor-only minus ECMP on goodput mean, FCT p50, FCT p99.
  Prediction: CIs include zero (the SYN copy and the counting rules do not
  change the result). If not, the Hedera and cooldown contrasts are reported
  against monitor-only as well, since that is their true baseline.
- Secondary S2: cooldown-only minus Hedera-style on reroutes per minute,
  flaps per minute and total reorder events. Prediction: <= 0 for all three.
  Cooldown-only minus Hedera-style on elephant goodput mean: reported, no
  prediction (the question is how much of the gain the cheap ablation keeps).
- Everything else in the tables is descriptive.

Per-reroute damage (experiments/replay.py pairs, analysis/chunk4.py):
- An event is one policy round of a live run in which the controller moved at
  least one elephant. Each event is replayed as a move run T and a matched
  no-move run C with the live run's seed (replay.py docstring). A pair is
  analysed only if both arms finished without error and the uplink captures
  show every elephant on its intended spine in both arms (as in Chunk 3);
  excluded pairs are listed.
- Goodput G of a flow in a window = in-order bytes acknowledged by the
  receiver in it (receiver-side trace, cumulative ACK, as in Chunks 2-3).
  Windows start at the T arm's barrier reply, and at the sham move's barrier
  in C: W2, W5, W10. Primary horizon W10, the others reported.
- Deficit of a flow = G_C - G_T (positive = the move cost that flow bytes).
  Three terms per event, in bytes over the window:
  (1) moved: sum of the deficits of the flows the round moved;
  (2) bystanders: sum of the deficits of the elephants that were already on a
      destination path of the round and were not moved;
  (3) destination-path total: sum over every elephant on the destination
      path(s) after the move = (1) + (2).
  Reporting rule: all three terms are always shown side by side. The single
  damage number for a reroute is term (3), because (1) + (2) + (3) would count
  the moved flow and the bystanders twice. The moved-flow-only deficit (1)
  stays as a column, so the write-up can show where it has the wrong sign.
  Also reported: the deficit summed over all four elephants (what the fabric
  as a whole lost, including what the flows left behind on the source path
  gained), reorder / spurious events and RTOs in W2 at the moved flows'
  senders, and the pre-move 2 s goodput of every elephant in both arms (a
  pair whose arms differ before the move by more than 10 % of the link rate
  is flagged).
- Per controller: mean ± 95 % CI over events of each term at W10 and, per
  run, the sum of term (3) over the run's events (damage per run), so the
  benefit in P1 and the damage it cost sit in the same table.
- Features for Chunk 5: every event row carries the controller's pre-move
  features of its first move from policy_log.jsonl (rate, U_p, U_q, gain,
  elephants on p and q, configured delays of p and q, age, previous moves).
  Reported, not tested: damage against delay gap and against bystander count.

Figures (figures/chunk4_comparison.png, chunk4_damage.png,
chunk4_tradeoff.png) from analysis/chunk4.py only; tables in
<campaign>/chunk4_summary.md. Anything in the write-up that is not in this
plan is labelled post hoc.

### Built

`analysis/chunk4.py` reads <compare>/runs.csv, flows.csv, links.csv and
<damage>/runs/*/ (plus the live runs' policy_log.jsonl) and writes
chunk4_summary.md, chunk4_summary.csv, chunk4_contrasts.csv,
chunk4_events.csv and the three figures. Modes for testing without Chunk 4
data: `--loaded-check results/loaded/loaded-2026-10-19` runs the damage-term
code on the Chunk 3 LR2 (move) / LC1 (no move) runs paired by seed, which
are exactly a T / C pair with E0 moved and B the bystander, and must
reproduce the Chunk 3 W10 numbers (moved -0.88, bystander +0.86, total
-0.03 Mbit/s); `analysis/chunk4_fixture.py` writes a synthetic campaign
(marked SYNTHETIC, figures kept out of figures/) so the Hedera and cooldown
columns and the replay-pair code paths run end to end before real data
exist. The committed Chunk 3 ECMP campaign runs through the comparison
path as a one-controller campaign (its runs.csv predates the reroute
columns, which the script reports as absent).

### Stopped at the budget limit (16:45 UTC)

Done and checked: the plan above; `analysis/chunk4.py --loaded-check
results/loaded/loaded-2026-10-19` gives W10 moved -0.884 ± 0.359, bystander
+0.858 ± 0.354, destination-path total -0.025 ± 0.010 Mbit/s, reorder event
in 9/10 (paired-t CIs; Chunk 3 used Welch), i.e. the Chunk 3 numbers;
`--compare results/ecmp/ecmp-2026-10-19` reproduces STATUS.md (6.16 ± 0.93 /
4.23 ± 1.16 Mbit/s, FCT 50.8 / 1210 ms) with the reroute columns reported
as absent. NOT done: `analysis/chunk4_fixture.py` (synthetic fixture for the
Hedera / cooldown columns and the replay pairs) was not written, so the
multi-controller and `--damage` code paths have only run on the Chunk 3
data through the shared functions; the first real campaign is their test.
STATUS.md untouched (no results yet).

## 2026-10-26: Chunks 5-6 code prep (cloud, no Mininet)

Continues the cloud prep thread that stopped on credits after PR #7. Code
only, no experiments; Chunk 4 is running on the laptop and was not touched.

- `analysis/damage_model.py` (Chunk 6 training). Decisions, fixed before any
  real data: target dest_total_deficit_W10 (matches cost.HORIZON_S = 10 s);
  inputs are cost.FEATURES built through cost.vector, so training and the
  controller see identical vectors (non-finite -> 1e9, as ModelCost); split
  leave-one-group-out by seed (GroupKFold(10) above 10 groups; `--group` for
  held-out scenarios); hyperparameters fixed in the file (tree depth 4 / leaf
  5; GBR 200 trees, depth 3, lr 0.05, subsample 0.8), no tuning on test folds;
  the ML controller uses GBR, chosen in advance. Added metric: decision
  agreement (rule verdict with C_hat vs with the measured damage), because
  MAE alone does not say whether the controller decides right.
- Synthetic check (`--synthetic 300`, scratch only, not a result): every
  learned estimator beats predict-the-mean on held-out groups; formula fits
  best because the generator has the formula's structure (expected, says
  nothing about real data). Decision time on the cloud box: formula ~0.002 ms,
  GBR ~0.23 ms median per flow, far below the 1 s poll.
- `controllers/hedera.py`: decision moved into `_rule(feat)`; the feature
  record is now built before the decision instead of after. Greedy and
  cooldown verdicts are the same code; tests/test_costaware.py pins them.
  Side effect: none on the log (the record of a move is identical).
- `controllers/costaware.py` + variants: no silent fallback to C_hat = 0 if a
  model file is missing (it raises). Moves the greedy rule would make but the
  cost rule rejects are logged as kind "held" (ignored by existing readers,
  which filter kind "round").
- analysis/chunk4.py: labels for the two new controllers (no change to the
  pre-registered contrasts).
- docs/stage2_report.md: skeleton per handout Stage 2 list.

## 2026-10-26 Chunk 4 (part 3, desktop, no Mininet): fixture and analysis fixes

Remote Control session that opened on a Windows desktop (DESKTOP-P0BJCR1)
instead of the laptop, so no Mininet: only the analysis prep part 2 left
undone. Fresh clone of main at 5beb93a; Python 3.14.7 venv with the pinned
analysis packages (numpy 2.5.4, pandas 3.0.6, scipy 1.18.1, matplotlib
3.11.2). No Chunk 4 campaign or replay data existed when any of this was
written (10:00-10:50 UTC); nothing below is a result.

### Built
- `analysis/chunk4_fixture.py --out DIR [--seeds 5] [--mice-step 1]`: a
  SYNTHETIC four-controller campaign in the runner's layout. Live runs use
  the real ecmp_mixed schedules and go through experiments/metrics.py; the
  replay jobs come from experiments/replay.py itself, then one synthetic run
  per job. Planted: a round whose first logged move is a mouse that crossed
  the elephant threshold, two elephants moved in one round, moves onto an
  occupied uplink (bystanders), a flow moved in two consecutive rounds, two
  flaps, a seed without a reroute, and three broken pairs (an arm with a
  recorded error, an arm whose captures show the moved flow on its old
  spine, a missing arm). DIR/planted.json holds every value the analysis
  must recover.
- `tests/test_chunk4.py` (10 tests, about a minute): every planted damage
  term at W2/W5/W10, pre-move goodput, the exclusions and their reasons, the
  features, damage per run, the live reroute columns of runs.csv (reroutes,
  mice moves, flaps, rounds, reorder events, retransmissions), P1, and the
  S1 branch both ways.

### Fixes to analysis/chunk4.py (each checked against the part 2 plan)
1. Bug: an event's features came from the round's first logged move. A round
   can also move a mouse that crossed 1 Mbit/s, which the event does not
   include (replay.py skips it) and which can come first (candidates are
   sorted by path load). The fixture's hedera-r02 round 3 got the mouse's
   rate, 1.4 instead of 2.9 Mbit/s. Now the features are those of the
   event's first moved elephant, as the plan says ("its first move");
   feat_n_moves_in_round still counts every move of the round.
2. Damage per run left out runs without an elephant reroute instead of
   counting 0. The plan says "per run, the sum of term (3) over the run's
   events", which is 0 for such a run, and P1, the benefit it is set
   against, uses every seed; leaving them out inflates the damage of the
   controller that reroutes less often. Now such runs count 0, and a run
   with an event that has no analysed pair is left out and counted (its sum
   would be incomplete). The tradeoff figure uses the same numbers; new
   output chunk4_run_damage.csv.
3. Not implemented before: the plan's S1 branch ("If not, the Hedera and
   cooldown contrasts are reported against monitor-only as well"). When an
   S1 contrast fails, P1 and P2 are repeated against monitor-only
   (Hedera-style with the same predictions, labelled "P1 vs monitor-only";
   cooldown-only reported).
4. Plan items missing from the tables, now there: benefit next to damage in
   the per-run table ("so the benefit in P1 and the damage it cost sit in
   the same table": P1 in Mbit/s and as MB over the run, and the moved-flow
   term per run); RTOs in W2 at the moved flows' senders next to the reorder
   events; every elephant's pre-move goodput in both arms (events CSV,
   pre_T_mbps_<E> / pre_C_mbps_<E>).
5. Reporting only: the summary lists what replay.py did not replay (the
   problems in its jobs.json and the count of non-elephant moves), counts
   events whose W10 runs past the end of the elephants (w10_truncated), and
   says the pre-move flag fires by construction (T starts from the pre-move
   placement, C from the post-move one): it marks pairs, it excludes none.
6. The "Figures:" line no longer fails when the output is on another drive
   (Windows only).
No pre-registered contrast, prediction, window or exclusion rule changed.
experiments/replay.py: the docstring pointed at analysis/stage2.py, now
analysis/chunk4.py (no code change).

### Checks, rerun after the fixes
- `--loaded-check results/loaded/loaded-2026-10-19`: W10 moved -0.884 ±
  0.359, bystanders +0.858 ± 0.354, destination-path total -0.0254 ± 0.010
  Mbit/s, reorder event 9/10: unchanged, the Chunk 3 numbers.
- `--compare results/ecmp/ecmp-2026-10-19`: 6.16 ± 0.93 / 4.23 ± 1.16
  Mbit/s, unchanged.
- Fixture with 5 seeds and every mouse: 20 live runs, 11 events (3 broken);
  every planted term comes back to 1e-4 Mbit/s and the broken pairs are
  excluded with the right reason. All 19 tests pass
  (`.venv/bin/python -m unittest discover -s tests -t .`).

### Open point for the write-up
The pre-move flag (sum over elephants, arms differ by > 1 Mbit/s) cannot
tell a replay that went wrong from the intended difference between the arms.
A check that can: elephants that are neither moved nor on a source or
destination spine of the event should have the same pre-move goodput in
both arms. Not added (not in the plan); the per-elephant columns make it a
one-liner.

### Next session (laptop, in order)
1. `git pull`; if this part is not on main yet, `git am` the patch from the
   project library (chunk4-analysis-prep.patch). Then the 19 tests.
2. `python3 experiments/runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown --scenario ecmp_mixed --runs 10 --seed 1 --out results/compare/compare-<date>` (40 runs, about 1 h at 89 s per run).
3. `python3 experiments/replay.py --live results/compare/compare-<date> --out results/damage/damage-<date>/jobs.json`, then `python3 experiments/runner.py replay --spec results/damage/damage-<date>/jobs.json --out results/damage/damage-<date>` (two runs per event; one pilot each gave 2 events for a Hedera-style run and 1 for a cooldown-only run, so roughly 60 runs, 1.5 h).
4. `.venv/bin/python analysis/chunk4.py --compare results/compare/compare-<date> --damage results/damage/damage-<date>`, independent recomputation, STATUS.md.
The independent code review of the Chunk 4 controllers, runner and replay
(part 1, next-session step 1) has not been done. Chunk 3 ran its review
while the campaigns ran, before any result was read; the same works here.
STATUS.md untouched (no results yet).

## 2026-10-26 Chunk 4 (part 4, laptop): campaign, replays, analysis

Remote Control session on the laptop, Opus 5.5. Boot
be9af6df-f66d-41bc-acf6-c99ae6688363 (up since 2026-10-23 23:51 PKT), not
the Chunk 3 boot, so ECMP's dp_hash puts a given seed's flows on other
spines than in Chunk 3; every controller of this campaign shares the boot.

### Setup
- main was at e38c9b7 (PR #8, Chunks 5-6 prep), ahead of the part 3 patch's
  base (5beb93a). `git am -3` of chunk4-analysis-prep.patch: one conflict in
  LOG.md (both sides appended a section), resolved by keeping both, main's
  first. 26 of 26 tests pass (the patch's 19, plus test_costaware 5 and
  test_damage_model 2 from main).
- controllers/hedera.py changed after the part 1 pilots (60f4263: the
  decision moved into `_rule`, the feature record is built before it).
  Checked by reading: same verdicts, and the record can be built for every
  candidate (t_syn is always set, p and q are always spines 1-4). This
  campaign is the first live run of that version.
- Laptop on AC (sleep on AC is off in GNOME); each campaign ran under
  `systemd-inhibit --what=idle:sleep`. Desktop apps open (Chrome, ChatGPT),
  load average about 1 on 8 cores. All runs rootless (userns.sh).

### Campaign (started 18:33 PKT; `scripts/chunk4_campaigns.sh`)
`runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown --scenario
ecmp_mixed --runs 10 --seed 1 --out results/compare/compare-2026-10-26`,
40 runs, about 68 s each (part 1 pilots: 89 s on the old boot).

### Code review (controllers, runner, replay; before any campaign result was read)
Read: controllers/fabric.py, ecmp.py, ecmp_mon.py, cooldown.py, hedera.py,
experiments/runner.py, replay.py, moves.py, metrics.py, traffic.py,
spinetrace.py, and analysis/chunk4.py against the part 2 plan. As a pipeline
check, chunk4.py ran on the part 1 dev pilot (results/compare/dev-pilot1 and
results/damage/dev-pilot1, uncommitted, one Hedera-style run, two events;
not a result) with output in a scratch directory. No bug that changes a
number. Findings:
1. Fixed in the analysis, post hoc: the plan's "reorder / spurious events
   and RTOs in W2 at the moved flows' senders" are the sender hosts' nstat
   counters. In ecmp_mixed each of h1..h4 also sends about 10 mice/s, so
   these count the mice too (SYN-loss RTOs among them); in Chunk 3 h1
   carried only E0. In the dev pilot the no-move arm had an RTO in W2 in 2
   of 2 events. analysis/chunk4.py keeps the plan's column and adds a post
   hoc table: the no-move arm's host counters next to the move arm's, and
   the moved flows' own sockets from the ss logs (retransmissions,
   reord_seen, dsack_dups over W2; `ss_socket`, `ss_delta`, unit test
   test_ss_socket_counts). On the dev pilot: reord_seen 8 / 0 and 1 / 0
   (move / no-move arm), dsack_dups 20 / 0 and 2 / 0.
2. Design property of the frozen Hedera-style baseline, no change: with
   "gain > 0" a flow alone on its uplink moves whenever U_p - r_f > U_q,
   i.e. when p's mice plus the error of the 1 s rate estimate (OVS refreshes
   rule counters about every 0.5 s) exceed the load on q. The dev pilot's
   first event is one (elephants_on_p = 1, gain 1.42 Mbit/s). The summary
   now counts such events (post hoc, descriptive).
3. Replay timing, no change: the runner reads the 8 hosts' counters
   (snapshot m0_pre) after reaching the event time and before sending the
   flow-mods, so a replayed move or sham goes out about 50-70 ms after the
   live round's time, and the arms differ by about 20 ms (dev pilot: T 1.838
   s, C 1.860 s, event at 1.788 s). Windows start at each arm's own barrier
   reply, as planned, so this does not bias T against C.
4. Design property, reported: the first Hedera-style rounds come about 1.8
   s after t0, while the elephants are in slow start, so an early event's
   pre-move window covers their start. The summary lists event times
   (post hoc, descriptive).
5. Checked, fine: replay.py builds T and C as the plan says (every elephant
   pinned from the start, the same flow-mods at the same time, a sham
   MODIFY_STRICT in C, ACKs and mice on ECMP in both arms, the plain ECMP
   controller in both); events_of_run checks the controller's view against
   the uplink captures; policy_moves drops failed and no-op flow-mods;
   chunk4.py checks every elephant's captured spines in both arms and counts
   runs without an elephant event as 0 damage.
6. Caveat for S2: its reorder metric (snd_reorder_events, whole run, all
   senders) also includes the mice. Pre-registered as is; reported with this
   caveat.
Edits to the runner, controllers or metrics: none (each run re-imports
them). Only analysis/chunk4.py and tests/test_chunk4.py changed (one new
test; 27 of 27 pass).

### Campaigns run (all rootless, one boot, `scripts/chunk4_campaigns.sh`)
- Comparison: 40 of 40 runs ok, 13:33-14:18 UTC, 63-70 s per run; no run
  excluded.
- `replay.py`: 25 events (Hedera-style 10, cooldown-only 15) from 18 live
  runs; no problem flagged (every live run's controller view matched its
  captures); 7 moves of non-elephant flows not replayed (large mice of
  228-947 KB; in 5 the pin came after the mouse's last packet).
- Replays: 50 of 50 runs ok, 14:19-15:17 UTC, 61-75 s per run; every pair
  passed the path check, none excluded.

### Result: comparison (results/compare/compare-2026-10-26/chunk4_summary.md, figures/chunk4_comparison.png)
Mean ± 95 % CI over 10 seeds, ECMP / monitor-only / Hedera-style /
cooldown-only:
- Elephant goodput, mean of 4: 5.94 ± 1.04 / 5.96 ± 1.03 / 8.54 ± 0.11 /
  8.49 ± 0.11 Mbit/s; worst of 4: 4.60 ± 1.09 / 4.52 ± 1.10 / 8.33 ± 0.17 /
  8.31 ± 0.16.
- Mice FCT p50 50.5 ± 6.7 / 49.5 ± 6.3 / 63.3 ± 1.8 / 63.4 ± 2.4 ms; p99
  1157 ± 63 / 1149 ± 66 / 1131 ± 32 / 1113 ± 16 ms; no mouse failed.
- Reroutes per run 0 / 0 / 1.9 ± 0.8 / 2.4 ± 1.2 (3.8 and 4.8 per minute);
  one flap in 40 runs (cooldown-only seed 9: E4 s2 -> s3 at 1.98 s, back at
  7.01 s, just after its 5 s cooldown). Max link utilisation 0.99-1.00
  everywhere, as expected.
Pre-registered contrasts, paired by seed:
- P1, Hedera-style - ECMP, goodput: +2.59 [1.62, 3.56] Mbit/s: supported.
- P2, Hedera-style - ECMP, FCT p50: +12.8 [5.6, 20.0] ms (worse): supported.
- S1, monitor-only - ECMP: goodput +0.015 [-0.007, 0.037] Mbit/s and FCT p99
  -7.9 [-65.5, 49.7] ms as predicted; FCT p50 -0.96 [-1.84, -0.07] ms, CI just
  excludes zero. As the plan says, Hedera-style and cooldown-only are then
  also reported against monitor-only: P1 +2.58 [1.62, 3.53] Mbit/s and P2
  +13.8 [7.0, 20.5] ms, both supported; cooldown-only +2.53 [1.60, 3.46]
  Mbit/s, +13.9 [7.9, 19.9] ms.
- S2, cooldown-only - Hedera-style: reroutes/min +1.0 [-1.3, 3.3], flaps/min
  +0.2 [-0.25, 0.65], reorder events +57 [-59, 172]: none supported
  (predicted <= 0); goodput -0.05 [-0.11, 0.01] Mbit/s (reported).
Reading: both dynamic controllers do the same thing here. In their first one
or two rounds (1.4-2.9 s after t0, elephants still in slow start) they spread
the 4 elephants one per uplink; after that no move has a positive gain. Seed
1 had no ECMP collision and neither moved anything. So the cooldown and the
margin have nothing to suppress; cooldown-only's extra reroutes are two moves
in seed 2 in the elephants' last second (28.96 s, 29.97 s) and the one flap.
Spreading the elephants leaves no uplink without one, hence P2.

### Finding: the monitoring reorders about half of the mice inside the leaf (post hoc)
Monitor-only never moves a flow, yet its senders counted 1018 ± 84
reorder / spurious events per run against 2.8 ± 2.2 for ECMP (mostly
TcpExtTCPSACKReorder; the receivers' TcpExtTCPDSACKOldSent 167 vs 1.6 per
run, so spurious retransmissions too), and Hedera-style and cooldown-only
the same (986, 1042). `analysis/mice_reorder.py` uses each flow's IPv4 ID
order on l1's uplinks to find packets that left l1 in another order than
their sender sent them (results/compare/compare-2026-10-26/mice_reorder.md):
- Mice with a reordered packet per run, of about 1057: ECMP 0 (0.4),
  monitor-only 467 ± 26, Hedera-style 464 ± 39, cooldown-only 476 ± 39;
  reordered mouse packets 0 / 1239 / 1235 / 1287 per run. Elephant packets
  0 / 1 / 9 / 16 per run (the last two at policy moves).
- No mouse changed spine under monitor-only (10,532 of 10,532 on one
  uplink): reordering inside l1, not a path change.
- A reordered mouse packet comes a median 18-25 ms after the mouse's SYN.
  Pooled by the time from the SYN to the mouse's next packet on the uplink:
  within 20 ms 51-52 % of mice are reordered, 20-50 ms 39-43 %, 50-100 ms
  16-33 %, after 100 ms 0-1 % (ECMP 0 % in every bin).
Mechanism (inference from the timing, not confirmed with OVS's own counters):
the controller installs each new flow's prio-150 counting rule on the SYN's
packet-in; when that rule reaches l1 while the flow's first packets pass,
Open vSwitch moves the flow to a new datapath flow, and packets that went
through the slow path (upcall) leave after later ones. Mice whose first
packets pass after the rule is in place are not reordered.
Consequences: (1) every monitoring controller's whole-run reorder counters
measure this, not rerouting, so S2's reorder contrast cannot see reroute
damage; (2) FCT barely moves (S1); (3) the per-reroute damage below was
measured in replays under the plain ECMP controller, which does not have
it; (4) the Chunk 6 controllers share the monitoring. A methods result like
the TSQ artifact.

### Result: per-reroute damage (chunk4_summary.md, chunk4_events.csv, figures/chunk4_damage.png, chunk4_tradeoff.png)
25 events, every pair analysed. Deficit = no-move minus move, Mbit/s, mean ±
95 % CI over events:
- Hedera-style (10): W10 moved 0.020 ± 0.034, bystanders 0 (no Hedera-style
  move went onto an uplink with another elephant), destination-path total
  0.020 ± 0.034, all four elephants 0.014 ± 0.113; W2 total 0.004 ± 0.073.
- cooldown-only (15): W10 moved -0.045 ± 0.134, bystanders -0.043 ± 0.092
  (one event had one), destination-path total -0.088 ± 0.142, all four
  -0.083 ± 0.195; W2 total -0.457 ± 0.584.
- Damage per run (term 3 at W10 summed over the run's events): Hedera-style
  0.03 ± 0.04 MB, cooldown-only -0.16 ± 0.32 MB; benefit over ECMP 38.9 ±
  14.5 and 38.2 ± 14.1 MB per run.
- The moves do reorder the moved flows. Their own sockets (ss, W2):
  reord_seen 7.2 ± 4.6 (Hedera-style) and 6.2 ± 3.4 (cooldown-only),
  dsack_dups 7.6 ± 4.6 and 3.3 ± 1.5, 0 in every no-move arm; a reordering
  in 8 of 10 and 12 of 15 events. The plan's host counters had an RTO in W2
  in 10 of 10 move arms and 9 of 10 no-move arms (Hedera-style): the mice's,
  as the review expected.
- 22 of 25 events in the first 3 s. The first moved elephant shared its
  uplink with another elephant in 19 of 25 (by the captures); the
  controller's own count (elephants_on_p) called it alone in 16 of 25, true
  in 6, because a flow's path is known one round after it crosses 1 Mbit/s.
- Reported, not tested (Pearson r of term 3 at W10 against the features, n =
  25): predicted gain 0.05, rate 0.24, elephants on the destination -0.46 (p
  = 0.02; one event with a bystander drives it), delay gap 0.20.
- Post hoc sensitivity (from the independent recomputation): in 5 events a
  moved flow or bystander started 0.6-2.2 s late in one arm (its first SYN
  lost at a full uplink queue, so the arms did not carry the same flows when
  the window opened; cooldown-only seed 5 round 10 alone gives -0.79 Mbit/s).
  Without them and the 2 truncated events: destination-path total at W10
  +0.025 ± 0.041 (Hedera-style, 8 events), +0.016 ± 0.077 Mbit/s
  (cooldown-only, 11 events); W2 -0.024 ± 0.047 and -0.131 ± 0.124. The
  plan's exclusion rules did not foresee this, so the main table keeps them.
Reading: on this workload a reroute costs no measurable goodput (the
destination-path total is within ±0.15 Mbit/s of zero at W10 for both
controllers), although it does reorder the moved flow. That fits Chunk 3
(total goodput on the destination barely changes) and Chunk 2 (a few % for
the moved flow, idle). These moves leave a shared uplink for an empty one
early in the flow's life; Chunk 5's deliberate moves are what can show
damage under other conditions.

### Fixes after results were read (each checked; no pre-registered number changed)
1. analysis/chunk4.py: when an elephant ends, its receiver answers the last
   data with RSTs without the ACK flag (ack 0 in the trace), which counted
   as a cumulative ACK of 0, so the two events whose windows reach the end
   (cooldown-only seed 2, 28.96 s and 29.97 s) gave +54 and -63 Mbit/s over
   W2. Only packets with the ACK flag count now (unit test
   test_goodput_ignores_the_receivers_closing_rsts). Only those two events
   change; `--loaded-check` unchanged (W10 -0.884 / +0.858 / -0.025).
2. analysis/chunk4.py: the review's descriptive "alone on its uplink" line
   now uses the capture-checked placement, with the controller's count next
   to it; figure tick labels.
3. experiments/metrics.py: numpy 2.x's percentile returns NaN once its
   interpolation touches inf, so a failed mouse (FCT = inf by design) would
   have made FCT p50/p99 NaN (found by the independent recomputation).
   `censored_percentile`, tests/test_metrics.py. No mouse failed in any
   campaign (Chunks 3-4), so no number changes; the 90 Chunk 4 runs' metrics
   were regenerated after the replays and every CSV came out byte-identical.
4. analysis/mice_reorder.py: packets with IP ID 0 and no payload are the
   kernel's replies for a socket in TIME_WAIT, not the socket's counter; they
   made ECMP look like 31 reordered mice per run (found by the independent
   recomputation). Excluded.

### Independent recomputation (agent with its own code, raw files only)
Comparison: every per-controller mean and CI and every pre-registered
contrast confirmed to the reported precision (P1 +2.5930 [1.6245, 3.5614],
S1 FCT p50 -0.9564 [-1.8402, -0.0726], S2 reroutes/min +1.00 [-1.26, 3.26]),
the reorder counters, and the per-flow reordering with its own pcap reader
(TIME_WAIT packets excluded: 0.4 / 466.7 / 464.3 / 476.6 mice per run); all
43 policy moves agree with the captures (last packet before the barrier on
the old uplink, later ones on the new); 42,128 of 42,128 mice ok; each
seed's schedule identical across the four controllers. Damage: all 25 pairs
pass its path check in both arms, every term confirmed (e.g. W10
destination-path total +0.0204 ± 0.0342 and -0.0880 ± 0.1423; per run 25.5 ±
43.2 KB and -165 ± 321 KB), its traces match its own decode of the receiver
pcaps, and the ss counts match (ESTAB samples only, as chunk4.py reads them).
Notes it raised, all handled above or below: the closing-RST, late-start,
TIME_WAIT and NaN-percentile points; the ss window start matters (taking the
first sample after the move instead of the last before it would miss most
reorderings, which show up within 4-94 ms); in 15 of 25 events the move came
less than 2 s after t0, so the pre-move window covers the elephants'
start-up; 5 of the 7 non-elephant moves came after the mouse had finished.

### What to check / open
- The monitoring artifact needs a decision before Chunk 6 (STATUS.md, open
  question 1) and a direct check of the mechanism with OVS's upcall counters
  (`ovs-appctl dpctl/show` lookups missed, ECMP vs monitor-only, two runs).
- Chunk 5 should produce reroutes in steady state and onto loaded paths; on
  ecmp_mixed the controllers only move at flow start onto empty uplinks.
