"""Minimal ps3netsrv wire-protocol client used by the test suite.

Protocol reference: include/netiso.h. Every command is a fixed 16-byte
header (big-endian fields, packed, no padding); some commands are followed
by a variable-length path sent as raw bytes. Results are fixed-size packed
structs, big-endian.
"""

import socket
import struct

# Opcodes, in enum declaration order starting at 0x1224 (include/netiso.h).
NETISO_CMD_OPEN_FILE = 0x1224
NETISO_CMD_READ_FILE_CRITICAL = 0x1225
NETISO_CMD_READ_CD_2048_CRITICAL = 0x1226
NETISO_CMD_READ_FILE = 0x1227
NETISO_CMD_OPEN_DIR = 0x122A
NETISO_CMD_READ_DIR = 0x1232

NONE = -1  # server's "no value" sentinel (see `static const int NONE = -1;`)

CONNECT_TIMEOUT = 5.0


def open_file_cmd(path: bytes) -> bytes:
    # netiso_open_cmd: uint16 opcode, uint16 fp_len, uint8 pad[12]
    return struct.pack(">HH12x", NETISO_CMD_OPEN_FILE, len(path)) + path


def read_file_cmd(num_bytes: int, offset: int) -> bytes:
    # netiso_read_file_cmd: uint16 opcode, uint16 pad, uint32 num_bytes, uint64 offset
    return struct.pack(">HHIQ", NETISO_CMD_READ_FILE, 0, num_bytes, offset)


def read_cd_2048_critical_cmd(start_sector: int, sector_count: int) -> bytes:
    # netiso_read_cd_2048_critical_cmd: uint16 opcode, uint16 pad, uint32 start_sector,
    # uint32 sector_count, uint32 pad2
    return struct.pack(">HHIII", NETISO_CMD_READ_CD_2048_CRITICAL, 0, start_sector, sector_count, 0)


def open_dir_cmd(path: bytes) -> bytes:
    # netiso_open_dir_cmd: uint16 opcode, uint16 dp_len, uint8 pad[12]
    return struct.pack(">HH12x", NETISO_CMD_OPEN_DIR, len(path)) + path


def read_dir_cmd() -> bytes:
    # netiso_read_dir_entry_cmd: uint16 opcode, uint8 pad[14]
    return struct.pack(">H14x", NETISO_CMD_READ_DIR)


def connect(port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT)
    sock.settimeout(CONNECT_TIMEOUT)
    return sock


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly `size` bytes, or return whatever arrived before EOF/close."""
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf
