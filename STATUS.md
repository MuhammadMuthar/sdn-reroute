# STATUS (rewritten at the end of every session)

Last session: Chunk 4, the four-controller comparison and the per-reroute
damage, laptop, 2026-10-26. Finish line met: one script
(`analysis/chunk4.py`) makes the Stage 2 figures in figures/ and the tables
in results/compare/compare-2026-10-26/chunk4_summary.md. Two things to know
before Chunk 5: the reroutes cost almost nothing on this workload, and the
Hedera-style monitoring itself reorders about half of the mice.

## What works

- Everything from Chunks 1-3 (environment, rootless Mininet via
  `scripts/userns.sh`, the gate, the reusable runner with host-NIC netem).
- Controllers (FabricController apps): `ecmp`, `ecmp_mon` (monitoring only),
  `hedera` (greedy, gain > 0), `cooldown` (gain > 1 Mbit/s and 5 s per-flow
  cooldown), plus `costaware_formula` / `costaware_ml` (Chunk 6 code, not run
  on switches yet).
- `runner.py campaign --controllers a,b,c` (paired seeds, block-randomised),
  `experiments/replay.py` (a matched move / no-move pair for every round in
  which a controller moved an elephant) and `runner.py replay --spec`.
- `scripts/chunk4_campaigns.sh`: the whole chunk in order (about 2 h).
- Analysis: `analysis/chunk4.py` (comparison, pre-registered contrasts,
  three-term damage per reroute, per-socket counters of the moved flows),
  `analysis/mice_reorder.py` (per-flow reordering inside the leaf from the
  uplink captures; needs the local pcaps). Tests: `.venv/bin/python -m
  unittest discover -s tests -t .` (30 pass).

## Key numbers (Chunk 4)

Comparison, results/compare/compare-2026-10-26 (10 paired seeds, ecmp_mixed,
40 of 40 runs, none excluded). Mean ± 95 % CI, ECMP / monitor-only /
Hedera-style / cooldown-only:

| metric | ECMP | monitor-only | Hedera-style | cooldown-only |
|---|---|---|---|---|
| elephant goodput, mean of 4 (Mbit/s) | 5.94 ± 1.04 | 5.96 ± 1.03 | 8.54 ± 0.11 | 8.49 ± 0.11 |
| elephant goodput, worst of 4 (Mbit/s) | 4.60 ± 1.09 | 4.52 ± 1.10 | 8.33 ± 0.17 | 8.31 ± 0.16 |
| mice FCT p50 (ms) | 50.5 ± 6.7 | 49.5 ± 6.3 | 63.3 ± 1.8 | 63.4 ± 2.4 |
| mice FCT p99 (ms) | 1157 ± 63 | 1149 ± 66 | 1131 ± 32 | 1113 ± 16 |
| reroutes per minute | 0 | 0 | 3.8 ± 1.6 | 4.8 ± 2.4 |
| flaps per minute | 0 | 0 | 0 | 0.2 ± 0.5 (one flap) |
| reorder / spurious events per run | 2.8 ± 2.2 | 1018 ± 84 | 986 ± 108 | 1042 ± 117 |

Pre-registered contrasts (paired by seed):
- P1 Hedera-style - ECMP, goodput: +2.59 [1.62, 3.56] Mbit/s, supported.
- P2 Hedera-style - ECMP, FCT p50: +12.8 [5.6, 20.0] ms (worse), supported.
- S1 monitor-only - ECMP: goodput and FCT p99 include 0 as predicted; FCT p50
  -0.96 [-1.84, -0.07] ms just excludes 0, so (per the plan) P1/P2 also hold
  against monitor-only: +2.58 [1.62, 3.53] Mbit/s, +13.8 [7.0, 20.5] ms.
- S2 cooldown-only - Hedera-style: reroutes/min +1.0 [-1.3, 3.3], flaps/min
  +0.2 [-0.25, 0.65], reorder events +57 [-59, 172]: not supported. Both
  controllers spread the elephants one per uplink in their first 1-2 rounds
  (1.4-2.9 s) and then have nothing to move, so the cooldown has nothing to
  suppress.

Per-reroute damage, results/damage/damage-2026-10-26 (25 events, 50 replay
runs, every pair passed the path check). Deficit = no-move minus move, W10,
Mbit/s, mean ± 95 % CI over events:

