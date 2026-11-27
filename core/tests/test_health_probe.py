"""A database that stops answering must not take /health and /ready with it."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import arango_memory.api.app as app_mod
import arango_memory.client as client_mod
from arango_memory.client import ArangoMemoryClient
from arango_memory.config import Settings


@pytest.fixture
def silent_server() -> Iterator[tuple[str, list[socket.socket]]]:
    """Accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(20)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}", held
    server.close()
    for conn in held:
        conn.close()


def test_a_probe_does_not_repeat_a_call_that_gets_no_answer(
    silent_server: tuple[str, list[socket.socket]], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, connections = silent_server
    monkeypatch.setattr(client_mod, "PROBE_TIMEOUT_SECONDS", 0.5)
    probe = ArangoMemoryClient(Settings(arango_url=url)).probe()
    started = time.monotonic()
    assert probe.ping() is False
    assert time.monotonic() - started < 2.0  # not a multiple of the timeout
    assert len(connections) == 1


def _serve(handle: Callable[[socket.socket, int], None]) -> tuple[str, socket.socket]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(20)

    def accept() -> None:
        number = 0
        while True:
            try:
                conn = server.accept()[0]
            except OSError:
                return
            number += 1
            threading.Thread(target=handle, args=(conn, number), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return f"http://127.0.0.1:{server.getsockname()[1]}", server


@pytest.mark.parametrize("dropped, reachable", [(1, True), (2, False)])
def test_a_probe_tries_a_dropped_connection_once_more(dropped: int, reachable: bool) -> None:
    connections: list[int] = []

    def handle(conn: socket.socket, number: int) -> None:
        connections.append(number)
        conn.recv(65536)
        if number <= dropped:
            conn.close()
            return
        body = b'{"server":"arango","version":"3.12.0"}'
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     b"Connection: close\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        conn.close()

    url, server = _serve(handle)
    try:
        assert ArangoMemoryClient(Settings(arango_url=url)).probe().ping() is reachable
        assert len(connections) == 2  # never a third
    finally:
        server.close()


def test_a_hung_database_call_does_not_hold_up_the_probes(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mod, "_PROBE_WAIT_SECONDS", 0.2)
    release = threading.Event()
    asked: list[int] = []
    probe = api.app.state.probe  # type: ignore[attr-defined]
    real = probe.client.ping

    def hung() -> bool:
        asked.append(1)
        release.wait(timeout=10)
        return bool(real())

    monkeypatch.setattr(probe.client, "ping", hung)
    try:
        started = time.monotonic()
        with ThreadPoolExecutor(4) as pool:
            health = [pool.submit(api.get, "/health") for _ in range(3)]
            ready = pool.submit(api.get, "/ready")
            bodies = [h.result() for h in health]
        assert time.monotonic() - started < 3.0
        assert [r.status_code for r in bodies] == [200, 200, 200]
        assert all(r.json()["arango"] is False and r.json()["vector"] == "unknown" for r in bodies)
        assert bodies[0].json()["write_worker"] == "running"  # the rest is still reported
        assert ready.result().status_code == 503
        assert api.get("/health").status_code == 200  # still not queued behind the stuck call
        assert asked == [1]  # and no second call piles on top of it
    finally:
        release.set()

    deadline = time.monotonic() + 5
    while api.get("/ready").status_code != 200 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert api.get("/health").json()["arango"] is True  # recovers by itself
