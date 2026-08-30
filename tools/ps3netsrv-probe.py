#!/usr/bin/env python3
"""Measure a live ps3netsrv the way a PS3 experiences it.

Speaks the netiso wire protocol and issues READ_FILE_CRITICAL requests against
a real image under four access patterns, because the interesting cases sit
between "perfectly sequential" and "uniformly random":

  1 stream    - the server's best case; rides the kernel readahead.
  2 streams   - two sequential reads interleaved, far apart in the image.
  4 streams   - four of them.
  random      - uniform offsets; the worst case, and not realistic.

The interleaved passes are the point. A game playing a video while streaming
other assets produces several individually-sequential reads at once, but
ps3netsrv opens the image once and issues every read on a single descriptor,
so they share one kernel readahead state. Linux decides sequentiality per
descriptor by checking whether each read continues where the last ended, so
alternating between distant regions looks non-sequential, readahead backs off,
and the disk thrashes -- even though every individual stream is sequential.

If the multi-stream numbers collapse toward the random one, that is the
mechanism, and it explains stutter during a video that "should" be sequential.

Usage:
  ps3netsrv-probe.py HOST PORT /PS3ISO/YourGame.iso

A PS3 Blu-ray drive tops out near 9 MB/s.
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


def run_pass(sock, label, offsets):
    import time

    latencies = []
    read = 0
    start = time.perf_counter()
    while time.perf_counter() - start < DURATION:
        offset = next(offsets)
        t0 = time.perf_counter()
        sock.sendall(struct.pack(">HHIQ", 0x1225, 0, REQ, offset))
        recv_exact(sock, REQ)
        latencies.append((time.perf_counter() - t0) * 1000)
        read += REQ
    elapsed = time.perf_counter() - start

    latencies.sort()
    rate = read / elapsed / 1e6
    verdict = "OK" if rate >= BD_MB_S else "TOO SLOW"
    print(f"  {label:<24} {rate:7.1f} MB/s   "
          f"median {statistics.median(latencies):7.2f} ms   "
          f"p99 {latencies[int(len(latencies) * 0.99)]:7.2f} ms   [{verdict}]")
    return rate


def interleaved(span, count):
    """Strictly alternate between `count` sequential streams.

    The streams start far apart so the drive's own cache and the I/O elevator
    cannot mask the effect, and they alternate every single request -- reading
    in bursts would re-trigger the kernel's sequential detection and hide the
    very thing being measured.
    """
    stride = span // count
    positions = [i * stride for i in range(count)]
    while True:
        for i in range(count):
            yield positions[i] & ~2047
            positions[i] += REQ
            if positions[i] >= (i + 1) * stride - REQ:
                positions[i] = i * stride


def sequential(span):
    base = span // 4
    position = base
    while True:
        yield position & ~2047
        position += REQ
        if position >= base + span // 2:
            position = base


def uniform_random(span):
    rng = random.Random(1)
    while True:
        yield rng.randrange(0, span) & ~2047


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
    one = run_pass(sock, "1 stream (best case)", sequential(span))
    two = run_pass(sock, "2 streams interleaved", interleaved(span, 2))
    four = run_pass(sock, "4 streams interleaved", interleaved(span, 4))
    rnd = run_pass(sock, "uniform random (worst)", uniform_random(span))
    sock.close()

    print()
    worst_stream = min(two, four)
    if worst_stream >= BD_MB_S and rnd >= BD_MB_S:
        print("  Storage keeps up under every pattern. Look elsewhere: the")
        print("  console's link, webMAN, or the game itself.")
    elif one >= BD_MB_S and worst_stream < BD_MB_S:
        print(f"  One stream reaches {one:.0f} MB/s but interleaving collapses it to")
        print(f"  {worst_stream:.1f} MB/s -- below what the console needs. Every read")
        print("  goes through one descriptor, so concurrent sequential streams")
        print("  share a single readahead state and defeat it. This is why a")
        print("  video that 'should' be sequential can still stutter.")
    elif one < BD_MB_S:
        print("  Even a single sequential stream is below what the console needs.")
        print("  The share itself is the bottleneck, not the access pattern.")
    else:
        print("  Interleaved streams hold up; only uniform random fails, and real")
        print("  workloads are not uniformly random. Storage is likely not your")
        print("  problem -- capture a TRACE_READS log to see the real pattern.")


if __name__ == "__main__":
    main()
