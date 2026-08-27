"""Regression tests for the clients[] slot lifecycle.

The accept loop and each client thread both mutate the shared clients[] array.
Before the locking added alongside these tests there was no synchronization at
all, and finalize_client() ended with memset(client, 0, sizeof(client_t)) --
run on the client's own thread, potentially after the accept loop had already
handed that slot to a new connection. That wiped the new client's freshly
malloc'd buffer and socket, so the incoming connection was reset (and its 4MB
buffer leaked).

Every connection here comes from 127.0.0.1, so each new connection matches an
existing slot by IP and drives the reconnection path: the accept loop reclaims
the slot, closes the old socket and joins the old thread. Reconnecting in a
tight loop is what makes the race probable -- on the unfixed build these tests
fail within a few dozen iterations, typically with ConnectionResetError.
"""

import socket
import struct

import pytest

from netiso_client import (
    connect,
    open_file_cmd,
    read_file_cmd,
    recv_exact,
)


def _round_trip(port):
    """One full connect / request / reply / close cycle."""
    sock = connect(port)
    try:
        sock.sendall(open_file_cmd(b"/CLOSEFILE"))
        result = recv_exact(sock, 16)  # netiso_open_result
        assert len(result) == 16, f"short reply: got {len(result)} bytes"
    finally:
        sock.close()


def test_rapid_same_ip_reconnects(server):
    # Arrange / Act: hammer the same-IP reconnection path. Each iteration
    # leaves a slot whose thread is still winding down as the next connection
    # claims it.
    for n in range(600):
        try:
            _round_trip(server.port)
        except (OSError, AssertionError) as exc:
            pytest.fail(f"reconnection {n} failed: {type(exc).__name__}: {exc}")

    server.assert_alive()


def test_reconnect_while_previous_connection_has_state(server):
    # A slot carrying real state (an open file and its 4MB buffer) is the
    # interesting case: the dying thread frees that state while the accept
    # loop is reassigning the slot.
    disc = server.root + "/disc.bin"
    with open(disc, "wb") as f:
        f.truncate(4 * 1024 * 1024)

    for n in range(200):
        sock = connect(server.port)
        try:
            sock.sendall(open_file_cmd(b"/disc.bin"))
            assert len(recv_exact(sock, 16)) == 16, f"iteration {n}: open failed"

            sock.sendall(read_file_cmd(num_bytes=2048, offset=0))
            (bytes_read,) = struct.unpack(">i", recv_exact(sock, 4))
            assert bytes_read == 2048, f"iteration {n}: read returned {bytes_read}"
            assert len(recv_exact(sock, bytes_read)) == bytes_read
        finally:
            # Abortive close (RST rather than FIN) so the server tears the slot
            # down from its recv side while the next connection is arriving.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            struct.pack("ii", 1, 0))
            sock.close()

    server.assert_alive()


def test_slots_are_reusable_after_many_connections(server):
    # MAX_CLIENTS is 5 and every connection here shares one IP. If a slot were
    # ever left marked connected with no live thread -- the leak the reserve /
    # join / release sequence prevents -- the server would eventually answer
    # "Too many connections!" and stop serving.
    for _ in range(400):
        _round_trip(server.port)

    # Still serving after well over MAX_CLIENTS worth of churn.
    _round_trip(server.port)
    server.assert_alive()
