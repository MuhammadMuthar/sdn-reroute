# Stage 2 report: numbers

Written by `analysis/stage2_report.py` from the raw run files. Mean ± 95 % CI (t, n - 1 df), n = 10 seeded runs per controller, 30 s each, 4 elephants and about 1050 short flows per run.

| controller | elephant goodput, mean of 4 (Mbit/s) | worst elephant (Mbit/s) | short-flow FCT p50 (ms) | short-flow FCT p99 (ms) | reroutes / min |
|---|---|---|---|---|---|
| ECMP | 5.94 ± 1.04 | 4.60 ± 1.09 | 50.5 ± 6.7 | 1157 ± 63 | 0.0 ± 0.0 |
| Hedera-style | 8.54 ± 0.11 | 8.33 ± 0.16 | 63.3 ± 1.8 | 1131 ± 32 | 3.8 ± 1.6 |
| cooldown-only | 8.49 ± 0.11 | 8.31 ± 0.16 | 63.4 ± 2.4 | 1113 ± 16 | 4.8 ± 2.4 |
| cost-aware (formula) | 8.48 ± 0.19 | 8.30 ± 0.22 | 63.3 ± 1.7 | 1119 ± 27 | 5.2 ± 2.5 |
| cost-aware (ML) | 8.35 ± 0.22 | 8.16 ± 0.27 | 63.0 ± 2.2 | 1130 ± 17 | 7.6 ± 3.3 |

Paired by seed (difference, mean [95 % CI]):

- Hedera-style minus ECMP, eleph_goodput_mean: +2.59 [1.62, 3.56] Mbit/s
- Hedera-style minus ECMP, fct_p50_ms: +12.80 [5.58, 20.03] ms
- cost-aware (formula) minus Hedera-style, eleph_goodput_mean: -0.05 [-0.23, 0.13] Mbit/s
- cost-aware (ML) minus Hedera-style, eleph_goodput_mean: -0.18 [-0.39, 0.02] Mbit/s
- cost-aware (ML) minus Hedera-style, reroutes_per_min: +3.80 [1.24, 6.36] /min

Deliberate reroutes (60 move / no-move pairs; deficit = no-move minus move, Mbit/s, positive = the move cost goodput):

- moved flow, 2 s after the move: +0.042 ± 0.159
- destination path total, 2 s: -0.058 ± 0.063
- destination path total, 10 s: -0.010 ± 0.012
- moves within ±0.1 Mbit/s over 10 s: 58 of 60
- moves where the moved flow's socket saw reordering in the 2 s after: 24 of 60 (no-move arms: 0)

Damage models, error on held-out groups of moves (kB per move over 10 s, mean ± 95 % CI over 10 folds):

- mean: 29.0 ± 12.2
- formula: 29.9 ± 12.5
- tree: 30.5 ± 14.8
- gbr: 31.4 ± 12.9
