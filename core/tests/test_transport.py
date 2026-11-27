"""A database call that fails or is slow says so, and which kind of failure it was."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests
from arango.database import StandardDatabase

from arango_memory.client import ArangoMemoryClient
from arango_memory.config import Settings
from arango_memory.telemetry.logging import logger
from arango_memory.transport import ObservedHTTPClient, _query_of


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[Any] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def named(self, message: str) -> list[Any]:
        return [r for r in self.records if r.getMessage() == message]


@pytest.fixture
def logs() -> Iterator[_Records]:
    handler = _Records()
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


@pytest.fixture
def server() -> Iterator[Callable[..., str]]:
    """Starts a server that treats each accepted connection with `handle`."""
    sockets: list[socket.socket] = []

    def start(handle: Callable[[socket.socket], None]) -> str:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(20)
        sockets.append(listener)

        def accept() -> None:
            while True:
                try:
                    conn = listener.accept()[0]
                except OSError:
                    return
                sockets.append(conn)
                threading.Thread(target=handle, args=(conn,), daemon=True).start()

        threading.Thread(target=accept, daemon=True).start()
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    yield start
    for sock in sockets:
        sock.close()


def _db(url: str, **more: Any) -> StandardDatabase:
    client = ArangoMemoryClient(Settings(arango_url=url, arango_db="x", **more))
    return client.database("x")


def test_no_answer_is_logged_as_a_read_timeout_with_the_query(
    server: Callable[..., str], logs: _Records
) -> None:
    url = server(lambda conn: None)
    with pytest.raises(Exception, match="timed out"):
        _db(url, arango_request_timeout=1).aql.execute(
            "FOR d IN\n   memories RETURN @secret", bind_vars={"secret": "do-not-log"})
    (failed,) = logs.named("database call failed")
    assert failed.reason == "ReadTimeout" and failed.seconds >= 1
    assert (failed.method, failed.path) == ("POST", "/_db/x/_api/cursor")
    assert failed.query == "FOR d IN memories RETURN @secret"
    assert "do-not-log" not in json.dumps({k: str(v) for k, v in vars(failed).items()})


def test_a_get_that_gets_no_answer_is_a_read_timeout_too(
    server: Callable[..., str], logs: _Records
) -> None:
    """requests reports this one as a ConnectionError."""
    url = server(lambda conn: None)
    http = ObservedHTTPClient(request_timeout=0.5, connect_timeout=1, slow_seconds=0,
                              retry_attempts=0)
    with pytest.raises(requests.ConnectionError):
        http.send_request(http.create_session(url), "get", url)
    (failed,) = logs.named("database call failed")
    assert failed.reason == "ReadTimeout"


def test_a_broken_connection_is_logged_even_though_the_call_is_sent_again(
    server: Callable[..., str], logs: _Records
) -> None:
    """python-arango retries these without a word."""
    seen: list[int] = []

    def drop_first(conn: socket.socket) -> None:
        conn.recv(65536)
        seen.append(1)
        if len(seen) == 1:
            conn.close()
            return
        body = b'{"result":[1],"hasMore":false,"error":false,"code":201}'
        conn.sendall(b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\n\r\n%s" % (len(body), body))

    assert list(_db(server(drop_first)).aql.execute("RETURN 1")) == [1]  # type: ignore[arg-type]
    (failed,) = logs.named("database call failed")
    assert failed.reason == "ConnectionError" and len(seen) == 2
    assert failed.cause in ("RemoteDisconnected", "ConnectionResetError")  # depends on timing
    assert "127.0.0.1" not in str(vars(failed))


def test_a_request_that_cannot_be_sent_gives_up_at_the_connect_timeout(
    server: Callable[..., str], logs: _Records
) -> None:
    """urllib3 applies the connect timeout until the request has gone out."""
    url = server(lambda conn: None)  # accepts, never reads
    http = ObservedHTTPClient(request_timeout=30, connect_timeout=1, slow_seconds=0)
    started = time.perf_counter()
    with pytest.raises(requests.ConnectionError):
        http.send_request(requests.Session(), "post", url, data=b"x" * 50_000_000)
    assert time.perf_counter() - started < 10
    (failed,) = logs.named("database call failed")
    assert (failed.reason, failed.cause) == ("ConnectionError", "TimeoutError")


def test_a_slow_call_is_logged(server: Callable[..., str], logs: _Records) -> None:
    def slow(conn: socket.socket) -> None:
        conn.recv(65536)
        time.sleep(0.3)
        body = b'{"result":[],"hasMore":false,"error":false,"code":201}'
        conn.sendall(b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\n\r\n%s" % (len(body), body))

    list(_db(server(slow), arango_slow_call_seconds=0.2).aql.execute("RETURN 2"))  # type: ignore[arg-type]
    (slow_call,) = logs.named("slow database call")
    assert slow_call.seconds >= 0.3 and slow_call.status == 201 and slow_call.query == "RETURN 2"
    assert not logs.named("database call failed")


def test_connecting_has_its_own_shorter_timeout() -> None:
    http = ObservedHTTPClient(request_timeout=60, connect_timeout=5, slow_seconds=5)
    seen: list[Any] = []

    class _Session:
        def request(self, **kwargs: Any) -> Any:
            seen.append(kwargs["timeout"])
            raise ConnectionError("stop")

    with pytest.raises(ConnectionError):
        http.send_request(_Session(), "get", "http://h/_api/version")  # type: ignore[arg-type]
    assert seen == [(5, 60)]
    assert ObservedHTTPClient(request_timeout=3, connect_timeout=5, slow_seconds=0
                              ).connect_timeout == 3


def test_real_calls_pass_through_quietly(db: StandardDatabase, logs: _Records) -> None:
    assert list(db.aql.execute("RETURN 1")) == [1]  # type: ignore[arg-type]
    assert not logs.records


def test_only_cursor_calls_carry_a_query() -> None:
    assert _query_of("/_db/x/_api/document/c", '{"query": "no"}') is None
    assert _query_of("/_db/x/_api/cursor", b"compressed") is None
    assert _query_of("/_db/x/_api/cursor", "not json") is None
    assert len(_query_of("/_db/x/_api/cursor", json.dumps({"query": "R " * 200})) or "") == 120
