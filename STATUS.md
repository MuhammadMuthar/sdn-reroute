# STATUS (rewritten at the end of every session)

Last session: Chunk 1, environment, laptop run, 2026-10-19. Chunk 1 is done.

## What works

- The laptop (HP EliteBook 840 G8, Ubuntu 26.04.1, kernel 7.0.0-38, i5-1145G7,
  8 threads, 15.5 GB RAM) has Mininet 2.3.0, Open vSwitch 3.7.1 with the kernel
  module loaded, iperf3 3.20, tshark 4.6.6, and qdiscs htb, netem, tbf and fq_codel.
- `./setup.sh venv` builds `.venv` on Python 3.14.4 in about 70 s with every pin
  in requirements.txt; os-ken 4.2.2 and numpy/pandas/matplotlib/sklearn import.
- `controllers/run.py` + `controllers/learning_switch.py` pass
  `scripts/fake_switch.py` on Python 3.14.
- `sudo ./scripts/verify.sh` passes on the laptop with the kernel datapath.

## Key numbers

| run | datapath | pingall loss | iperf3 on 10 Mbps TCLinks |
|---|---|---|---|
| laptop 2026-10-19T17:33:43 (same CSV, after the script fixes) | kernel | 0 % | 9.512 Mbps (95.1 %) |
| laptop 2026-10-19T17:12:23 (`results/verify/muhammad-muthar-HP-EliteBook-840-G8-Notebook-PC.csv`) | kernel | 0 % | 9.452 Mbps (94.5 %) |
| cloud 2026-10-19T11:38:11 (`results/verify/vm.csv`) | userspace | 0 % | 9.53 Mbps (95.3 %) |

Environment snapshots are in `results/env/`. Nothing else has been measured.

## Changed this session

- `scripts/sysinfo.sh` no longer records `net.ipv4.tcp_fastopen_key` (a secret).
- `scripts/verify.sh` hands results/verify and results/env back to `$SUDO_USER`.
- `setup.sh` needed no fixes on Ubuntu 26.04 / Python 3.14.

## Next: Chunk 2, the reordering gate

Revised prompt in `docs/CHUNKS.md` section B (pending Mutahar's approval of the
Chunk 0 revisions; section A is the original). netem works on the laptop, so
unequal path delays can be built here. Before any run, write the pass criterion
in LOG.md. Reuse `disable_offloads()` from `topologies/smoke.py`.

## Open questions for Mutahar

1. Approve the Chunk 0 revisions (docs/CHUNKS.md section B)? Chunk 2 is written
   against them.
2. verify.sh's chown now sets the group explicitly (uutils chown quirk, see LOG.md);
   the next sudo run will confirm it. Harmless if it doesn't: files are already yours.
