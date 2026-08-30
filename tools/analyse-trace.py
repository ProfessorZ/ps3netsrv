#!/usr/bin/env python3
"""Analyse a TRACE_READS capture and say which server-side fix would pay off.

Readahead (read more than asked) and retention (keep what was read) are
different fixes that different access patterns call for, so this reports the
evidence for each separately, then simulates a combined cache to predict the
reduction in disk operations.

Usage:  analyse-trace.py trace.log
"""

import collections
import re
import sys

MB = 1 << 20
BD_MB_S = 9.0

reads = []
pattern = re.compile(r"^TRACE (\d+) (\d+)$")
with open(sys.argv[1], errors="replace") as handle:
    for line in handle:
        m = pattern.match(line.strip())
        if m:
            reads.append((int(m.group(1)), int(m.group(2))))

if not reads:
    raise SystemExit("no TRACE lines found -- was the server built with -DTRACE_READS?")

print(f"\n{len(reads)} reads, {sum(n for _, n in reads) / MB:.1f} MB requested total\n")

# --- request size: the number that sets everything else ---
sizes = collections.Counter(n for _, n in reads)
print("Request size (this drives how badly a seek hurts):")
for size, count in sizes.most_common(5):
    share = 100.0 * count / len(reads)
    print(f"  {size:>9} B ({size / 1024:>7.1f} KiB)  {count:>7} reads  {share:5.1f}%")
common = sizes.most_common(1)[0][0]
for seek_ms in (7.0, 14.0):
    print(f"  -> at {seek_ms:.0f} ms/seek, {common / 1024:.0f} KiB per seek = "
          f"{common / (seek_ms / 1000) / 1e6:6.1f} MB/s "
          f"({'OK' if common / (seek_ms / 1000) / 1e6 >= BD_MB_S else 'below the ~9 MB/s a Blu-ray needs'})")

# --- gaps: does readahead have anything to latch onto? ---
gaps = collections.Counter()
for (prev_off, prev_len), (off, _) in zip(reads, reads[1:]):
    delta = off - (prev_off + prev_len)
    if delta == 0:
        gaps["contiguous"] += 1
    elif 0 < delta <= 1 * MB:
        gaps["forward < 1 MB"] += 1
    elif delta > 1 * MB:
        gaps["forward jump"] += 1
    elif -16 * MB < delta < 0:
        gaps["backward (near)"] += 1
    else:
        gaps["backward jump"] += 1

transitions = len(reads) - 1
print("\nSeek gap between consecutive reads:")
for label, count in gaps.most_common():
    print(f"  {label:<18} {count:>7}  {100.0 * count / transitions:5.1f}%")

local = gaps["contiguous"] + gaps["forward < 1 MB"]
print(f"  -> {100.0 * local / transitions:.1f}% of reads land near the previous one")

# --- streams: are these several sequential readers interleaved? ---
#
# Gap analysis alone cannot tell "scattered" from "two sequential streams
# alternating", and the two call for opposite conclusions: the second is still
# sequential access and wants per-stream readahead, not a verdict of
# "too random for readahead". Attribute each read to a stream it continues.
TOLERANCE = 4 * MB
streams = collections.OrderedDict()   # id -> next expected offset
continued, started, next_id = 0, 0, 0
for off, n in reads:
    match = None
    for sid, expected in streams.items():
        if abs(off - expected) <= TOLERANCE:
            match = sid
            break
    if match is None:
        match, next_id = next_id, next_id + 1
        started += 1
    else:
        continued += 1
    streams[match] = off + n
    streams.move_to_end(match)
    while len(streams) > 16:          # only recent streams stay candidates
        streams.popitem(last=False)

share = 100.0 * continued / len(reads)
print(f"\nStream structure: {started} stream starts, "
      f"{continued} reads continued one ({share:.1f}%)")
if share > 80.0 and started > 1:
    print(f"  -> not random at all: ~{started} sequential streams interleaved.")
    print("     Every read shares one descriptor, so they share one kernel")
    print("     readahead state and defeat it. Per-stream readahead is the fix.")
elif share > 80.0:
    print("  -> essentially one sequential stream; readahead already applies.")
elif local / transitions > 0.5:
    print("  -> mostly local access; a larger readahead window should pay off.")
else:
    print("  -> genuinely scattered; readahead alone will not help.")

# --- revisits: does retention have anything to latch onto? ---
BLOCK = 1 * MB
touched, revisited = set(), 0
for off, n in reads:
    for block in range(off // BLOCK, (off + n) // BLOCK + 1):
        if block in touched:
            revisited += 1
        else:
            touched.add(block)
print(f"\nRevisited 1 MB blocks: {revisited} of {revisited + len(touched)} touches "
      f"({100.0 * revisited / max(1, revisited + len(touched)):.1f}%) "
      f"({'retention pays off' if revisited > len(touched) * 0.2 else 'little reuse'})")

# --- simulate: how many disk operations would a cache actually remove? ---
print("\nSimulated cache (LRU over aligned windows) -- disk reads still needed:")
print(f"  {'window':>8}  {'32 MB':>10}  {'128 MB':>10}  {'512 MB':>10}")
for window in (256 * 1024, 1 * MB, 4 * MB):
    row = f"  {window // 1024:>6}KiB"
    for budget in (32 * MB, 128 * MB, 512 * MB):
        cache, misses = collections.OrderedDict(), 0
        limit = max(1, budget // window)
        for off, n in reads:
            for block in range(off // window, (off + n - 1) // window + 1):
                if block in cache:
                    cache.move_to_end(block)
                    continue
                misses += 1
                cache[block] = True
                if len(cache) > limit:
                    cache.popitem(last=False)
        row += f"  {misses:>6} ({100.0 * misses / len(reads):>3.0f}%)"
    print(row)
print("\n  Percentages are disk operations as a share of the console's requests.")
print("  Lower is better; 100% means the cache never helped.\n")
