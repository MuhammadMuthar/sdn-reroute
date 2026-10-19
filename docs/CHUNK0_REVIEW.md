# Chunk 0 review: what is wrong or risky, and the chunk plan

Written 2026-10-19 after reading CLAUDE.md, the Stage 1 proposal, the research-gap
note and the EE-353 handout in full. No code was written. Numbers below marked
"estimate" are back-of-envelope and must be measured in Chunk 2 before they appear
anywhere else.

Stage 2 milestone report is due 15 November 2026 (proposal, section 7). That is
about four weeks from today. Chunks 1 to 4 fit if Chunk 2 passes on the first
or second attempt.

---

## 1. Technically wrong or risky in the proposal

Ordered by how much damage each would do to the paper if left alone.

### 1.1 The decision rule compares quantities in different units (wrong as written)

The rule is `(U_p - U_q) * r_f > C_hat(f, p, q)`. Utilization is a fraction or a
rate, `r_f` is a rate, so the left side is rate-squared or rate. The right side is
"damage", which the proposal measures as retransmitted segments and a throughput
drop. A reviewer will ask what it means for a rate to exceed a segment count.

Fix: put both sides in bytes over a decision horizon H (for example 5 s).
- Gain = (predicted rate of f on q minus current rate on p) * H. The predicted rate
  on q is the fair share f would get there, roughly min(r_f, free capacity of q),
  not (U_p - U_q) * r_f. Moving f also adds r_f to q, which the current formula
  ignores.
- Cost = predicted goodput deficit of f in bytes during its recovery after the
  move (definition in 1.2) plus a penalty per retransmitted segment if we want it.
The model then predicts bytes, and the formula variant is a few lines with the
same output. The cooldown term stays as it is.

### 1.2 "Throughput drop after the move" is not a clean damage label (risky)

If the controller moves f to a path with higher delay or more load, throughput
falls because the new path is worse, not because the move hurt TCP. Labelling
that as "damage" teaches the model the wrong thing and double counts the gain term.

Fix: define damage as the transient deficit relative to the steady state f reaches
on the new path: integrate (steady-state rate on q minus actual rate) over the
recovery window, in bytes, and stop the window when the rate has been within a
band of the new steady state for some time. Record retransmissions, spurious
retransmissions, reorder events and cwnd-undo events alongside it. Fix this
definition in LOG.md during Chunk 2 before the first run so the gate measures the
right thing.

### 1.3 Modern Linux TCP is built to absorb exactly this reordering (biggest risk to the gate)

Reordering only happens when the flow moves from a slower path to a faster one:
new packets overtake the ones still in flight on the old path. Moving fast to slow
makes a gap, not reordering. Estimate at 10 Mbps and a 5 ms delay gap: about four
1500-byte packets are overtaken, since one packet takes 1.2 ms on the wire.

The sender's kernel is the laptop kernel, which has RACK-TLP loss detection on by
default. RACK waits a reorder window (about a quarter of the minimum RTT at first)
before declaring loss, and widens that window each time it sees a DSACK proving a
retransmission was spurious. The `tcp_reordering` estimate also grows up to 300.
So the first move may cause spurious retransmissions and a cwnd cut, and later
moves on the same connection may cause almost nothing. The damage can shrink
with the number of moves, which is the opposite of what the "time since last move"
feature assumes, and it means the gate could show a small, noisy effect.

Two confounds come with this:
- Linux caches the reordering estimate per destination in `tcp_metrics`. The next
  connection to the same host starts tolerant. Every run must `ip tcp_metrics flush`
  (or set `net.ipv4.tcp_no_metrics_save=1`) or run-to-run results drift.
- All Mininet hosts share one kernel, so one sysctl setting applies to all hosts.
  That is fine, but the sysctl values must be logged in every result row and
  reported in the paper.

What to do: keep the default stack as the primary result, because that is what a
real network sees. Pre-register the pass criterion before running (section 3,
Chunk 2). Test two or three delay gaps (for example 3, 6 and 12 ms) and both
directions. If damage is small with defaults, report it honestly; a second run
with RACK off (`net.ipv4.tcp_recovery=0`) is allowed only as a clearly labelled
sensitivity analysis, never as the headline. If even that shows little, the
research-gap note's Option 3 fallback applies.

### 1.4 Netem queue depth will hide the effect (risky, easy to fix)

