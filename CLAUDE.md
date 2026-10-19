# Project brief: Learning the Cost of Rerouting

This file is read automatically at the start of every session. It holds everything
that stays true across the whole project. Session-specific instructions come in
the prompt.

## Who we are
Mutahar and Uswa Fatima, BESE-16AB, NUST SEECS. EE-353 Computer Networks project
(instructor Ali Hassan). We treat it as a real research problem: the goal is a
result that would survive a conference reviewer, not just a passing grade.

## The research question
When an SDN controller moves a large (elephant) TCP flow to a less-loaded path,
the move itself damages that flow: reordering, spurious retransmissions, cwnd
cuts, and flapping if the controller is too eager. Existing controllers
(Hedera-style rerouting, and recent Ryu/Mininet work) decide using path load only
and never measure that damage.

We want to know:
1. How much damage does each reroute actually cause, and what predicts it?
2. Can a controller that predicts damage (learned model, or a hand-written
   formula) keep most of Hedera's gain over ECMP while causing much less damage?

Decision rule we are testing:
  move flow f from path p to q only if
    (U_p - U_q) * r_f > C_hat(f, p, q)   and   t - t_last(f) > T_cool
  U = EWMA-smoothed path load, r_f = flow rate, C_hat = predicted damage,
  T_cool = per-flow cooldown.

## What is new vs prior work (do not overclaim)
- The *principle* of cautious rerouting is NOT new (Hermes SIGCOMM'17, FlexDATE
  ToN'22). Never claim it is.
- What is new: an explicit, per-flow damage estimate computed from
  controller-visible stats inside a reactive OpenFlow controller, and measuring
  TCP-level damage of each policy against ECMP and Hedera.
- Full analysis: research_gap.md. Spec: proposal.pdf. Course requirements:
  handout.pdf.

## The four controllers we compare
1. ECMP (OpenFlow 1.3 select groups) - static baseline
2. Hedera-style: poll stats every 1-2 s, elephant = >10% of link capacity,
   move to least-loaded path - dynamic baseline
3. Our rule with a hand-written damage formula
4. Our rule with an ML damage model (decision tree / gradient boosting, sklearn)

## Metrics
- Benefit: elephant throughput, short-flow FCT p50/p99, max link utilization,
  AI-round completion time (synchronized equal-size elephants).
- Damage: retransmitted + spurious + out-of-order segments per move, throughput
  drop after each move, reroutes/min, flaps (A->B->A).
- Model: prediction error on held-out topologies/loads, decision time per flow.
- Rigor: 10 runs per configuration, mean with 95% confidence intervals.

## Known pitfalls (learned the hard way by others - respect these)
- Upstream Ryu 4.34 breaks on Python 3.10+ (eventlet ALREADY_HANDLED). Options:
  Python 3.9 venv + ryu 4.34 + eventlet==0.30.2, or the os-ken fork.
- In Mininet, a reroute causes almost no reordering unless paths have
  different delays. Use TCLink with unequal delays (e.g. 1 ms vs 5-10 ms).
  Put netem delay on switch interfaces, not the same interface as bandwidth
  shaping.
- OVS select groups hash per flow, so few elephants collide often. Verify
  bucket behaviour with `ovs-ofctl -O OpenFlow13 dump-group-stats`.
- Flow stats are cumulative: compute rates from deltas using duration_sec/nsec.
- Fat-trees have loops: handle ARP or you get broadcast storms.
- Disable TSO/GSO/GRO on host interfaces before capturing.
- Wireshark can mislabel late reordered packets as retransmissions: cross-check
  with `nstat -az TcpRetransSegs` and `ss -tin`.
- Keep links at 10 Mbps; check idle iperf reaches ~95% of configured rate.
  Run `mn -c` between runs.

## Machine
Native Ubuntu laptop. Mininet/OVS need root. If you cannot run a sudo command
yourself, print the exact command for me to run and wait.
Never modify or delete anything outside this project folder.

## How we work: one chunk per session
Each session does ONE chunk with a clear finish line, then stops.
- Start of every session: read STATUS.md and the latest part of LOG.md.
- During: append to LOG.md (what you tried, what failed, decisions and why).
- Stop and ask me when: a result contradicts our expectations, a decision would
  change the research question, or you have been blocked ~30 minutes.
- End of every session:
  1. Verify the chunk's finish line actually works (run it, don't assume).
  2. Rewrite STATUS.md: what works, key numbers, what's next, open questions.
  3. git commit with a clear message.
  4. Give me a short summary: done / numbers / what I should check / next chunk.

## Evidence rules (non-negotiable)
- Every number and figure must come from a script in this repo that I can rerun.
- Never invent, estimate, or hand-edit results. If something didn't work, say so.
- Raw data goes in results/ untouched; analysis scripts read from it.
- Negative results are fine and get reported honestly.

## Repo layout
controllers/   Ryu/os-ken apps (ecmp.py, hedera.py, costaware.py)
topologies/    Mininet topologies (leafspine.py, fattree.py)
experiments/   runners that launch topology + controller + traffic + capture
analysis/      parsing captures, stats, plotting, model training
results/       raw outputs (CSV, pcaps summaries) - never edited by hand
figures/       generated plots
README.md      install, run, reproduce (an outsider must be able to follow it)
STATUS.md      current state, rewritten each session
LOG.md         running lab notebook, appended each session
