# Chunk 7a cross-check: fast -> slow onto an empty path

Written by `analysis/regime_crosscheck.py`. Deficit over 10 s, % of the link, mean ± 95 % CI. Left: recomputed here from iperf3 receiver reports. Right: analysis/regime.py (packet traces).

| rate | cc | RACK | delays | n | iperf3 recomputation | regime.py | verdict there |
|---|---|---|---|---|---|---|---|
| 100 | cubic | on | D2 | 5 | 54.8 ± 9.2 | 54.9 ± 9.3 | DAMAGE |
| 100 | cubic | off | D2 | 5 | 48.5 ± 24.0 | 48.7 ± 23.7 | DAMAGE |
| 10 | cubic | on | D2 | 5 | 23.9 ± 10.4 | 25.9 ± 10.8 | DAMAGE |
| 10 | cubic | off | D2 | 5 | 18.3 ± 12.4 | 19.3 ± 13.4 | DAMAGE |
| 10 | bbr | on | D2 | 5 | 31.6 ± 29.6 | 31.8 ± 29.2 | DAMAGE |
| 10 | bbr | off | D2 | 5 | 33.1 ± 28.8 | 33.7 ± 27.5 | DAMAGE |
| 100 | cubic | on | D1 | 5 | 3.9 ± 5.4 | 4.0 ± 5.5 | no damage |
| 100 | cubic | off | D1 | 5 | 1.3 ± 1.4 | 1.4 ± 1.4 | no damage |
| 10 | cubic | on | D1 | 5 | -0.0 ± 0.9 | 0.1 ± 0.2 | no damage |
| 10 | cubic | off | D1 | 5 | -0.1 ± 0.7 | 0.1 ± 0.3 | no damage |
| 10 | bbr | on | D1 | 5 | 0.8 ± 2.1 | 1.0 ± 2.1 | no damage |
| 10 | bbr | off | D1 | 5 | 2.6 ± 3.9 | 2.2 ± 3.8 | no damage |

Largest difference between the two cell means: 1.98 points.
