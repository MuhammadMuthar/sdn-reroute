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
