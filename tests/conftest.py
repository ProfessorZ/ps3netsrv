"""Shared pytest fixtures: build the server once, run a fresh instance per test.

Each `server` instance is bound to a scratch shared directory (tmp_path) and a
free TCP port, torn down after the test.
"""

import os
import socket
import subprocess
import time

import pytest

from netiso_client import CONNECT_TIMEOUT, connect, open_file_cmd, recv_exact

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_BIN = os.path.join(REPO_ROOT, "ps3netsrv")


class Server:
    def __init__(self, proc: subprocess.Popen, port: int, root: str):
        self.proc = proc
        self.port = port
        self.root = root

    def assert_alive(self):
        """Assert the server process survived -- the property these tests care
        about after feeding it malicious or edge-case input.

        This deliberately does NOT open a second connection to probe liveness.
        Doing so would exercise the accept loop's same-IP reconnection path
        (every test connects from 127.0.0.1), which has a pre-existing race
        unrelated to anything under test here: it force-closes and joins the
        previous slot while that client's thread is concurrently running
        finalize_client(), which memsets the whole client_t -- including .s and
        .thread. Losing that race resets the incoming connection. Tests that
        want to prove the server is still *serving* should reuse their existing
        connection for another round-trip instead (see the CLOSEFILE check in
        test_multipart_seek_offset_out_of_range_is_rejected).
        """
        assert self.proc.poll() is None, (
            "server process exited unexpectedly: "
            f"{self.proc.stdout.read().decode(errors='replace') if self.proc.stdout else ''}"
        )


@pytest.fixture(scope="session")
def built_server():
    subprocess.run(["make", "-f", "Makefile.linux"], cwd=REPO_ROOT, check=True, capture_output=True)
    assert os.path.exists(SERVER_BIN), "build did not produce ps3netsrv"
    yield SERVER_BIN


@pytest.fixture()
def unused_tcp_port():
    # Avoids depending on pytest-asyncio's fixture of the same name -- this
    # repo has no Python dependency file, so keep test deps to plain pytest.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def server(built_server, tmp_path, unused_tcp_port):
    root = tmp_path / "root"
    root.mkdir()

    proc = subprocess.Popen(
        [built_server, str(root), str(unused_tcp_port)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Readiness is confirmed with a real protocol round-trip rather than a bare
    # TCP connect: the port comes from a bind-and-release probe, so a stale
    # listener could in principle still hold it, and a plain connect() would
    # happily succeed against the wrong process. A correct OPEN_FILE reply
    # proves it is our server and that it is serving requests.
    deadline = time.monotonic() + CONNECT_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            probe = socket.create_connection(("127.0.0.1", unused_tcp_port), timeout=0.5)
            try:
                probe.settimeout(0.5)
                probe.sendall(open_file_cmd(b"/CLOSEFILE"))
                if len(recv_exact(probe, 16)) == 16:
                    break
                last_error = AssertionError("incomplete OPEN_FILE reply")
            finally:
                probe.close()
        except OSError as exc:
            last_error = exc
        assert proc.poll() is None, f"server exited during startup: {proc.stdout.read().decode(errors='replace')}"
        time.sleep(0.1)
    else:
        raise TimeoutError(f"server never became ready on port {unused_tcp_port}: {last_error}")

    handle = Server(proc, unused_tcp_port, str(root))
    yield handle

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
