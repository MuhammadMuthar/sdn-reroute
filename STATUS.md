# STATUS (rewritten at the end of every session)

Last session: Chunks 5-6 code prep, cloud, 2026-10-26 (no Mininet). Code only,
no new results: the damage-model training script, the two cost-aware
controllers and a Stage 2 report skeleton exist and pass offline tests. The
Chunk 3 state below is unchanged; Chunk 4 is running on the laptop.

## Chunks 5-6 code (cloud, 2026-10-26)

- `analysis/damage_dataset.py` (PR #7): Chunk 5 dataset builder.
- `analysis/damage_model.py`: leave-one-seed-out evaluation of mean /
  formula / decision tree / gradient boosting on dest_total_deficit_W10 (and
  the moved and bystander terms), MAE with 95 % CI, decision agreement, and
  decision time per flow; saves models/formula.json and
  models/damage_model.joblib for the controllers. `--synthetic N` exercises it
  without data (marked SYNTHETIC, never writes models/).
- `controllers/costaware.py` (+ `costaware_formula.py`, `costaware_ml.py`):
  hedera.py with the rule from controllers/cost.py swapped in through a new
  `_rule` hook; greedy and cooldown decisions are unchanged (tested). Logs
  gain/cost bytes and decision time per move, and held-back moves per round.
  Fails at start if its model file is missing. Runner: `--controllers
  ecmp,hedera,cooldown,costaware_formula,costaware_ml`.
- `docs/stage2_report.md`: report skeleton (due 2026-11-15) with the Chunk 2-3
  numbers and TODOs naming the script that fills each gap.
- Tests: `python3 -m pytest tests` (16 pass; needs os-ken and sklearn).

## What works

- Everything from Chunks 1-2 (environment, rootless Mininet via
  `scripts/userns.sh`, the gate).
- `controllers/fabric.py`: shared base for all controllers (static forwarding,
  pin/move/status/events command channel, OFPT_ERROR-checked flow-mods).
  `controllers/ecmp.py`: OF1.3 select group per leaf, OVS default dp_hash.
- `experiments/runner.py campaign --controller <app> --scenario <name> --runs N
  --seed S --out DIR`: fresh namespaces per run, seeded schedule
  (`experiments/traffic.py`; iperf3 elephants with fixed --cport, mice from
  `experiments/mice.py` with FCT), counters of every host, ss, captures on
  l1's uplinks (path oracle and utilisation), traces of tagged elephants,
  group/flow/port stats; `experiments/metrics.py` writes runs.csv,
  flows.csv, links.csv. Scenarios: `ecmp_mixed`, `loaded_move` (conditions
  IC1, IR2, LC1, LR0, LR2). Chunks 4-6 plug in a new controller app and/or
  scenario; deliberate moves and pins already work on top of any controller.
- Host NICs get a 10 us netem by default (`--host-qdisc netem`), which fixes
  the TSQ artifact; `experiments/tsq_check.py` demonstrates it.
- Analysis: `analysis/ecmp.py`, `analysis/loaded_move.py`.

## Key numbers

ECMP baseline (results/ecmp/ecmp-2026-10-19/summary.md, 10 seeds, 30 s, 4
elephants + about 1050 mice per run):
- Spread: mice per spine 2596 / 2617 / 2657 / 2662 (chi-square p = 0.76);
  group bucket bytes 35 / 25 / 21 / 19 %; no flow ever changed spine; ACKs
  on the data's spine. Replay of seeds 1-3: same schedule sha256, same spine
  for 3169 / 3169 flows.
- Elephant goodput by elephants sharing its uplink: 1 -> 8.70, 2 -> 4.42,
  3 -> 3.09 Mbit/s; per run mean 6.16 ± 0.93, worst 4.23 ± 1.16.
- Mice FCT p50 50.8 ± 5.8 ms, p99 1210 ± 100 ms; p99 is mostly lost
  handshakes (2.3 % of mice wait for the 1 s initial RTO). Max link
  utilisation is 1.0 in every run, so it will not separate controllers.

Loaded move (results/loaded/loaded-2026-10-19/summary.md, 10 runs x 5
conditions; E0 moved onto s1 where elephant B runs):

| contrast (W10 = 10 s after the move) | moved flow E0 | bystander B | E0 + B |
|---|---|---|---|
| slow->fast (s4->s1) vs fast->fast (s2->s1), P1 | -0.64 [-0.96, -0.32] | +0.63 [0.31, 0.95] | -0.01 |
| slow->fast vs no move | -0.88 [-1.25, -0.52] | +0.86 [0.50, 1.22] | -0.03 [-0.04, -0.01] |
| idle slow->fast vs no move (gate R2 replication), W2 | +0.10 [0.03, 0.17] | | |

Deficits in Mbit/s, reference minus reroute (negative = the reroute did
better). Pre-registered prediction (P1 > 0) not supported: the moved flow
gains, the bystander pays, total goodput hardly changes.

Ablation with the old host qdisc (results/loaded/loaded-default-2026-10-19):
P1 flips to +2.46 Mbit/s [1.77, 3.14] (47 %), with frozen cwnds and almost no
loss. Idle IR2 vs IC1 W2 80 KB, 3.3 % (gate R2 67 KB, 2.8 %): the new runner
reproduces the gate under the old setting.

TSQ check (results/tsq/tsq-2026-10-19): old qdisc, 14-29 KB of each sender's
skbs charged while queued in the switch, packets out / cwnd 0.5-0.67, 27
drops; fixed, 0 KB, 1.00, 118 drops.

Every number above was recomputed from raw data by an independent agent
with its own code: all confirmed (LOG.md).

## Decisions made this session (LOG.md has the evidence)

1. Host-NIC netem for every run from Chunk 3 on; Chunk 2 stays as recorded.
2. Damage = moved flow + bystanders on the destination path + lost total,
   each vs its matched no-move control; moved-flow-only kept as a column
   (CHUNKS.md section B amendment). Both decided through the project
   coordinator on delegated authority, on this thread's recommendation.

## Next

After Chunk 4: Chunk 5 (`experiments/replay.py` + `runner.py replay` over the
moves, then `analysis/damage_dataset.py`), Chunk 6 (`analysis/damage_model.py
--data ...`, then the 5-way campaign). Not tested yet: the cost-aware
controllers on real switches (first laptop run should be a one-seed pilot).

Chunk 4 notes (unchanged):

Chunk 4 (CHUNKS.md B): Hedera-style + cooldown-only controllers as
FabricController apps, ECMP vs Hedera vs cooldown-only, 10 paired seeds on
`ecmp_mixed`, three-term damage per reroute. Watch-outs: use elephant goodput
and FCT (not max link utilisation) to separate controllers; FCT p99 is
dominated by SYN loss, so also report p99 by size class; every right-hand host
receives one elephant, so mice share their last hop with it.

## Open questions for Mutahar

1. Optional: `sudo ./scripts/chunk3_sudo.sh` (about 4 min) repeats the TSQ
   check and two ECMP seeds as real root on the system OVS, a consistency
   check like Chunk 2's. Everything in Chunk 3 ran rootless.
2. Paper framing: the TSQ artifact plus its effect on the loaded-move sign is
   a methods result in its own right; worth a section?
