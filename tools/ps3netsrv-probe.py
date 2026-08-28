#!/usr/bin/env python3
"""Measure a live ps3netsrv the way a PS3 experiences it.

Speaks the netiso wire protocol and issues READ_FILE_CRITICAL requests against
a real image, sequentially and then at random offsets. The random pass is the
one that matters: game data access is seeky, and on a spinning disk each seek
is paid in full before any byte reaches the console, because the server reads
and sends strictly serially with no readahead.

Usage:
  ps3netsrv-probe.py HOST PORT /PS3ISO/YourGame.iso

A PS3 Blu-ray drive tops out near 9 MB/s. Anything below that in the random
pass will stutter in game.
"""

import random
import socket
import statistics
import struct
import sys

BD_MB_S = 9.0            # what a PS3 Blu-ray drive sustains
REQ = 64 * 1024          # a representative console read
DURATION = 6.0           # seconds per pass


def recv_exact(sock, size):
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(min(1 << 20, size - len(buf)))
        if not chunk:
            raise ConnectionError(
                f"server closed the connection after {len(buf)} of {size} bytes"
            )
        buf += chunk
    return bytes(buf)


def open_image(sock, path):
    name = path.encode()
    sock.sendall(struct.pack(">HH12x", 0x1224, len(name)) + name)
    size, _mtime = struct.unpack(">qQ", recv_exact(sock, 16))
    if size <= 0:
        raise SystemExit(f"could not open {path} on the server (size {size})")
    return size


def run_pass(sock, size, label, pick_offset):
    import time

    latencies = []
    read = 0
    start = time.perf_counter()
    while time.perf_counter() - start < DURATION:
        offset = pick_offset(read)
        t0 = time.perf_counter()
        sock.sendall(struct.pack(">HHIQ", 0x1225, 0, REQ, offset))
        recv_exact(sock, REQ)
        latencies.append((time.perf_counter() - t0) * 1000)
        read += REQ
    elapsed = time.perf_counter() - start

    latencies.sort()
    rate = read / elapsed / 1e6
    verdict = "OK" if rate >= BD_MB_S else "TOO SLOW"
    print(f"  {label:<22} {rate:7.1f} MB/s   "
          f"median {statistics.median(latencies):7.2f} ms   "
          f"p99 {latencies[int(len(latencies) * 0.99)]:7.2f} ms   [{verdict}]")
    return rate


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    sock = socket.create_connection((host, port), timeout=15)
    sock.settimeout(60)
    size = open_image(sock, path)
    print(f"\n{path}: {size / 1e9:.1f} GB on {host}:{port}")
    print(f"{REQ // 1024} KiB requests, {DURATION:.0f}s per pass, "
          f"a PS3 Blu-ray needs ~{BD_MB_S:.0f} MB/s\n")

    span = size - REQ
    # Sequential from a fixed point: rides the OS readahead, so this is the
    # server's best case and mostly reflects the link.
    seq_base = span // 4
    seq = run_pass(sock, size, "sequential", lambda read: seq_base + read % (span // 2))

    # Random across the whole image: defeats readahead and forces a real seek
    # per request, which is what game data access looks like.
    rng = random.Random(1)
    rnd = run_pass(sock, size, "random (the real test)",
                   lambda read: rng.randrange(0, span) & ~2047)
    sock.close()

    print()
    if rnd >= BD_MB_S:
        print("  Storage keeps up. Look at the console's link or webMAN next.")
    elif seq >= BD_MB_S:
        print("  Sequential is fine but random is not: this is seek latency.")
        print("  The ISO is on a spinning disk and the server reads serially,")
        print("  so every request pays a full seek. Moving this one image to an")
        print("  SSD is the fix; caching the whole thing in RAM also works if")
        print("  the host has room to spare.")
    else:
        print("  Even sequential reads are below what the console needs -- the")
        print("  share is the bottleneck, not the access pattern.")


if __name__ == "__main__":
    main()