Netem's default queue is 1000 packets. At 10 Mbps that is about 1.2 s of buffering
(estimate). A deep queue inflates RTT, lets TCP ride through reordering, and makes
"path delay" meaningless. Set `max_queue_size` to about twice the
bandwidth-delay product (tens of packets at these rates) and keep bandwidth
shaping (HTB) on host links and netem delay on spine-side switch interfaces, as
CLAUDE.md already says. Check with `tc -s qdisc` that each interface has exactly
one of the two.

### 1.5 Mice flows on a delay-asymmetric topology (confound in the comparison)

With two fast and two slow spines, a short flow's completion time is set mostly
by which spine the hash picked, not by load. ECMP versus Hedera-style differences
in short-flow FCT will be swamped by this unless it is controlled. Options:
report FCT per spine class, or keep the delay gap small on the comparison topology
and large only in the damage campaign, or use the symmetric k=4 fat-tree for the
mice-FCT comparison in Stage 3. Decide in Chunk 3; my default is to report FCT
split by spine class and say so.

### 1.6 "Controller-visible" features that the controller cannot see (overclaim risk)

- Path delay difference: a reactive OpenFlow controller does not know link delays
  unless it probes (LLDP timestamps or a PacketOut echo). In Mininet we configure
  them, so the controller "knows" them by cheating. Either implement a simple
  probe in Chunk 5 or state plainly in the paper that path delays are taken from
  topology configuration. Do not call it controller-visible without one of those.
- Remaining flow size: unknowable to the controller. Keep bytes sent so far as a
  feature; use remaining size only as an "oracle" feature for an upper bound, and
  label it as such.

### 1.7 Without a cooldown-only ablation, reviewers will credit the cooldown (missing control)

The proposed controller adds two things to Hedera-style rerouting: a damage
estimate and a cooldown. If it wins, the obvious objection is that the cooldown
alone did it. Add a fifth controller: Hedera-style plus the same cooldown and a
fixed hysteresis margin, no damage estimate. It is a flag on the Chunk 4
controller and costs almost nothing. The paper's claim then becomes "the cost
estimate adds X over a plain cooldown", which is defensible.

### 1.8 Hedera is a global scheduler; ours is a greedy mover (naming)

Hedera estimates demands and runs Global First Fit or Simulated Annealing over
all elephants every 5 s. "Poll every 1-2 s, move the biggest flow to the
least-loaded path" is a reasonable baseline but it is Hedera-style greedy
placement, not Hedera. Name it that everywhere, including the Stage 2 report, and
say which parts of Hedera were dropped and why.

### 1.9 Prediction accuracy is not the same as a better controller (evaluation gap)

The proposal evaluates the model by prediction error. The claim that matters is
closed-loop: does the controller using the model make better move decisions than
the one using the formula? Both must be reported. For the offline part, include a
trivial predict-the-mean baseline; a tree that cannot beat the mean is not a
result. Split train and test by scenario group, not by random row, or the
"unseen scenarios" claim is false.

### 1.10 Reroute mechanics: design so a move is one atomic flow-mod (simplification)

"Install new path before removing old" is awkward if every switch on the path has
a per-flow rule. Design the leaf-spine so spines and the egress leaf forward by
destination host (static rules) and only the ingress leaf chooses the spine per
flow. A reroute is then a single OFPFC_MODIFY on one switch, atomic, with no
install-order problem and no transient loss, so all measured damage is TCP
behaviour. This also makes "flow pinned by ECMP select group" and "flow moved by
controller" coexist: the controller installs a higher-priority exact-match rule on
the ingress leaf, and deleting it later is itself a reroute, so keep it until the
flow ends (idle timeout).

### 1.11 Measurement choice: kernel counters first, Wireshark second

Every Mininet host is its own network namespace, so `/proc/net/netstat` and
`nstat` inside a host give per-host TCP counters with no filtering needed. Use
deltas of TcpRetransSegs, TcpExtTCPFastRetrans, TcpExtTCPSpuriousRTOs,
TcpExtTCPDSACKRecv (spurious retransmissions acknowledged), TcpExtTCPSACKReorder,
TcpExtTCPTSReorder, TcpExtTCPRACKReorder (reordering detected) and
TcpExtTCPDSACKUndo / TcpExtTCPLossUndo (cwnd cuts reversed) as the primary damage
counts, plus `ss -ti` for reordering and retrans per socket. tshark's
`tcp.analysis.*` flags are the cross-check, not the source of truth, because the
note already shows Wireshark mislabels late reordered packets. Record the exact
counter names in the data dictionary; a few of them differ by kernel version, so
check which exist in Chunk 1 and list them in results/env.txt.

