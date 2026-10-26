# Damage model evaluation

Data: results/dataset/dataset-2026-10-26/dataset/dataset.csv (sha256 1275997a3b8a5b3f), 90 rows, 13 groups by `group`, split GroupKFold(10). Target dest_total_deficit_W10 (bytes over 10 s). MAE in kB (1 kB = 1000 bytes), mean ± 95 % CI over folds. Agreement = share of held-out rows where the rule decides as it would with the measured damage.

| target | estimator | MAE kB | pooled MAE kB | agreement |
|---|---|---|---|---|
| moved_deficit_W10 | mean | 246.8 ± 99.8 | 212.3 |  |
| moved_deficit_W10 | formula | 271.8 ± 90.0 | 222.7 |  |
| moved_deficit_W10 | tree | 262.0 ± 98.3 | 219.1 |  |
| moved_deficit_W10 | gbr | 306.5 ± 88.8 | 359.7 |  |
| bystander_deficit_W10 | mean | 229.4 ± 106.2 | 170.4 |  |
| bystander_deficit_W10 | formula | 239.4 ± 105.0 | 178.3 |  |
| bystander_deficit_W10 | tree | 256.5 ± 110.8 | 192.7 |  |
| bystander_deficit_W10 | gbr | 254.1 ± 106.7 | 189.3 |  |
| dest_total_deficit_W10 | mean | 29.0 ± 12.2 | 39.7 | 0.52 ± 0.17 |
| dest_total_deficit_W10 | formula | 29.9 ± 12.5 | 40.7 | 0.52 ± 0.17 |
| dest_total_deficit_W10 | tree | 30.5 ± 14.8 | 43.6 | 0.52 ± 0.17 |
| dest_total_deficit_W10 | gbr | 31.4 ± 12.9 | 41.7 | 0.52 ± 0.17 |

Decision time per flow (cost.decide_move, saved estimators):

- formula: median 0.002 ms, p99 0.004 ms (n = 500)
- gbr: median 0.188 ms, p99 0.239 ms (n = 500)
