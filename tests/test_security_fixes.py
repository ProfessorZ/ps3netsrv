"""Black-box protocol tests for the ps3netsrv security fixes.

These tests build the server and drive it over its real TCP wire protocol,
sending the exact crafted packets described in the code review findings.
Each test asserts the server handles the malicious/edge-case input
gracefully -- an error response and/or a closed connection -- instead of
crashing, and that the server process is still alive afterward.

Shared harness (build/run fixtures, packet builders) lives in conftest.py
and netiso_client.py.

Not covered here: the VIsoFile::buildPathTable() heap-overflow fix, which
requires generating a virtual PS3 ISO from a directory tree with thousands
of subdirectories to reach the 4MB tempBuf limit -- verified separately by
code inspection and a clean build instead of a live protocol test.
"""

import os
import struct

from netiso_client import (
    NONE,
    connect,
    open_dir_cmd,
    open_file_cmd,
    read_cd_2048_critical_cmd,
    read_dir_cmd,
    read_file_cmd,
    recv_exact,
)


def test_cd_read_sector_count_overflow_is_rejected(server):
    # Arrange: a real file to open, large enough that the read loop could
    # actually walk past the 4MB client->buf if the size check didn't catch
    # it. A small file would make this test pass for the wrong reason: the
    # loop's own per-iteration EOF check ("read() != 2048") would bail out
    # after a couple of iterations on either the old or the fixed code,
    # masking whether the overflow guard itself ever fired. Sized well past
    # BUFFER_SIZE (4MB) so a regression would actually write out of bounds.
    disc_path = os.path.join(server.root, "disc.bin")
    with open(disc_path, "wb") as f:
        f.truncate(8 * 1024 * 1024)  # sparse file, instant

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/disc.bin"))
        open_result = recv_exact(sock, 16)
        assert len(open_result) == 16

        # Act: sector_count = 0x00200000 makes `sector_count * 2048` wrap to 0
        # in 32-bit arithmetic (2,097,152 * 2048 == 2**32), which used to slip
        # past the `> BUFFER_SIZE` guard and write ~4GB into a 4MB buffer.
        sock.sendall(read_cd_2048_critical_cmd(start_sector=0, sector_count=0x00200000))

        # Assert: the fixed guard now widens the multiplication and rejects
        # the request up front (no sector data, and no crash) instead of
        # attempting the read.
        reply = sock.recv(4096)
        assert reply == b"", "server should have closed the connection instead of streaming data"
    finally:
        sock.close()

    server.assert_alive()


def test_open_dir_with_long_path_survives_read_dir(server):
    # Arrange: a real subdirectory, reached via a client-supplied path long
    # enough to have overflowed the old fixed 510-byte MAX_PATH_LEN stack
    # buffer in process_read_dir(). "./" segments are inert to the
    # filesystem (opendir resolves them) but are not collapsed by the
    # server's own normalize_path(), so the full padded string is what
    # process_read_dir() has to handle.
    os.mkdir(os.path.join(server.root, "d"))
    long_path = b"/" + (b"./" * 300) + b"d"
    assert len(long_path) > 510

    sock = connect(server.port)
    try:
        sock.sendall(open_dir_cmd(long_path))
        open_dir_result = recv_exact(sock, 4)  # netiso_open_dir_result
        (open_result,) = struct.unpack(">i", open_dir_result)
        assert open_result == 0, "opendir on the padded path should still succeed"

        # Act: this is what used to run the vulnerable strcpy/sprintf calls.
        sock.sendall(read_dir_cmd())

        # Assert: got a well-formed response instead of a crash.
        read_dir_result = recv_exact(sock, 8)  # netiso_read_dir_result
        assert len(read_dir_result) == 8
        (dir_size,) = struct.unpack(">q", read_dir_result)
        assert dir_size == 0  # "d" is empty
    finally:
        sock.close()

    server.assert_alive()


def test_multipart_seek_offset_out_of_range_is_rejected(server):
    # Arrange: naming a file "*.iso.0" makes File::open() treat it as part 0
    # of a multi-part ISO (see File.cpp:94); since no "*.iso.1" exists,
    # is_multipart stays at 1 and part_size becomes this file's size.
    part_path = os.path.join(server.root, "game.iso.0")
    with open(part_path, "wb") as f:
        f.write(b"\x00" * 16)

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/game.iso.0"))
        open_result = recv_exact(sock, 16)
        (file_size, _mtime) = struct.unpack(">qQ", open_result)
        assert file_size == 16

        # Act: offset / part_size == 1000, far outside the single valid part
        # index (0). The old code truncated this into an int8_t and indexed
        # fp[] out of bounds; the fix rejects it in File::seek().
        sock.sendall(read_file_cmd(num_bytes=4, offset=16 * 1000))

        # Assert: a clean "read failed" result instead of touching fp[] OOB.
        # process_read_file_cmd() always keeps the connection open, even on
        # a rejected seek, so we can also confirm this same connection is
        # still usable.
        read_result = recv_exact(sock, 4)  # netiso_read_file_result
        (bytes_read,) = struct.unpack(">i", read_result)
        assert bytes_read == NONE

        sock.sendall(open_file_cmd(b"/CLOSEFILE"))
        close_result = recv_exact(sock, 16)
        assert len(close_result) == 16
    finally:
        sock.close()

    server.assert_alive()
