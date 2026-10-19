#!/usr/bin/env python3
"""Controller smoke test without Mininet or OVS: a fake OpenFlow 1.3 switch.

Connects to the controller, completes the HELLO / FEATURES handshake, sends
two PACKET_INs (h1->h2, then h2->h1) and checks that the learning switch
answers with a table-miss FLOW_MOD, a FLOOD PACKET_OUT, and finally a
unicast FLOW_MOD + PACKET_OUT once it has learned h1's port.

This only proves the controller app logic runs on this Python/os-ken
combination. It says nothing about real forwarding; verify.sh does that.

Usage: python scripts/fake_switch.py [host] [port]   (exit 0 = pass)
"""

import socket
import struct
import sys

OFP_VERSION = 0x04
T_HELLO, T_FEATURES_REQUEST, T_FEATURES_REPLY = 0, 5, 6
T_PACKET_IN, T_PACKET_OUT, T_FLOW_MOD = 10, 13, 14
T_SET_CONFIG, T_BARRIER_REQUEST, T_BARRIER_REPLY = 9, 20, 21
T_ECHO_REQUEST, T_ECHO_REPLY, T_MULTIPART_REQUEST = 2, 3, 18
OFPP_FLOOD = 0xFFFFFFFB
OFPP_CONTROLLER = 0xFFFFFFFD


def hdr(mtype, length, xid):
    return struct.pack("!BBHI", OFP_VERSION, mtype, length, xid)


def recv_msg(sock):
    head = b""
    while len(head) < 8:
        chunk = sock.recv(8 - len(head))
        if not chunk:
            raise ConnectionError("controller closed the connection")
        head += chunk
    ver, mtype, length, xid = struct.unpack("!BBHI", head)
    body = b""
    while len(body) < length - 8:
        body += sock.recv(length - 8 - len(body))
    return mtype, xid, body


def eth_frame(dst, src):
    return dst + src + b"\x08\x00" + b"\x00" * 46  # IPv4 ethertype, padded


def packet_in(xid, in_port, frame):
    # OFPT_PACKET_IN: buffer_id, total_len, reason, table_id, cookie, match
    # match: type=1 (OXM), length, OXM in_port (class 0x8000, field 0, len 4)
    oxm = struct.pack("!HBBI", 0x8000, 0 << 1, 4, in_port)
    match = struct.pack("!HH", 1, 4 + len(oxm)) + oxm
    match += b"\x00" * ((8 - len(match) % 8) % 8)
    body = struct.pack("!IHBBQ", 0xFFFFFFFF, len(frame), 0, 0, 0)
    body += match + b"\x00\x00" + frame
    return hdr(T_PACKET_IN, 8 + len(body), xid) + body


def out_port_of_packet_out(body):
    # buffer_id(4) in_port(4) actions_len(2) pad(6) actions...
    actions_len = struct.unpack("!H", body[8:10])[0]
    act = body[16:16 + actions_len]
    atype, alen, port = struct.unpack("!HHI", act[:8])
    return port


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6653
    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall(hdr(T_HELLO, 8, 1))
    seen = []

    def expect(wanted, max_msgs=20):
        for _ in range(max_msgs):
            mtype, xid, body = recv_msg(sock)
            seen.append(mtype)
            if mtype == T_FEATURES_REQUEST:
                reply = struct.pack("!QIBBHII", 0x1, 256, 1, 0, 0, 0x4F, 0)
                sock.sendall(hdr(T_FEATURES_REPLY, 8 + len(reply), xid) + reply)
            elif mtype == T_ECHO_REQUEST:
                sock.sendall(hdr(T_ECHO_REPLY, 8 + len(body), xid) + body)
            elif mtype == T_BARRIER_REQUEST:
                sock.sendall(hdr(T_BARRIER_REPLY, 8, xid))
            elif mtype == T_MULTIPART_REQUEST:
                # port-desc request from ofp_handler; answer with an empty list
                mp_type = struct.unpack("!H", body[:2])[0]
                sock.sendall(hdr(T_MULTIPART_REQUEST + 1, 16, xid)
                             + struct.pack("!HH4x", mp_type, 0))
            if mtype == wanted:
                return body
        raise AssertionError("did not get type %d, saw %s" % (wanted, seen))

    expect(T_HELLO)
    fm = expect(T_FLOW_MOD)
    assert struct.unpack("!H", fm[22:24])[0] == 0, "first flow-mod not table-miss"
    print("ok: handshake done, table-miss flow installed")

    h1, h2 = b"\x00\x00\x00\x00\x00\x01", b"\x00\x00\x00\x00\x00\x02"
    sock.sendall(packet_in(100, 1, eth_frame(h2, h1)))
    po = expect(T_PACKET_OUT)
    assert out_port_of_packet_out(po) == OFPP_FLOOD, "expected FLOOD"
    print("ok: unknown destination flooded")

    sock.sendall(packet_in(101, 2, eth_frame(h1, h2)))
    fm = expect(T_FLOW_MOD)
    assert struct.unpack("!H", fm[22:24])[0] == 1, "learned flow priority != 1"
    po = expect(T_PACKET_OUT)
    assert out_port_of_packet_out(po) == 1, "expected unicast to port 1"
    print("ok: learned h1 on port 1, installed unicast flow")
    print("PASS fake_switch: controller logic works on this interpreter")
    sock.close()


if __name__ == "__main__":
    main()