| controller | events | moved flow | bystanders | destination-path total | all 4 elephants |
|---|---|---|---|---|---|
| Hedera-style | 10 | 0.020 ± 0.034 | 0 | 0.020 ± 0.034 | 0.014 ± 0.113 |
| cooldown-only | 15 | -0.045 ± 0.134 | -0.043 ± 0.092 | -0.088 ± 0.142 | -0.083 ± 0.195 |

- Damage per run (term 3, W10): 0.03 ± 0.04 MB (Hedera-style), -0.16 ± 0.32
  MB (cooldown-only), against a benefit over ECMP of about 39 MB per run.
- Post hoc sensitivity: in 5 events a moved flow or bystander started late in
  one arm (lost first SYN); without them and the 2 events at the very end of
  the run, W10 totals are +0.025 ± 0.041 and +0.016 ± 0.077 Mbit/s.
- The moves do reorder the moved flow (own socket, W2: reord_seen 7.2 ± 4.6
  and 6.2 ± 3.4, dsack_dups 7.6 and 3.3, vs 0 in every no-move arm), but that
  costs no measurable goodput over 2-10 s.
- 22 of 25 events come in the first 3 s; in 24 of 25 no other elephant was on
  the destination after the move (1 event had a bystander); the first moved
  elephant came off an uplink it shared with another elephant in 19 of 25.

Monitoring artifact (post hoc, results/compare/compare-2026-10-26/
mice_reorder.md): with the controller's per-flow counting rules, 464-476 of
about 1057 mice per run have packets reordered inside l1 (ECMP: 0.4), a median
18-25 ms after their SYN; mice whose first packet after the SYN passes l1 more
than 100 ms after it are not reordered (0-1 %, against 51-52 % within 20 ms).
That is where the ~1000 reorder events per run come from. Inference: the
counting rule installed on each SYN races the flow's first packets through
Open vSwitch's slow path.

Every Chunk 4 number above (comparison, damage terms, per-socket counts,
mice reordering) was recomputed from raw data by an independent agent with
its own code: all confirmed (LOG.md, part 4).

Earlier chunks (unchanged): ECMP baseline 6.16 ± 0.93 Mbit/s mean goodput
(Chunk 3, other boot); loaded move: moved flow gains, bystander pays, total
-0.03 Mbit/s (Chunk 3); TSQ artifact fixed by host-NIC netem (Chunk 3).

## Decisions made this session

None that change the plan. Post hoc additions are labelled in the summary
(per-socket W2 counters, event timing, mice_reorder.py); three bugs fixed
after reading results (closing-RST ACKs in chunk4.py, NaN percentiles for
failed mice in metrics.py, TIME_WAIT packets in mice_reorder.py), none of
which changes a pre-registered number (LOG.md, part 4).

## Next

Chunk 5 (CHUNKS.md B): deliberate reroutes under varied conditions. Lessons
from Chunk 4 for its design:
- On ecmp_mixed the controllers only move at flow start onto empty uplinks,
  which costs nothing; damage needs moves in steady state, onto loaded paths,
  with delay gaps (Chunk 3's loaded_move conditions show it exists there).
- Measure reordering per flow (ss or traces), not with host nstat counters:
  with mice on the senders, and with the monitoring controllers' artifact,
  host counters are dominated by other flows.
- The controller's features lag in early rounds (elephants_on_p said "alone"
  in 16 of 25 events, true in 6).
- `analysis/damage_dataset.py` can already read compare-2026-10-26 +
  damage-2026-10-26 (25 rows) as a first real input.

## Open questions for Mutahar

1. The monitoring artifact: keep the Hedera-style monitoring as is for
   Chunks 5-6 (all dynamic controllers share it, damage is measured per flow
   and in replays without it), or change it so the counting rule cannot race
   a flow's first packets (e.g. release the SYN only after the rule is in,
   which adds controller latency to every SYN) and rerun Chunk 4?
   Recommendation: keep it, report it as a methods result next to TSQ, and
   confirm the mechanism with OVS's upcall counters in one short check.
2. Optional, from Chunk 3: `sudo ./scripts/chunk3_sudo.sh` (about 4 min).
3. Paper framing: TSQ plus the monitoring artifact as one methods section?
