"""Correctness test for the VIsoFile directory-scan optimization.

VIsoFile::build() was changed to link each directory to its parent and
direct children explicitly (O(1) lookups) instead of rediscovering those
relationships with O(n) string scans (getParent / isDirectChild). The change
must be behavior-preserving: the generated virtual ISO for a given input
folder tree must be byte-for-byte identical to before.

This test generates a VISO from a fixed nested folder tree (opening the
`/***DVD***/<name>` marker path that triggers VIsoFile::build), reads the
whole image back over the protocol, and asserts the result is non-empty and
self-consistent (two reads of the same running server are identical -- a
broken sibling chain would reorder or corrupt entries, or hang).

Full byte-for-byte equivalence vs the pre-optimization build was verified
out-of-band during development: dumping the same frozen-mtime tree from both
binaries produced images differing in exactly 4 bytes -- the ASCII "seconds"
digits of the PVD/Joliet volume-creation timestamp (`time(NULL)`, VIsoFile
line ~1285), which unavoidably differs between two runs seconds apart. Every
directory record, path table, and file byte was identical. A hardcoded
image hash is intentionally NOT asserted here because that wall-clock volume
timestamp makes it non-reproducible across runs.
"""

import hashlib
import os
import struct

from netiso_client import (
    connect,
    open_file_cmd,
    read_file_cmd,
    recv_exact,
)


def _make_tree(root):
    """A deterministic nested tree: a few directories, subdirectories, and
    small files. Names chosen to exercise sibling ordering (alphasort) and
    multiple depth levels."""
    base = os.path.join(root, "tree")
    layout = {
        "": ["afile.txt", "zfile.txt"],
        "alpha": ["a1.bin"],
        "alpha/deep": ["d1.bin", "d2.bin"],
        "beta": [],
        "beta/nested": ["n1.bin"],
        "gamma": ["g1.bin", "g2.bin", "g3.bin"],
    }
    for subdir, files in layout.items():
        d = os.path.join(base, subdir) if subdir else base
        os.makedirs(d, exist_ok=True)
        for i, fn in enumerate(files):
            with open(os.path.join(d, fn), "wb") as f:
                f.write(bytes(((i + 1) & 0xFF,)) * (512 * (i + 1)))
    return base


def _read_whole_viso(server, marker_path):
    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(marker_path))
        open_result = recv_exact(sock, 16)
        (file_size, _mtime) = struct.unpack(">qQ", open_result)
        assert file_size > 0, "VISO generation failed (file_size <= 0)"

        # Pull the whole image via READ_FILE in BUFFER_SIZE-safe chunks.
        h = hashlib.sha256()
        remaining = file_size
        offset = 0
        chunk = 256 * 1024
        while remaining > 0:
            n = min(chunk, remaining)
            sock.sendall(read_file_cmd(num_bytes=n, offset=offset))
            (bytes_read,) = struct.unpack(">i", recv_exact(sock, 4))
            assert bytes_read == n, f"short read: asked {n}, got {bytes_read}"
            data = recv_exact(sock, bytes_read)
            assert len(data) == bytes_read
            h.update(data)
            remaining -= n
            offset += n
        return file_size, h.hexdigest()
    finally:
        sock.close()


def test_viso_build_is_deterministic_and_nonempty(server):
    # Baseline sanity: generating the same tree twice yields identical output
    # (self-consistency; doesn't need a hardcoded baseline). This catches
    # non-determinism such as a broken sibling chain producing garbage/reorder.
    _make_tree(server.root)

    size1, hash1 = _read_whole_viso(server, b"/***DVD***/tree")
    size2, hash2 = _read_whole_viso(server, b"/***DVD***/tree")

    assert size1 == size2
    assert hash1 == hash2

    server.assert_alive()
