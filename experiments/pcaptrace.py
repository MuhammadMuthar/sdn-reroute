"""Turn a tcpdump capture of one TCP connection into a compact packet trace.

Pure Python (no dpkt/scapy), classic libpcap format, Ethernet + IPv4 + TCP.
Output rows (CSV, gzip): t_us, dir, seq, ack, len, flags, sack
  t_us   microseconds since `t0` (wall clock, same clock as the controller)
  dir    0 = from the data sender (sport == the given sender port), 1 = reverse
  seq    sequence number relative to that direction's first SYN/packet
  ack    ack number relative to the other direction's ISN (0 if no ACK flag)
  len    TCP payload bytes
  flags  TCP flags byte
  sack   SACK blocks relative to the data ISN, "l-r;l-r", empty if none

Used by experiments/gate.py after every trial; the traces are committed in
results/ (the pcaps are not, they are large and .gitignored).
"""

import csv
import gzip
import struct


def read_pcap(path):
    with open(path, "rb") as f:
        hdr = f.read(24)
        magic = struct.unpack("<I", hdr[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian = ">"
        else:
            raise ValueError("%s: not a classic pcap file" % path)
        nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
        linktype = struct.unpack(endian + "I", hdr[20:24])[0]
        if linktype != 1:
            raise ValueError("%s: linktype %d, expected Ethernet" % (path, linktype))
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            ts_s, ts_frac, incl, _orig = struct.unpack(endian + "IIII", rec)
            data = f.read(incl)
            yield ts_s + ts_frac * (1e-9 if nano else 1e-6), data


def parse_tcp(frame):
    """(src_port, dst_port, seq, ack, flags, payload_len, sack_blocks) or None."""
    if len(frame) < 14 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0x0F) * 4
    if ip[9] != 6:
        return None
    total_len = struct.unpack("!H", ip[2:4])[0]
    tcp = ip[ihl:]
    sport, dport, seq, ack, off_flags = struct.unpack("!HHIIH", tcp[:14])
    thl = (off_flags >> 12) * 4
    flags = off_flags & 0xFF
    payload = total_len - ihl - thl
    sacks = []
    opts = tcp[20:thl]
    i = 0
    while i < len(opts):
        kind = opts[i]
        if kind == 0:
            break
        if kind == 1:
            i += 1
            continue
        if i + 1 >= len(opts):
            break
        olen = opts[i + 1]
        if olen < 2:
            break
        if kind == 5:
            body = opts[i + 2:i + olen]
            for j in range(0, len(body) - 7, 8):
                sacks.append(struct.unpack("!II", body[j:j + 8]))
        i += olen
    return sport, dport, seq, ack, flags, payload, sacks


def trace(pcap_path, sender_port, t0):
    """Yield trace rows for the connection whose data sender uses sender_port."""
    isn = {}
    for ts, frame in read_pcap(pcap_path):
        p = parse_tcp(frame)
        if p is None:
            continue
        sport, dport, seq, ack, flags, plen, sacks = p
        if sender_port not in (sport, dport):
            continue
        d = 0 if sport == sender_port else 1
        if d not in isn:
            isn[d] = seq
        rel_ack = 0
        if flags & 0x10 and (1 - d) in isn:
            rel_ack = (ack - isn[1 - d]) & 0xFFFFFFFF
        sack = ""
        if sacks and 0 in isn:
            sack = ";".join("%d-%d" % ((l - isn[0]) & 0xFFFFFFFF, (r - isn[0]) & 0xFFFFFFFF)
                            for l, r in sacks)
        yield (int(round((ts - t0) * 1e6)), d, (seq - isn[d]) & 0xFFFFFFFF, rel_ack,
               plen, flags, sack)


def write_trace(pcap_path, out_path, sender_port, t0, dirs=(0, 1)):
    n = 0
    with gzip.open(out_path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_us", "dir", "seq", "ack", "len", "flags", "sack"])
        for row in trace(pcap_path, sender_port, t0):
            if row[1] in dirs:
                w.writerow(row)
                n += 1
    return n
