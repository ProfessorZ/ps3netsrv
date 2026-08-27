"""Correctness tests for the VIsoFile directory-scan optimization.

VIsoFile::build() was changed to link each directory to its parent and
direct children explicitly (O(1) lookups) instead of rediscovering those
relationships with O(n) string scans (getParent / isDirectChild). The change
must be behavior-preserving: the generated virtual ISO for a given input
folder tree must be identical to before.

Two properties are checked here:

1. **Structure** -- every directory and file in the source tree appears in
   the generated image, in both the ISO9660 (uppercased ASCII) and Joliet
   (UCS-2 big-endian) name encodings. This is what actually catches a broken
   parent/child/sibling chain: a dropped link silently omits that subtree's
   directory records. Note a determinism check alone would NOT catch it --
   a consistently-wrong tree is still perfectly deterministic.

2. **Determinism** -- two builds of the same tree produce the same bytes,
   catching garbage or reordering from a corrupted sibling walk.

The image embeds one wall-clock field per volume descriptor
(`volumeCreation`, set from `time(NULL)` in VIsoFile::write via
genIso9660TimePvd), so two builds legitimately differ there whenever they
straddle a second boundary. Those fields are masked out before comparing
rather than being left to make the test flaky -- everything else in the
image is derived from the source tree and is stable. The sibling fields
volumeModification/Expiration/Effective are constant '0' fill, so they need
no masking.

Full byte-for-byte equivalence against the pre-optimization build was
verified out-of-band during development: dumping the same frozen-mtime tree
from both binaries produced images differing in exactly 4 bytes, all inside
the volumeCreation fields masked below.
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

# Volume descriptors written by VIsoFile::write(): the ISO9660 PVD and the
# Joliet SVD. `volumeCreation` is at offset 0x32D in Iso9660PVD (include/iso9660.h)
# and is 17 bytes wide.
DESCRIPTOR_OFFSETS = (0x8000, 0x8800)
VOLUME_CREATION_OFFSET = 0x32D
VOLUME_CREATION_LEN = 17

# Directories and files created by _make_tree(), which must all be represented
# in the generated image.
EXPECTED_DIRS = ["alpha", "deep", "beta", "nested", "gamma"]
EXPECTED_FILES = ["afile.txt", "zfile.txt", "a1.bin", "d1.bin", "n1.bin", "g1.bin"]


def _make_tree(root):
    """A deterministic nested tree: a few directories, subdirectories, and
    small files. Names chosen to exercise sibling ordering (alphasort) and
    multiple depth levels."""
    base = os.path.join(root, "tree")
    layout = {
        "": ["afile.txt", "zfile.txt"],
        "alpha": ["a1.bin"],
        "alpha/deep": ["d1.bin"],
        "beta": [],
        "beta/nested": ["n1.bin"],
        "gamma": ["g1.bin"],
    }
    for subdir, files in layout.items():
        d = os.path.join(base, subdir) if subdir else base
        os.makedirs(d, exist_ok=True)
        for i, fn in enumerate(files):
            with open(os.path.join(d, fn), "wb") as f:
                f.write(bytes(((i + 1) & 0xFF,)) * (512 * (i + 1)))
    return base


def _mask_volume_timestamps(image: bytes) -> bytes:
    """Zero the wall-clock volumeCreation field of each volume descriptor so
    two builds of the same tree are byte-comparable."""
    out = bytearray(image)
    for base in DESCRIPTOR_OFFSETS:
        start = base + VOLUME_CREATION_OFFSET
        end = start + VOLUME_CREATION_LEN
        assert end <= len(out), "image too small to contain its volume descriptors"
        out[start:end] = b"\x00" * VOLUME_CREATION_LEN
    return bytes(out)


def _read_whole_viso(server, marker_path):
    """Open the marker path (triggering a fresh VIsoFile::build) and read the
    entire generated image back."""
    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(marker_path))
        open_result = recv_exact(sock, 16)
        (file_size, _mtime) = struct.unpack(">qQ", open_result)
        assert file_size > 0, "VISO generation failed (file_size <= 0)"

        buf = bytearray()
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
            buf += data
            remaining -= n
            offset += n
        return bytes(buf)
    finally:
        sock.close()


def test_viso_contains_every_directory_and_file(server):
    # Arrange
    _make_tree(server.root)

    # Act
    image = _read_whole_viso(server, b"/***DVD***/tree")

    # Assert: each name is present in both the ISO9660 (uppercased ASCII) and
    # the Joliet (UCS-2 BE) directory records. A broken firstChild/nextSibling
    # link would drop that directory's records entirely.
    for name in EXPECTED_DIRS:
        assert name.upper().encode() in image, f"ISO9660 record missing for dir {name!r}"
        assert name.encode("utf-16-be") in image, f"Joliet record missing for dir {name!r}"

    for name in EXPECTED_FILES:
        assert name.upper().encode() in image, f"ISO9660 record missing for file {name!r}"
        assert name.encode("utf-16-be") in image, f"Joliet record missing for file {name!r}"

    server.assert_alive()


def test_viso_build_is_deterministic(server):
    # Arrange
    _make_tree(server.root)

    # Act: two independent OPEN_FILEs, each triggering a fresh build.
    first = _read_whole_viso(server, b"/***DVD***/tree")
    second = _read_whole_viso(server, b"/***DVD***/tree")

    # Assert: identical once the wall-clock volumeCreation fields are masked.
    assert len(first) == len(second)
    assert hashlib.sha256(_mask_volume_timestamps(first)).hexdigest() == \
           hashlib.sha256(_mask_volume_timestamps(second)).hexdigest()

    # The masking must be narrowly scoped: everything outside those two
    # 17-byte fields is expected to be stable build-to-build, so guard against
    # the mask quietly hiding a real difference.
    differing = [i for i in range(len(first)) if first[i] != second[i]]
    for i in differing:
        assert any(
            base + VOLUME_CREATION_OFFSET <= i < base + VOLUME_CREATION_OFFSET + VOLUME_CREATION_LEN
            for base in DESCRIPTOR_OFFSETS
        ), f"unexpected non-timestamp difference between builds at offset 0x{i:x}"

    server.assert_alive()