### 1.12 Smaller points

- Pair the runs: seed the traffic generator and run all controllers on the same
  seeds. Paired differences give much tighter 95% CIs than independent runs.
- iperf3 serves one test per server process and cannot generate hundreds of short
  flows. Use iperf3 (with `--cport` fixed so the ECMP hash is reproducible, and
  JSON output at 100 ms intervals) for elephants, and a small Python
  client/server for mice that logs each flow's completion time.
- Title says "ML-Based". If the formula matches the model, which the proposal
  itself allows, the title is undercut. Consider "Disruption-Aware" as the lead
  and ML as one instantiation when the Stage 2 report is written.
- The research-gap note's search was partial; DPLBAnt and the MDPI
  flow-redirection paper are still unread. Read them before Stage 2, not Stage 3.
- Define a flap precisely (A to B to A within W seconds) before Chunk 4.
- Raspberry Pi testbed: keep optional; nothing in Chunks 1 to 6 depends on it.

---

## 2. What is right and should not change

- The reframing around measuring and pricing disruption is the correct gap; the
  proposal already cites Hermes and FlexDATE honestly. Keep that tone.
- 10 Mbps links, unequal path delays, 10 runs with CIs, raw results untouched,
  every figure from a script: all correct and already in CLAUDE.md.
- os-ken as the controller framework on a modern Ubuntu is the right default.
  The CLI is `osken-manager` and imports are `os_ken.*` instead of `ryu.*`; Chunk 1
  verifies the rest.
- The gate in Chunk 2 before any baseline work is the right order. Do not swap it.

---

## 3. Proposed chunk plan to a Stage 2-ready milestone

Order unchanged (1 to 6). Full revised prompts are in CHUNKS.md section B. What
changes and why:

| Chunk | Finish line (runnable) | Change from the original and reason |
|---|---|---|
| 1 Environment | Learning switch forwards traffic in a 2-switch Mininet; `setup.sh` + `requirements.txt` rebuild it; `scripts/sysinfo.sh` writes kernel, OVS, Mininet, controller versions and all `net.ipv4.tcp_*` sysctls to `results/env.txt`; idle iperf3 reaches ~95% of 10 Mbps | Adds the sysinfo script and the iperf sanity check, because 1.3 and 1.4 make kernel state part of every result |
| 2 Gate | `results/gate/*.csv`; `analysis/gate.py` produces the table and the throughput time series around the reroute; pass criterion written in LOG.md before the first run | Adds the pre-registered criterion, both directions, two or three delay gaps, tcp_metrics flush, queue limit, and pulls the runner skeleton forward from Chunk 3, because the gate needs topology + controller + capture + CSV anyway and writing it twice wastes the day |
| 3 ECMP + traffic | One command runs ECMP N times with seeds and writes a CSV; `dump-group-stats` shows spreading; the same seed reproduces the flow schedule | Adds the seeded mice generator and reproducibility check (1.12) |
| 4 Baselines + comparison (Stage 2 ready) | `figures/` from one analysis script; STATUS.md results summary; ECMP vs Hedera-style vs cooldown-only, 10 paired runs | Adds the cooldown-only ablation (1.7), flaps/min and reorder events as metrics, paired seeds |
| 5 Damage dataset | Dataset CSV + data dictionary + correlation table and plot + row count | Adds direction of move, number of previous moves, scenario id for grouped splits, and the deficit-vs-new-steady-state label (1.2) |
| 6 Model + controller | Five controllers run through the same runner; first 5-way comparison; offline MAE vs predict-the-mean and vs formula; decision time per flow | Adds the trivial baseline and grouped evaluation (1.9), unit-consistent rule (1.1) |
| 7 Stage 3 start | Fat-tree with ARP handling, link degrade mid-run, AI-round pattern, full campaigns, paper figures, clean-machine README test | New, so the Stage 3 scope is visible now |

Decision points where I will stop and ask, in line with CLAUDE.md:
- End of Chunk 2 if the criterion fails or is borderline.
- Chunk 3 if OVS select-group spreading on the installed version does not match
  expectations (the research-gap note flags weight and hash quirks).
- Chunk 4 if the cooldown-only variant already captures most of Hedera-style's
  gain with little damage, because that changes what the paper is about.

Timing against 15 November 2026: Chunk 1 one session, Chunk 2 one or two sessions
with you present, Chunks 3 and 4 one session each, leaving buffer for the
milestone write-up. Chunks 5 and 6 are Stage 3 work that can start before the
milestone if Chunk 4 lands early.
