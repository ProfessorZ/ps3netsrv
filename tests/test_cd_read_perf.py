"""Correctness tests for the batched CD-read fast path.

process_read_cd_2048_critical_cmd was changed to satisfy a multi-sector
request with a single bulk read (then in-memory extraction) instead of one
seek+read syscall per sector, for backends where that's safe
(AbstractFile::supportsBulkRead). These tests assert the bytes returned by
the new fast path -- and by the preserved per-sector fallback -- exactly
match a hand-computed extraction, so the optimization is behavior-preserving.

Sector geometry (see process_read_cd_2048_critical_cmd + process_open_cmd):
the server returns, per requested sector, the 2048-byte payload located at
`start_sector_of_that_sector * CD_SECTOR_SIZE + 24` within the file, i.e. it
skips a 24-byte header and the trailing bytes up to the next sector. With no
CD magic present, CD_SECTOR_SIZE stays at its 2352 default.
"""

import os
import struct

from netiso_client import (
    connect,
    open_file_cmd,
    read_cd_2048_critical_cmd,
    recv_exact,
)

CD_SECTOR_SIZE = 2352  # default when no PLAYSTATION/CD001 magic is sniffed
HEADER = 24
PAYLOAD = 2048


def _make_disc(path, num_sectors):
    """Write a file where byte at file-offset i == (i & 0xFF), so every
    extracted payload byte is trivially predictable from its file offset."""
    size = num_sectors * CD_SECTOR_SIZE
    data = bytes((i & 0xFF) for i in range(size))
    with open(path, "wb") as f:
        f.write(data)
    return data


def _expected_payloads(data, start_sector, sector_count):
    out = bytearray()
    for n in range(sector_count):
        sec = start_sector + n
        base = sec * CD_SECTOR_SIZE + HEADER
        out += data[base:base + PAYLOAD]
    return bytes(out)


def test_bulk_read_multi_sector_matches_expected(server):
    # Arrange: a plain file (bulk-read-capable backend) spanning enough
    # sectors that the request covers several -- exercising the new
    # single-read + in-memory-extract fast path.
    data = _make_disc(os.path.join(server.root, "disc.bin"), num_sectors=40)
    start_sector, sector_count = 3, 16

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/disc.bin"))
        assert len(recv_exact(sock, 16)) == 16

        # Act
        sock.sendall(read_cd_2048_critical_cmd(start_sector, sector_count))

        # Assert: this command streams sector_count*2048 raw bytes with no
        # length prefix; compare against hand-computed extraction.
        got = recv_exact(sock, sector_count * PAYLOAD)
        assert len(got) == sector_count * PAYLOAD
        assert got == _expected_payloads(data, start_sector, sector_count)
    finally:
        sock.close()

    server.assert_alive()


def test_bulk_read_single_sector_matches_expected(server):
    # A one-sector request is the fast path's minimal case (span == 24+2048).
    data = _make_disc(os.path.join(server.root, "disc.bin"), num_sectors=8)
    start_sector, sector_count = 5, 1

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/disc.bin"))
        assert len(recv_exact(sock, 16)) == 16

        sock.sendall(read_cd_2048_critical_cmd(start_sector, sector_count))
        got = recv_exact(sock, sector_count * PAYLOAD)
        assert got == _expected_payloads(data, start_sector, sector_count)
    finally:
        sock.close()

    server.assert_alive()


def test_multipart_fallback_still_correct(server):
    # A single-part "*.iso.0" opens as multipart (is_multipart == 1), for
    # which supportsBulkRead() returns false -- so this exercises the
    # preserved per-sector fallback loop rather than the bulk path. The bytes
    # returned must still match the same extraction.
    #
    # File::seek() for multipart computes index = offset / part_size; with a
    # single part, any sector whose payload offset stays within part_size is
    # valid (index 0). Keep the request inside the file so index == 0.
    data = _make_disc(os.path.join(server.root, "movie.iso.0"), num_sectors=12)
    start_sector, sector_count = 2, 4
    # Ensure the last payload we read stays inside the single part.
    assert (start_sector + sector_count) * CD_SECTOR_SIZE <= len(data)

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/movie.iso.0"))
        open_result = recv_exact(sock, 16)
        (file_size, _mtime) = struct.unpack(">qQ", open_result)
        assert file_size == len(data)

        sock.sendall(read_cd_2048_critical_cmd(start_sector, sector_count))
        got = recv_exact(sock, sector_count * PAYLOAD)
        assert got == _expected_payloads(data, start_sector, sector_count)
    finally:
        sock.close()

    server.assert_alive()
