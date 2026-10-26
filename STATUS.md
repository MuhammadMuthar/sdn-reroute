# STATUS (rewritten at the end of every session)

Last session: Chunks 4 and 5 on the laptop, 2026-10-26. Chunk 5 finish line
met: the damage dataset (results/dataset/dataset-2026-10-26/dataset/:
dataset.csv, data_dictionary.md, correlations.csv, summary.md) and its two
figures, 90 labelled rows. The headline: across 60 deliberately varied
reroutes, a move shifts goodput between flows for a couple of seconds but the
destination path as a whole loses nothing measurable. That needs a decision
before Chunk 6 (open question 1).

## What works

- Everything from Chunks 1-4 (rootless Mininet, the runner with host-NIC
  netem, ECMP / monitor-only / Hedera-style / cooldown-only controllers,
  matched move / no-move replays, `analysis/chunk4.py`).
- Chunk 5: `experiments/damage_design.py` (seeded deliberate reroutes: every
  ordered spine pair, load on both paths, rate cap, mice, previous move /
  flap; each trial a move arm and a matched no-move arm under the monitor-only
  controller, which records the features), runner options `bitrate` /
  `pacing` per elephant, `snapshots_last_only` and `--ovs-stats`, the monitor
  tracks runner-pinned flows, `analysis/damage_dataset.py` (dataset, labels,
  features, exclusions, summary, correlations, figures, plus the Chunk 4
  controller moves as extra rows), `analysis/upcall_check.py`.
- `scripts/chunk4_campaigns.sh`, `scripts/chunk5_campaign.sh` rerun each chunk.
- Tests: `.venv/bin/python -m unittest discover -s tests -t .` (31 pass).

## Key numbers

Chunk 5 dataset (design rows: 32 uncapped trials + 28 capped trials with a
true cap; deficit = no-move minus move, Mbit/s, mean ± 95 % CI):

| group | trials | moved flow, W2 | destination-path total, W2 | destination-path total, W10 |
|---|---|---|---|---|
| all | 60 | +0.04 ± 0.16 | -0.058 ± 0.063 | -0.010 ± 0.012 |
| slow -> fast | 25 | -0.27 ± 0.23 | -0.10 ± 0.15 | -0.017 ± 0.029 |
| fast -> slow | 25 | +0.32 ± 0.25 | -0.023 ± 0.054 | -0.004 ± 0.008 |
| 1 elephant on the destination | 25 | +0.20 ± 0.27 | -0.077 ± 0.035 | -0.016 ± 0.006 |

- 58 of 60 trials within ±0.1 Mbit/s at W10. The moves do reorder: the moved
  flow's socket saw a reordering in W2 in 14 / 25 slow->fast, 3 / 10
  same-delay and 7 / 25 fast->slow moves (0 / 60 no-move arms), plus about 4.5
  extra retransmissions; none of it shows in goodput over 2-10 s.
- Spearman r with the W10 total (60 design rows): predicted gain +0.53,
  unacked before the move -0.40, rate -0.38, elephants on the destination
  -0.38, delay gap -0.02; the label spans about ±0.03 Mbit/s for most trials.
- Pre-stated expectations: (1) moved term larger for slow->fast: opposite
  sign (as in Chunk 3); (2) total nearer zero than the moved term with a
  loaded destination: yes; (3) |total| < 0.5 Mbit/s: yes; (4) reordering in
  most slow->fast moves and few others: 56 % slow->fast, 28 % fast->slow.
- A flaw found and fixed post hoc: iperf3 `-b` is an average since the start,
  so a capped flow held below its cap catches up at full rate after the move
  (5 of 28 capped trials; it made the first campaign's n_q = 0 mean -0.46
  Mbit/s). The 28 capped trials were rerun with kernel pacing (`--fq-rate`);
  the as-run summary is kept (dataset-as-run/).

Chunk 4 (results/compare/compare-2026-10-26, damage-2026-10-26):
- Hedera-style vs ECMP: elephant goodput +2.59 Mbit/s [1.62, 3.56], mice FCT
  p50 +12.8 ms [5.6, 20.0]; cooldown-only the same; reroute damage at W10
  +0.02 ± 0.03 (Hedera-style), -0.09 ± 0.14 Mbit/s (cooldown-only).
- The monitoring's per-flow counting rules reorder about 470 of 1057 mice per
  run inside the leaf; Open vSwitch's own counters confirm the slow path
  (results/compare/upcall-2026-10-26/upcall_check.md: 5.86 vs 0.23 missed
  lookups per new flow, 1583 vs 0 revalidations from flow-table changes, 1831
  vs 0 duplicate upcalls per run).

Earlier chunks: TSQ artifact fixed by host-NIC netem (Chunk 3); loaded move:
the moved flow gains, the bystander pays, total -0.03 Mbit/s (Chunk 3).

Every Chunk 4 and Chunk 5 number above was recomputed from raw data by an
independent agent with its own code: all confirmed (LOG.md).

## Decisions made this session

1. Keep the Hedera-style monitoring for Chunks 5-6 (Mutahar, "continue",
   16:11 UTC).
2. Chunk 5 rate caps by kernel pacing instead of iperf3 -b (post hoc, this
   thread, LOG.md).
3. The optional Chunk 3 root cross-check (`scripts/chunk3_sudo.sh`) is
   dropped, not run (Mutahar, 21:11 UTC): Chunk 2's root cross-check
   (results/gate/sudo-check-2026-10-19) already showed that the rootless
   setup gives the same results as real root on the system OVS, and every
   chunk since used that same rootless setup. Nothing from Chunk 3 is open.

## Next

Chunk 6 (CHUNKS.md B): `analysis/damage_model.py --data
results/dataset/dataset-2026-10-26/dataset/dataset.csv` (13 groups: 12 spine
pairs + chunk4), then the 5-way comparison. But see open question 1 first.

Damage-regime pilot at 100 Mbit/s (results/dataset/regime-2026-10-26/
regime_pilot.md, 16 trials, not independently recomputed): slow -> fast
moves reorder 10-50x more than at 10 Mbit/s but the destination path still
loses nothing (W10 -0.02 % ± 0.09 of the link); only fast -> slow moves onto
an empty path cost something (the flow ramps to the larger BDP: 6-12 % of the
link in W2, 1-2 % over W10, n = 2). By the pre-stated rule Chunk 6 runs as
planned (10 Mbit/s).

Chunk 6 (results/compare/five-way-2026-10-26/chunk6_summary.md): no damage
model beats predicting the mean (MAE about 29 kB per move at W10); both
cost-aware controllers keep Hedera-style's gain over ECMP (+2.5 Mbit/s) and
do not differ from Hedera-style or cooldown-only (formula minus Hedera-style
-0.05 [-0.23, 0.13] Mbit/s). Five controllers run through one runner. Not
independently recomputed yet.

## Open questions for Mutahar

1. (Pilot done, see above; Chunk 6 as planned unless you prefer 100 Mbit/s.) The damage label is about zero on this setup (10 Mbit/s, 1-10 ms paths,
   CUBIC with RACK and DSACK undo). A cost-aware controller trained on it will
   predict C_hat about 0 and behave like cooldown-only. Options: (a) run Chunk
   6 as planned and report that honestly; (b) first look for the regime where
   damage exists (higher link rate, e.g. 50-100 Mbit/s, or larger delay gaps,
   so more packets are reordered than RACK's window absorbs), one pilot campaign;
   (c) both, (b) first. Recommendation: (c).
2. Paper framing: TSQ plus the monitoring artifact as one methods section,
   and "reroute damage is small on modern Linux TCP" as a finding?
