# Damage model evaluation

Data: results/dataset/dataset7b-2026-11-14/dataset/dataset.csv (sha256 25b55cfaacfa3682), 59 rows, 12 groups by `group`, split GroupKFold(10). Target dest_total_deficit_W10 (bytes over 10 s). MAE in kB (1 kB = 1000 bytes), mean ± 95 % CI over folds. Agreement = share of held-out rows where the rule decides as it would with the measured damage.

| target | estimator | MAE kB | pooled MAE kB | agreement |
|---|---|---|---|---|
| moved_deficit_W10 | mean | 13654.6 ± 4452.0 | 13526.5 |  |
| moved_deficit_W10 | formula | 11481.5 ± 4457.1 | 11483.8 |  |
| moved_deficit_W10 | tree | 13914.7 ± 8486.3 | 13623.0 |  |
| moved_deficit_W10 | gbr | 14027.6 ± 6765.2 | 14185.1 |  |
| bystander_deficit_W10 | mean | 9442.7 ± 5524.6 | 9139.7 |  |
| bystander_deficit_W10 | formula | 10165.1 ± 5800.0 | 9933.4 |  |
| bystander_deficit_W10 | tree | 10370.4 ± 5365.4 | 9936.2 |  |
| bystander_deficit_W10 | gbr | 10320.2 ± 5294.3 | 9914.0 |  |
| dest_total_deficit_W10 | mean | 2875.0 ± 1893.5 | 2804.6 | 0.40 ± 0.15 |
| dest_total_deficit_W10 | formula | 3332.5 ± 2445.8 | 3124.3 | 0.41 ± 0.15 |
| dest_total_deficit_W10 | tree | 3144.1 ± 1790.8 | 3198.6 | 0.43 ± 0.15 |
| dest_total_deficit_W10 | gbr | 3607.8 ± 2023.6 | 3508.2 | 0.39 ± 0.14 |

Decision time per flow (cost.decide_move, saved estimators):

- formula: median 0.009 ms, p99 0.023 ms (n = 500)
- gbr: median 0.605 ms, p99 1.058 ms (n = 500)
