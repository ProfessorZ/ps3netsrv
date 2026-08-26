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
        """A fresh connection plus a harmless OPEN_FILE "/CLOSEFILE" round-trip
        proves the process is still up and its accept loop isn't wedged."""
        assert self.proc.poll() is None, "server process exited unexpectedly"

        sock = connect(self.port)
        try:
            sock.sendall(open_file_cmd(b"/CLOSEFILE"))
            result = recv_exact(sock, 16)  # netiso_open_result
            assert len(result) == 16, "server did not answer a fresh connection"
        finally:
            sock.close()


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

    deadline = time.monotonic() + CONNECT_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", unused_tcp_port), timeout=0.2).close()
            break
        except OSError as exc:
            last_error = exc
            assert proc.poll() is None, f"server exited during startup: {proc.stdout.read().decode(errors='replace')}"
            time.sleep(0.1)
    else:
        raise TimeoutError(f"server never opened its port: {last_error}")

    handle = Server(proc, unused_tcp_port, str(root))
    yield handle

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
