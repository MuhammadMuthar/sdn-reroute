#!/usr/bin/env python3
"""Short-flow ("mice") server and client. Standard library only, so it runs
with the system python3 inside the Mininet hosts.

  python3 experiments/mice.py serve  --port 8000
  python3 experiments/mice.py client --schedule S.json --host h2 --t0 EPOCH --out m.csv

One mouse = one TCP connection from a fixed source port (from the seeded
schedule, so the ECMP hash of every mouse is reproducible): connect, send
`size` bytes, half-close, wait for the server's 1-byte reply, close.
The server replies once it has read everything up to EOF, so

  FCT = time from just before connect() to the reply byte arriving

which covers the handshake, all data and one return trip, the usual
definition for request/response short flows. The client writes one CSV row
per mouse: id, scheduled and actual start (epoch s), FCT, connect time
(SYN to SYN-ACK), bytes, ok, error.
"""

import argparse
import csv
import json
import socket
import threading
import time

CHUNK = 65536
TIMEOUT_S = 20.0


def serve(port):
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("0.0.0.0", port))
    ls.listen(512)

    def handle(c):
        try:
            c.settimeout(TIMEOUT_S * 2)
            while c.recv(CHUNK):
                pass
            c.sendall(b"x")
        except OSError:
            pass
        finally:
            c.close()

    while True:
        c, _ = ls.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def one_mouse(m, src_ip, t_sched, rows, lock):
    row = dict(id=m["id"], t_sched=t_sched, size=m["size"], ok=0, error="")
    payload = memoryview(b"\0" * min(m["size"], CHUNK))
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((src_ip, m["sport"]))
        s.settimeout(TIMEOUT_S)
        t_start = time.time()
        row["t_start"] = t_start
        s.connect((m["dst_ip"], m["dport"]))
        row["connect_s"] = time.time() - t_start
        left = m["size"]
        while left > 0:
            n = s.send(payload[:min(left, len(payload))])
            left -= n
        s.shutdown(socket.SHUT_WR)
        if s.recv(1) != b"x":
            raise OSError("no reply byte")
        row["fct_s"] = time.time() - t_start
        row["ok"] = 1
    except OSError as exc:
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        s.close()
    with lock:
        rows.append(row)


def client(schedule, host, t0, out):
    sched = json.load(open(schedule))
    mine = sorted((m for m in sched["mice"] if m["src"] == host), key=lambda m: m["start"])
    src_ip = sched["hosts"][host]
    rows, lock, threads = [], threading.Lock(), []
    for m in mine:
        t_sched = t0 + m["start"]
        delay = t_sched - time.time()
        if delay > 0:
            time.sleep(delay)
        th = threading.Thread(target=one_mouse, args=(m, src_ip, t_sched, rows, lock),
                              daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(TIMEOUT_S + 5)
    cols = ["id", "t_sched", "t_start", "fct_s", "connect_s", "size", "ok", "error"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["id"]):
            w.writerow({k: r.get(k, "") for k in cols})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8000)
    p = sub.add_parser("client")
    p.add_argument("--schedule", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--t0", type=float, required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.mode == "serve":
        serve(a.port)
    else:
        client(a.schedule, a.host, a.t0, a.out)


if __name__ == "__main__":
    main()
