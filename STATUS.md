# STATUS (rewritten at the end of every session)

Last session: Chunk 2, the reordering gate, laptop, 2026-10-19. The gate
PASSES its pre-registered criterion, but the measured damage is small.
Waiting for Mutahar's decision before Chunk 3.

## What works

- Everything from Chunk 1 (environment, os-ken on Python 3.14, verify.sh).
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

Pending Mutahar's decision (see open question 1). If the gate is accepted:
Chunk 3 (ECMP baseline and traffic generator, CHUNKS.md section B).

## Open questions for Mutahar

1. The gate passes, but single-flow damage is tiny (0.3 % for a 4 ms gap,
   2.8 % for 9 ms, driven by occasional RTOs). Accept and go to Chunk 3, where
   competing traffic should make cwnd cuts cost real throughput? Or first add
   a gate condition with background load to check that damage grows there?
2. Run `sudo ./scripts/chunk2_sudo.sh` once (verify.sh plus an R2/C1 x3
   cross-check on the system OVS, ~6 min) to confirm the rootless runs match.
