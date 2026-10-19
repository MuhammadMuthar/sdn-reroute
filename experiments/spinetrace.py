"""Path oracle: which spine every flow actually used, from captures on the
ingress leaf's uplinks.

The runner captures headers on l1-s1 .. l1-s4. Each of those interfaces sees
the data that l1 sends up to spine k (after the 10 Mbit/s HTB, so at the
rate the link really carries) and whatever comes back down from spine k
(ACKs of left-to-right flows). Summaries written per run:

  spine_flows.csv  one row per (uplink, direction, 5-tuple): packets, data
                   packets, payload and wire bytes, first/last time, SYNs.
                   A flow whose data shows up on two uplinks was moved.
  spine_bins.csv   wire bytes per uplink and direction in 100 ms bins, the
                   link utilisation time series (wire bytes = Ethernet frame
                   length, what HTB counts).

Direction: "up" = l1 -> spine (frames the leaf transmits), "down" = spine ->
l1. Decided by the source IP being on leaf 1, so it does not depend on the
capture's direction metadata. Times are seconds since the run's t0.
"""

import collections
import csv
import struct

BIN_S = 0.1


def read_pcap(path):
    """(timestamp, original length, captured bytes) per record."""
    with open(path, "rb") as f:
        hdr = f.read(24)
        if len(hdr) < 24:
            return
        magic = struct.unpack("<I", hdr[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian = ">"
        else:
            raise ValueError("%s: not a classic pcap file" % path)
        nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
        rec_fmt = endian + "IIII"
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            ts_s, ts_frac, incl, orig = struct.unpack(rec_fmt, rec)
            yield ts_s + ts_frac * (1e-9 if nano else 1e-6), orig, f.read(incl)


def parse(frame):
    """(src_ip, dst_ip, sport, dport, flags, payload_len) for IPv4/TCP, else None."""
    if len(frame) < 54 or frame[12:14] != b"\x08\x00" or frame[23] != 6:
        return None
    ihl = (frame[14] & 0x0F) * 4
    total = struct.unpack("!H", frame[16:18])[0]
    src = "%d.%d.%d.%d" % tuple(frame[26:30])
    dst = "%d.%d.%d.%d" % tuple(frame[30:34])
    t = 14 + ihl
    sport, dport = struct.unpack("!HH", frame[t:t + 4])
    thl = (frame[t + 12] >> 4) * 4
    return src, dst, sport, dport, frame[t + 13], total - ihl - thl


def summarize(pcaps, t0, left_ips, out_flows, out_bins):
    """pcaps: {spine k: path of the l1-s<k> capture}."""
    flows = {}
    bins = collections.defaultdict(int)
    for k, path in sorted(pcaps.items()):
        for ts, orig, frame in read_pcap(path):
            p = parse(frame)
            if p is None:
                continue
            src, dst, sport, dport, flags, plen = p
            d = "up" if src in left_ips else "down"
            t = ts - t0
            key = (k, d, src, dst, sport, dport)
            r = flows.get(key)
            if r is None:
                r = flows[key] = [0, 0, 0, 0, t, t, 0]
            r[0] += 1
            if plen > 0:
                r[1] += 1
                r[2] += plen
            r[3] += orig
            r[5] = t
            if flags & 0x02:
                r[6] += 1
            bins[(k, d, int(t // BIN_S))] += orig
    with open(out_flows, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["spine", "dir", "src", "dst", "sport", "dport", "pkts", "data_pkts",
                    "payload_bytes", "wire_bytes", "t_first", "t_last", "syns"])
        for key in sorted(flows):
            r = flows[key]
            w.writerow(list(key) + r[:4] + ["%.6f" % r[4], "%.6f" % r[5], r[6]])
    with open(out_bins, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["spine", "dir", "t_bin", "wire_bytes"])
        for (k, d, b) in sorted(bins):
            w.writerow([k, d, "%.1f" % (b * BIN_S), bins[(k, d, b)]])
    return len(flows)
