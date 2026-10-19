# LOG (lab notebook, append only)

## 2026-10-19 Chunk 1: frozen environment

Run in a cloud Ubuntu 24.04.5 container (Python 3.13.16, root, kernel
6.18.44 without loadable modules) because the laptop Claude Code session had
no credits. Laptop facts relayed from a later Remote Control session: Ubuntu
26.04.1, kernel 7.0.0-38, Python 3.14.4 (python3.11 also present), 8 cores,
14 GB RAM, openvswitch kernel module available, apt has mininet 2.3.0,
openvswitch-switch 3.7.1, iperf3 3.20, tshark 4.6.6, python3-os-ken 4.1.1.

### Decision: os-ken 4.2.2 in a venv, with our own launcher

Options considered:
1. Ryu 4.34 in a Python 3.9 venv with eventlet 0.30.2. Rejected: the laptop has
   no 3.9, pyenv adds a toolchain build, Ryu is unmaintained since 2020, and
   docs/research_gap.md cites reports that the eventlet pin does not fix 3.10+.
2. Distro `python3-os-ken` 4.1.1. Rejected: ties us to the system Python,
   cannot be pinned in requirements.txt, and also has no manager CLI.
3. `pip install os-ken==4.2.2` (Aug 2026, classifiers 3.10-3.13). Chosen.

Finding while testing: os-ken 4.0.0 and later ship no `osken-manager` console
script and no `os_ken.cmd` package (checked the wheels for 3.1.1, 4.0.0, 4.1.2,
4.2.2; only 3.1.1 has them). The library part (app_manager, controller,
ofproto, packet) is unchanged. So `controllers/run.py` reimplements the ~30
lines of the CLI (hub.patch, cfg.CONF, AppManager.load_apps / create_contexts /
instantiate_apps, hub.joinall). It accepts the usual flags.

Both stacks were exercised with `scripts/fake_switch.py` (handshake, table-miss
flow, flood, learn, unicast flow): PASS with os-ken 4.2.2 + run.py and with
os-ken 3.1.1 + osken-manager, both on Python 3.13.16.

### Mininet in the container

- `apt install mininet openvswitch-switch iperf3 iperf ethtool iputils-ping
  telnet` worked. `ovs-ctl start` works without the kernel module; Mininet
  switches must then use `OVSSwitch(datapath='user')`. The kernel-datapath run
  hangs at bridge creation ("failed to create datapath ovs-system").
- First pingall showed 100 % loss: `ping` was not installed in the container.
  Not a networking fault. Added iputils-ping to setup.sh.
- Mininet's built-in `--test iperf` hung: it waits for `telnet` to confirm the
  server is listening. Added telnet to setup.sh; our smoke test uses iperf3
  with its own retry loop instead.
- TCP did not connect at all although ICMP did: flows showed h1->h2 SYNs
  forwarded (12 packets) and nothing back from h2. Cause: tx checksum offload
  on the host veth with the userspace datapath. `ethtool -K <intf> tx off rx
  off tso off gso off gro off` on every host and switch port fixed it. We wanted
  offloads off anyway for captures (CLAUDE.md pitfalls), so smoke.py now does
  it for all ports at start; later topologies should reuse `disable_offloads`.
- Available qdiscs here: htb, tbf, pfifo. Missing: netem, fq_codel. So TCLink
  bandwidth works in the container, delay does not. Chunk 2 cannot run here.

### Finish line

`sudo ./scripts/verify.sh` (controller from the venv, smoke topology, pingall,
10 s idle iperf3 on 10 Mbps TCLinks), cloud container, userspace datapath:
pingall 0 % loss, iperf3 9.53 Mbps = 95.3 % of 10 Mbps. Row in
results/verify/vm.csv, logs beside it, env snapshot in results/env/. An earlier
manual run of the same script gave 9.42 Mbps (94.2 %). Pass threshold in the
script is 90 % so a slower laptop does not fail on noise; the number is always
printed and logged.

The laptop run (kernel datapath, real sysctls, netem present) is still owed.

## 2026-10-19 Chunk 1 (laptop): native run on the HP EliteBook

Remote Control session on the laptop itself. Machine: Ubuntu 26.04.1 LTS,
kernel 7.0.0-38-generic, Intel i5-1145G7 (8 threads), 15.5 GB RAM,
Python 3.14.4. Snapshot: `results/env/muhammad-muthar-HP-EliteBook-840-G8-Notebook-PC-2026-10-19T171202.txt`.

### What was run

- System packages: Mutahar ran by hand (I have no sudo password)
  `sudo apt-get install -y mininet openvswitch-switch openvswitch-testcontroller iperf3 tshark python3-os-ken`
  before the repo existed. Afterwards every package in `setup.sh`'s APT_PKGS
  was present (`dpkg -s`), the openvswitch kernel module was loaded and
  `openvswitch-switch` active, so `sudo ./setup.sh system` itself was not run
  on the laptop. python3-os-ken from that command is unused (the venv has its
  own os-ken); harmless.
- `./setup.sh venv` on Python 3.14.4: passed first try in 70 s. All pins in
  requirements.txt have cp314 wheels; no python3.11 fallback needed.
- `scripts/fake_switch.py 127.0.0.1 6655` against `controllers/run.py` +
  `learning_switch.py`: PASS on 3.14.
- `sudo ./scripts/verify.sh` (run by Mutahar, kernel datapath):
  pingall 0 % loss, iperf3 9.452 Mbps = 94.5 % of 10 Mbps, SMOKE PASS.
  Row in `results/verify/muhammad-muthar-HP-EliteBook-840-G8-Notebook-PC.csv`.
- sysinfo confirms netem, htb, tbf and fq_codel all work here, so Chunk 2's
  unequal path delays can run on this machine.

### Fixes

- `scripts/sysinfo.sh` dumped `net.ipv4.tcp_fastopen_key`, a per-boot secret,
  into results/env (the repo is public). It now skips that key. The one laptop
  snapshot already written had the value replaced with a `<redacted ...>`
  marker; nothing else in it was touched. Also silenced tshark's
  "running as root" warning that replaced its version line.
- `scripts/verify.sh` ran as root, so its results/ files came out root-owned
  and git/analysis could not edit them as the user. It now chowns
  results/verify and results/env back to `$SUDO_USER` on exit. (Today's files
  were re-owned by copying them within the user-owned directory.)
- `setup.sh` needed no change on Ubuntu 26.04 / Python 3.14.

### Not done

- verify.sh was run once on the laptop (plus the cloud run). The fixed
  verify.sh/sysinfo.sh have only been syntax-checked and run unprivileged,
  not re-run under sudo.

### Re-run after the fixes (2026-10-19T17:33)

`sudo ./scripts/verify.sh` again: pingall 0 % loss, iperf3 9.512 Mbps =
95.1 % of 10 Mbps, SMOKE PASS (second row in the laptop CSV). The new env
snapshot has no tcp_fastopen_key line. Result files came out owned by
muhammad-muthar but with group root: Ubuntu 26.04's `chown` is the Rust
uutils one (/usr/lib/cargo/bin/coreutils/chown), which does not give
`user:` the GNU meaning "user's login group". verify.sh now passes the group
explicitly (`$OWNER:$(id -gn $OWNER)`). Not re-run under sudo after that edit.
