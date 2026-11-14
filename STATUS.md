# STATUS (rewritten at the end of every session)

Last session: Chunk 7a (damage-regime campaign) on the laptop, 2026-11-13/14,
branch claude/chunk7-regime (from claude/stage2-milestone-report, not merged).
Finish line met: results/regime/summary/regime_cells.md (+ .csv) and
figures/regime_damage.png give, for 64 cells (16 settings x 4 move types, 5
matched pairs each) and the repeated-move runs, the goodput one reroute costs
the destination path, with 95 % CIs and the pre-registered verdict.

Headline: reordering from a reroute costs nothing measurable in any condition
tried (10 and 100 Mbit/s, cubic and bbr, RACK on and off, 1-10 or 1-50 ms
paths, moves every 0.5 s). The only damage is a fast -> slow move onto an
EMPTY path with a large RTT gap (1 -> 50 ms one-way): 19-77 % of the link
over 10 s, in all 8 such cells, because the window (cubic) or the RTT
estimate (bbr) is sized for the old path. Onto a loaded path the same move
costs the path nothing (the share shifts).

## What works

- Everything from Chunks 1-6 (rootless Mininet, runner, five controllers,
  matched move / no-move pairs, damage dataset, models, 5-way comparison).
- Chunk 7a: runner schedule keys `spine_delay_ms` and `sysctl` (set and read
  back in every host namespace, logged in meta.json), `--keep-pcaps`;
  `damage_design.py --sysctl --spine-delays --placed-only --flap --alone`;
  `scripts/chunk7_campaign.sh` (feasibility gate per setting, ~12 h);
  `analysis/regime.py` (cells, verdicts, repeated moves, feasibility, figure).
- BBR needs `sudo modprobe tcp_bbr` once per boot (it was loaded on boot
  a4dff7ba).
- Tests: `.venv/bin/python -m unittest discover -s tests -t .` (34 pass).

## Key numbers (results/regime/summary/regime_cells.md)

Destination-path total deficit over 10 s after the move, % of link, mean ±
95 % CI, n = 5:

| move | rate | delays | cubic RACK on | cubic RACK off | bbr RACK on | bbr RACK off |
|---|---|---|---|---|---|---|
| fast -> slow, empty q | 100 | 1/1/20/50 | **54.9 ± 9.3** | **48.7 ± 23.7** | **77.1 ± 9.6** | **47.6 ± 45.8** |
| fast -> slow, empty q | 10 | 1/1/20/50 | **25.9 ± 10.8** | **19.3 ± 13.4** | **31.8 ± 29.2** | **33.7 ± 27.5** |
| fast -> slow, empty q | 100 | 1/1/5/10 | 4.0 ± 5.5 | 1.4 ± 1.4 | 12.3 ± 20.5 | 10.6 ± 24.3 |
| fast -> slow, empty q | 10 | 1/1/5/10 | 0.05 ± 0.20 | 0.09 ± 0.28 | 1.0 ± 2.1 | 2.2 ± 3.8 |

Bold = damage by the pre-registered rule (CI above 0 and >= 1 % of the link).
All other 56 cells (slow -> fast, and any move onto a loaded path) have no
damage; one has a CI above zero below the floor (100 bbr RACK on D1 fast ->
slow loaded, +0.71 ± 0.51 %). Slow -> fast moves reorder heavily (up to 325
reorderings and 555 retransmissions in 2 s, cell means) without any goodput loss.
Repeated moves s1 <-> s4 (D1, cubic, RACK on), E1's goodput share of the
no-move run: 100 Mbit/s 0.9961 ± 0.0058 (every 0.5 s), 0.9962 ± 0.0059 (2 s);
10 Mbit/s 0.9924 ± 0.0093, 0.9994 ± 0.0008: no damage.

Damaged cells cross-checked against iperf3's own receiver reports (agree
within 0.6 Mbit/s per trial); not independently recomputed by a separate
agent. Expectations that failed: RACK off did not create damage, bbr is not
less damaged than cubic, and repeated moves cost < 1 %.

Earlier: Chunk 5 (10 Mbit/s, varied reroutes) W10 total -0.010 ± 0.012
Mbit/s; Chunk 6: no damage model beats the mean, both cost-aware controllers
match Hedera-style (+2.5 Mbit/s over ECMP).

## Next

Chunk 7a found a regime with real damage; the comparison was not started
(as instructed). Recommendation: run the cost-aware vs Hedera-style
comparison with a large RTT asymmetry (the 1/1/20/50 ms set, or a WAN-like
topology) at 100 Mbit/s, with workloads where the least-loaded path is often
the long, empty one; add features for the RTT ratio q/p, cwnd (or rate x
RTT_p) against q's BDP, and whether q is empty. Then the fat-tree and the
AI-training traffic (Stage 3 plan).

## Open questions for Mutahar

1. Is a 1 vs 50 ms path gap an acceptable setting for the comparison? It is
   WAN-like, not a datacenter fabric; with 1-10 ms gaps there is still no
   damage to predict.
2. Merge claude/chunk7-regime (it includes the Stage 2 report branch)?
   It adds about 3 GB of receiver traces to the repository.
3. Independent recomputation of the Chunk 7a table by a separate agent
   (the project's usual check) before the numbers go into a report?
