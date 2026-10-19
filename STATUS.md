# STATUS (rewritten at the end of every session)

Last session: Chunk 2, the reordering gate, laptop, 2026-10-19. Gate status:
PASSED with a caveat. The pre-registered criterion is met, but single-flow
damage on an idle path is small. Plan (project recommendation, Mutahar can
override): go to Chunk 3 and test the damage mechanism under load. Chunk 2
is closed: the sudo cross-check (verify.sh 9.54 Mbit/s PASS; R2/C1 x3 as
root on the system OVS) agrees with the rootless campaign.

## What works

- Everything from Chunk 1 (environment, os-ken on Python 3.14, verify.sh;
  re-verified under sudo 2026-10-19T18:41, 9.54 Mbit/s).
- `scripts/userns.sh` runs Mininet + kernel OVS without sudo in fresh user/net
  namespaces; smoke iperf3 9.44 Mbit/s (sudo on the same laptop: 9.45).
- `topologies/leafspine.py` (2 leaves, 4 spines at 1/1/5/10 ms, 10 Mbit/s,
  20-packet queues), `controllers/gate_ctl.py` (reroute = one
  OFPFC_MODIFY_STRICT on the ingress leaf, flow-mod to barrier median 1.15 ms).
- `experiments/gate.py campaign` runs the 80-trial gate in ~52 min, rootless
  or under sudo; `analysis/gate.py` writes per_trial.csv, summary.csv,
  verdict.md and `figures/gate_timeseries.png`.

## Key numbers (results/gate/gate-2026-10-19/verdict.md)

| cond | move | trials with reorder/spurious event | goodput deficit in 2 s vs control |
|---|---|---|---|
| R1 | slow->fast, 4 ms gap | 10/10 | 6.9 KB (0.29 %), CI excludes 0 |
| R2 | slow->fast, 9 ms gap | 10/10 | 67 KB (2.8 %), CI [0.008, 0.529] Mbit/s |
| R0 | fast->fast, no gap | 10/10 | 7.5 KB (0.32 %) |
| R3 | fast->slow, 4 ms gap | 10/10 | 5.0 KB (0.21 %) |
| R4 | fast->slow, 9 ms gap | 2/10 | 1.1 KB (0.05 %) |
| C1/C3/C4 | sham move | 0/30 | control |

R2's deficit is mostly 3 of 10 trials that hit an RTO; the other 7 lose
10.8 KB each (post-hoc table in verdict.md). Retransmissions after a move are
almost all spurious (retrans ≈ DSACKs). Slow->fast moves cut cwnd (R2 43 -> 22
segments) and the kernel never undoes it, but with one flow on an idle path
the cut costs almost no goodput.

## Next

Chunk 3 (ECMP baseline and traffic generator, CHUNKS.md section B). Carry this into Chunk 3: the mechanism to test under
load is spurious retransmissions after a slow->fast move plus a cwnd cut the
kernel never undoes (TCPDSACKUndo = TCPLossUndo = 0 in all 80 trials). On an
idle path the cut only drains the standing queue; with competing flows it
should cost throughput share. Unmeasured so far.

## Open questions for Mutahar

1. Gate recorded as passed with a caveat and the plan is to go to Chunk 3
   (recommended in the project chat). Override if you would rather add a
   gate condition with background load first.
