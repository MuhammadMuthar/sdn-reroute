# STATUS (rewritten at the end of every session)

Last session: Chunk 1, environment, 2026-10-19 (cloud container; laptop run pending).

## What works

- `setup.sh system` + `setup.sh venv` build the environment from scratch. The venv
  step was run in a clean Ubuntu 24.04 container (Python 3.13.16): 47 s, all pins
  resolve, os-ken 4.2.2 and the analysis stack import.
- `controllers/learning_switch.py` (OpenFlow 1.3 learning switch on os-ken) runs
  through `controllers/run.py`, our replacement for the removed `osken-manager`.
- `scripts/fake_switch.py` passes against the controller on Python 3.13 with
  os-ken 4.2.2 (and with os-ken 3.1.1 + osken-manager, for reference).
- `scripts/verify.sh` passes in the cloud container with Mininet 2.3.0 and
  Open vSwitch 3.3.9 in userspace-datapath mode (no OVS kernel module there):

  | run (results/verify/vm.csv) | pingall loss | iperf3 at 10 Mbps TCLink |
  |---|---|---|
  | 2026-10-19T11:38:11, userspace datapath | 0 % | 9.53 Mbps (95.3 %) |

  Environment snapshot: `results/env/vm-2026-10-19T113755.txt`.

## Key numbers

Only the one above. Nothing else has been measured.

## Not yet verified (needs the laptop, root, and the OVS kernel module)

- `sudo ./setup.sh system` on the real machine (Ubuntu 26.04.1, kernel 7.0, Python
  3.14.4 per the Remote Control session; apt mininet 2.3.0, openvswitch-switch 3.7.1).
- `sudo ./scripts/verify.sh` with the kernel datapath, which is what every
  experiment will use. Expect pingall 0 % loss and iperf3 >= 9 Mbps; the brief
  expects about 95 %.
- netem is missing in the cloud container (`sch_netem` not built in), so unequal
  path delays, the heart of Chunk 2, could not be exercised there. `sysinfo.sh`
  prints `ok: netem delay 1ms` on a machine where it will work.
- The pinned analysis wheels (numpy 2.5.4, pandas 3.0.6, scikit-learn 1.9.1,
  matplotlib 3.11.2, scipy 1.18.1) were resolved on Python 3.13. If they fail on
  3.14, run `PYTHON=python3.11 ./setup.sh venv` and note it in LOG.md.

## Next: Chunk 2, the reordering gate

Revised prompt in `docs/CHUNKS.md` section B (pending Mutahar's approval of the
Chunk 0 revisions; section A is the original). Before any run, write the pass
criterion in LOG.md. Reuse `disable_offloads()` from `topologies/smoke.py`.

## Open questions for Mutahar

1. Approve the Chunk 0 revisions (docs/CHUNKS.md section B)? Chunk 2 is written
   against them.
2. Does `sudo ./scripts/verify.sh` pass on the laptop? Paste the three RESULT/SMOKE
   lines or commit `results/`.
