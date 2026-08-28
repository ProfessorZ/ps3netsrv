"""End-of-image reads must not drop the connection.

READ_FILE_CRITICAL and READ_CD_2048_CRITICAL both stream a fixed number of
bytes with no length field: the client consumes exactly what it asked for and
then sends its next command. The server used to treat any short read as fatal
and return FAILED, which tears the connection down mid-stream.

That is reachable in normal play. The console reads in fixed-size chunks, so
an image whose length is not a whole multiple of that chunk makes the last
request of a sequential run overhang the end of the file. The console then has
to reconnect and reopen, which is felt as a stall during playback.

The only reply that keeps both ends in sync is num_bytes with the overhang
zero-filled, which is what these tests pin down.
"""

import os
import struct

from netiso_client import (
    connect,
    open_file_cmd,
    read_cd_2048_critical_cmd,
    recv_exact,
)

SECTOR = 2048
CD_SECTOR_SIZE = 2352  # default when no PLAYSTATION/CD001 magic is sniffed


def read_file_critical_cmd(num_bytes: int, offset: int) -> bytes:
    # netiso_read_file_critical_cmd: uint16 opcode, uint16 pad, uint32 num_bytes,
    # uint64 offset (include/netiso.h)
    return struct.pack(">HHIQ", 0x1225, 0, num_bytes, offset)


def _make_iso(path, num_sectors):
    """A sector-aligned image, like every real PS3 ISO, with predictable bytes."""
    data = bytes((i & 0xFF) for i in range(256)) * (num_sectors * SECTOR // 256)
    with open(path, "wb") as f:
        f.write(data)
    return data


def _still_serving(sock):
    """Prove the connection survived by completing another request on it."""
    sock.sendall(read_file_critical_cmd(SECTOR, 0))
    return len(recv_exact(sock, SECTOR)) == SECTOR


def test_read_overhanging_end_of_file_is_zero_filled(server):
    # Arrange: request 32 sectors starting 16 sectors before the end, so the
    # read starts inside the image and runs 16 sectors past it.
    num_sectors = 4096
    data = _make_iso(os.path.join(server.root, "game.iso"), num_sectors)
    req = 32 * SECTOR
    offset = (num_sectors - 16) * SECTOR

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/game.iso"))
        assert len(recv_exact(sock, 16)) == 16

        # Act
        sock.sendall(read_file_critical_cmd(req, offset))
        got = recv_exact(sock, req)

        # Assert: full length, real bytes up to EOF, zeros beyond it, and the
        # connection is still usable rather than torn down.
        assert len(got) == req, "server short-replied or closed the connection"
        assert got[: 16 * SECTOR] == data[offset:]
        assert got[16 * SECTOR:] == bytes(16 * SECTOR)
        assert _still_serving(sock)
    finally:
        sock.close()

    server.assert_alive()


def test_read_entirely_past_end_of_file_is_zero_filled(server):
    # A request that begins at or after EOF reads nothing at all -- the whole
    # reply is padding, and the connection must still survive.
    num_sectors = 512
    _make_iso(os.path.join(server.root, "game.iso"), num_sectors)
    req = 8 * SECTOR

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/game.iso"))
        assert len(recv_exact(sock, 16)) == 16

        sock.sendall(read_file_critical_cmd(req, num_sectors * SECTOR))
        got = recv_exact(sock, req)

        assert got == bytes(req)
        assert _still_serving(sock)
    finally:
        sock.close()

    server.assert_alive()


def test_read_fully_inside_file_is_unchanged(server):
    # Guard against the zero-fill leaking into ordinary reads.
    num_sectors = 512
    data = _make_iso(os.path.join(server.root, "game.iso"), num_sectors)
    req, offset = 8 * SECTOR, 100 * SECTOR

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/game.iso"))
        assert len(recv_exact(sock, 16)) == 16

        sock.sendall(read_file_critical_cmd(req, offset))
        got = recv_exact(sock, req)

        assert got == data[offset:offset + req]
    finally:
        sock.close()

    server.assert_alive()


def test_cd_read_overhanging_end_of_file_is_zero_filled(server):
    # The CD path has the same no-length-field contract, so the tail of a CD
    # image must be padded rather than fatal. Sector geometry per
    # process_read_cd_2048_critical_cmd: payload at sector*2352 + 24.
    num_sectors = 64
    size = num_sectors * CD_SECTOR_SIZE
    with open(os.path.join(server.root, "disc.bin"), "wb") as f:
        f.write(bytes((i & 0xFF) for i in range(256)) * (size // 256))

    # Start two sectors from the end and ask for four: the last two overhang.
    start_sector, sector_count = num_sectors - 2, 4

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/disc.bin"))
        assert len(recv_exact(sock, 16)) == 16

        sock.sendall(read_cd_2048_critical_cmd(start_sector, sector_count))
        got = recv_exact(sock, sector_count * SECTOR)

        assert len(got) == sector_count * SECTOR, "server closed the connection"
        assert got[-SECTOR:] == bytes(SECTOR), "past-EOF sector should be zeros"
    finally:
        sock.close()

    server.assert_alive()


def test_multipart_short_read_mid_file_is_still_fatal(server):
    """The zero-fill must never paper over missing data in the middle of a file.

    File::read() stitches a multipart read across exactly one part boundary
    (src/File.cpp), so a request spanning three parts comes back short with
    real data still ahead of it. That is a genuine failure, not the end of the
    image, and must stay fatal rather than be padded with zeros -- otherwise
    the console is silently handed zeros where game data should be.
    """
    # Arrange: three equal parts. part_size is taken from the size of .iso.0.
    part = 64 * 1024
    for i in range(3):
        with open(os.path.join(server.root, f"game.iso.{i}"), "wb") as f:
            f.write(bytes((j & 0xFF) for j in range(256)) * (part // 256))
    total = 3 * part

    # A request spanning all three parts short-reads at the second boundary.
    req = total - 1024
    assert req > 2 * part, "request must span more than two parts"

    sock = connect(server.port)
    try:
        sock.sendall(open_file_cmd(b"/game.iso.0"))
        header = recv_exact(sock, 16)
        assert len(header) == 16
        (file_size, _mtime) = struct.unpack(">qQ", header)
        assert file_size == total, "multipart fstat should sum every part"

        # Act
        sock.sendall(read_file_critical_cmd(req, 0))
        got = recv_exact(sock, req)

        # Assert: refused, not silently zero-padded to full length.
        assert len(got) < req, "mid-file short read was padded instead of refused"
    finally:
        sock.close()

    server.assert_alive()
