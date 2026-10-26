# Stage 2 milestone report (skeleton)

**Learning the Cost of Rerouting: disruption-aware elephant-flow rerouting in an SDN controller**
Muhammad Muthar (540395), Uswa Fatima (575207). EE-353 Computer Networks, BESE-16AB, NUST SEECS.
Instructor: Ali Hassan. Due 15 November 2026. Code: https://github.com/MuhammadMuthar/sdn-reroute

> Skeleton written 2026-10-26. Sections follow the handout's Stage 2 list
> (handout.pdf p. 3-4). Every number below is copied from a summary file that
> a script in the repo writes; the source is named next to it. `[TODO ...]`
> marks what is still missing and which script fills it. Target length 1-2
> pages: cut, don't add, when the Chunk 4 numbers arrive.

## 1. Progress overview

| Chunk | What | State |
|---|---|---|
| 1 | Environment: Mininet + OVS + os-ken 4.2.2, rootless runs, 10 Mbit/s check | done |
| 2 | Gate: does one reroute measurably hurt TCP? | passed with caveat |
| 3 | ECMP baseline + reusable experiment runner | done |
| 4 | Hedera-style and cooldown-only baselines, comparison vs ECMP | [TODO: running on the laptop] |
| 5 | Damage dataset (per-move features + measured damage) | builder done, data pending Chunk 4 |
| 6 | Damage model + cost-aware controller (formula, ML) | code done and tested offline; no runs yet |

## 2. Refined problem statement

When an SDN controller moves an elephant TCP flow to a less-loaded path, the
move itself damages traffic: reordering, spurious retransmissions and window
cuts on the moved flow, and lost share for the flows already on the
destination path. Hedera-style controllers decide on path load alone and never
measure this damage. We ask (1) how much damage each reroute causes and what
predicts it from controller-visible statistics, and (2) whether a controller
that moves a flow only when `gain_bytes > C_hat(f, p, q)` and its per-flow
cooldown has expired keeps most of Hedera's gain over ECMP at much lower damage.

What is not new (do not overclaim): cautious rerouting itself (Hermes,
SIGCOMM'17; FlexDATE, ToN'22). What is new: an explicit per-flow damage
estimate inside a reactive OpenFlow controller, and measuring the TCP-level
damage of each policy against ECMP and Hedera (docs/research_gap.md).

Refinement from our own measurements (Chunk 3, CHUNKS.md amendment): damage
is three terms, each a goodput deficit against a matched no-move control:
the moved flow, the bystanders on the destination path, and the destination
path's total. The moved flow alone can *gain* from a move onto a loaded path,
so the destination-path total is the single damage number.

## 3. System features

- Leaf-spine fabric in Mininet, 10 Mbit/s links, unequal spine delays, 10 us
  netem on host NICs (fixes a TCP Small Queues emulation artifact, Chunk 3).
- Five controllers on one base (`controllers/fabric.py`), differing only in
  the decision: ECMP (`ecmp.py`), Hedera-style (`hedera.py`), cooldown-only
  ablation (`cooldown.py`), cost-aware with formula (`costaware_formula.py`)
  and with ML model (`costaware_ml.py`).
- Seeded, paired experiment runner (`experiments/runner.py`), matched
  move/no-move replays (`experiments/replay.py`), per-host TCP counters,
  receiver traces, path oracle from uplink captures.
- Damage dataset builder (`analysis/damage_dataset.py`) and model training /
  held-out evaluation (`analysis/damage_model.py`).

## 4. Initial implementation and baselines

ECMP (OpenFlow 1.3 select groups) is implemented and measured (Chunk 3,
`results/ecmp/ecmp-2026-10-19/summary.md`, `analysis/ecmp.py`; 10 seeds, 30 s,
4 elephants + ~1050 mice per run): elephant goodput mean 6.16 ± 0.93 Mbit/s,
worst elephant 4.23 ± 1.16 Mbit/s; by elephants sharing an uplink 1 -> 8.70,
2 -> 4.42, 3 -> 3.09 Mbit/s; mice FCT p50 50.8 ± 5.8 ms, p99 1210 ± 100 ms.

Hedera-style and cooldown-only baselines are implemented (Chunk 4).
[TODO Chunk 4: one line per controller from `<campaign>/chunk4_summary.md`
(analysis/chunk4.py).]

## 5. Initial experimental results

**Gate (Chunk 2, `results/gate/gate-2026-10-19/verdict.md`, analysis/gate.py).**
A single reroute on an idle path caused reorder/spurious events in 10/10
trials of every move condition and 0/30 controls; goodput deficit over 2 s
was 6.9 KB (0.29 %) for a 4 ms gap and 67 KB (2.8 %) for a 9 ms gap, the
latter driven by RTOs in 3 of 10 trials. Small but real.

**Loaded move (Chunk 3, `results/loaded/loaded-2026-10-19/summary.md`,
analysis/loaded_move.py).** Moving a flow slow->fast onto a path carrying
another elephant, vs no move, over 10 s: moved flow deficit -0.88 Mbit/s
[-1.25, -0.52] (it gains), bystander +0.86 [0.50, 1.22] (it pays), total
-0.03 [-0.04, -0.01]. The pre-registered prediction (moved flow hurt) was not
supported; this is why damage is now measured on the destination path.

[TODO figures: figures/ecmp_baseline.png, figures/loaded_move_timeseries.png,
figures/chunk4_comparison.png and chunk4_tradeoff.png (analysis/chunk4.py).]

## 6. Preliminary comparison

[TODO Chunk 4: table ECMP vs ECMP-monitor-only vs Hedera-style vs
cooldown-only, mean ± 95 % CI over 10 paired seeds: elephant goodput mean and
worst, mice FCT p50/p99, reroutes/min, flaps/min, destination-path damage per
run. Source: `<campaign>/chunk4_summary.md`. Report the pre-registered
contrasts P1, P2, S1, S2 as written in LOG.md, including any that fail.]

## 7. Proposed improvement

Rule (controllers/cost.py): move f from p to q only if
`gain_bytes = clip(U_p - U_q - r_f, 0, link - r_f) x H > C_hat(f, p, q)` and
the flow was not moved in the last 5 s; H = 10 s, gain and cost both in
bytes over H. C_hat predicts the destination-path total deficit from features
the controller already logs (rate, path loads, elephants per path, delay gap,
age, bytes sent, previous moves, time since last move).

Two estimators: a hand-written formula whose structure is fixed in advance
and whose coefficients are fitted by least squares, and a gradient-boosted
tree (sklearn). Both are evaluated on held-out seeds against predict-the-mean,
by MAE, decision agreement with the measured damage, and decision time per
flow (`analysis/damage_model.py`). [TODO Chunks 5-6: dataset row count and
held-out MAE from `results/model/<n>/model_summary.md`; first 5-way comparison.]

## 8. Source code

https://github.com/MuhammadMuthar/sdn-reroute (README.md: install, run,
reproduce). Results site: https://sdn-reroute.vercel.app
