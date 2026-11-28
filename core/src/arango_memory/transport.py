"""The ArangoDB HTTP transport: a shorter timeout for connecting and sending, a log line for
any call that fails or is slow, and no reuse of a connection that has sat idle.

Something between here and a hosted database drops idle connections, sometimes without saying
so. A request sent on one is then reset, or goes nowhere until a timeout. So a pooled
connection idle for longer than `max_idle_seconds` is closed and opened again before use.

python-arango sends a request again after a connection error without saying so, which makes a
call that stalled and then succeeded look like one slow call. Each of those attempts passes
through here, so each failure is logged with what kind it was: `ConnectTimeout` (no
connection), `ReadTimeout` (connected, no answer) or `ConnectionError` (the connection broke).
(Underneath, urllib3 also retries failed connects and warns about those itself.) Host names,
bind values and documents are never logged.

One request is never sent again once it may have arrived: asking a cursor for its next batch.
The database moves on when it answers, so asking twice skips a batch without any error. That
call raises `CursorBatchLost` instead, unless the cursor was opened with `allow_retry`, which
lets the same batch be asked for again.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlsplit

from arango.http import DefaultHTTPClient
from arango.response import Response
from requests import ConnectionError as RequestsConnectionError
from requests import ConnectTimeout, Session
from requests.adapters import HTTPAdapter
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ReadTimeoutError

from .telemetry.logging import logger

_QUERY_CHARS = 120
# The next batch of a cursor with no batch id in the path: not repeatable.
_NEXT_BATCH = re.compile(r"/_api/cursor/[^/]+$")


class CursorBatchLost(ConnectionError):
    """The connection broke while fetching a cursor's next batch; run the query again.

    Built on the builtin `ConnectionError`, not requests', which the driver would resend."""


def _retiring_pools(max_idle: float) -> dict[str, type[HTTPConnectionPool]]:
    """urllib3 pool classes that reopen a connection which waited in the pool too long."""

    class _Retiring(HTTPConnectionPool):
        def _put_conn(self, conn: Any) -> None:
            if conn is not None:
                conn.returned_at = time.monotonic()
            super()._put_conn(conn)

        def _get_conn(self, timeout: float | None = None) -> Any:
            conn = super()._get_conn(timeout)
            returned = getattr(conn, "returned_at", None)
            if returned is not None and time.monotonic() - returned > max_idle:
                conn.close()  # it connects again when used
            return conn

    class _RetiringHTTPS(_Retiring, HTTPSConnectionPool):
        pass

    return {"http": _Retiring, "https": _RetiringHTTPS}


def _kind(exc: BaseException) -> str:
    """What failed. requests calls a read that timed out a `ConnectionError` when urllib3 was
    allowed to retry the method (a GET, say); report it as the `ReadTimeout` it is."""
    wrapped = exc.args[0] if isinstance(exc, RequestsConnectionError) and exc.args else None
    if isinstance(getattr(wrapped, "reason", None), ReadTimeoutError):
        return "ReadTimeout"
    return type(exc).__name__


def _dropped(exc: BaseException) -> bool:
    """A connection that broke or was refused, as opposed to one that timed out."""
    return (isinstance(exc, RequestsConnectionError) and not isinstance(exc, ConnectTimeout)
            and _kind(exc) != "ReadTimeout")


def _root_cause(exc: BaseException) -> str:
    """The type of the error at the bottom, e.g. `ConnectionResetError`. Only the type: the
    messages name the host."""
    while exc.__context__ is not None:
        exc = exc.__context__
    return type(exc).__name__


def _query_of(path: str, data: Any) -> str | None:
    """The start of an AQL query's text (never its bind values), to say which call it was."""
    if not path.endswith("/_api/cursor") or not isinstance(data, str):
        return None
    try:
        query = json.loads(data)["query"]
    except (ValueError, KeyError, TypeError):
        return None
    return " ".join(str(query).split())[:_QUERY_CHARS]


class ObservedHTTPClient(DefaultHTTPClient):
    def __init__(
        self,
        *,
        request_timeout: float,
        connect_timeout: float,
        slow_seconds: float,
        retry_attempts: int = 3,
        resend_dropped: bool = False,
        max_idle_seconds: float = 0,
    ) -> None:
        super().__init__(request_timeout=request_timeout, retry_attempts=retry_attempts)
        self.connect_timeout = min(connect_timeout, request_timeout)
        self.slow_seconds = slow_seconds  # 0 = don't report slow calls
        self.resend_dropped = resend_dropped  # send once more if the connection broke
        self.max_idle_seconds = max_idle_seconds  # 0 = reuse however long it has waited

    def create_session(self, host: str) -> Session:
        session = super().create_session(host)
        for adapter in session.adapters.values():
            # urllib3 resends after other errors (a TLS connection ending early) whatever the
            # method. Raise them instead, so every resend is logged and goes through here.
            assert isinstance(adapter, HTTPAdapter)
            adapter.max_retries = adapter.max_retries.new(other=0)
            if self.max_idle_seconds:
                adapter.poolmanager.pool_classes_by_scheme = _retiring_pools(
                    self.max_idle_seconds)
        return session

    def send_request(
        self,
        session: Session,
        method: str,
        url: str,
        headers: MutableMapping[str, str] | None = None,
        params: MutableMapping[str, str] | None = None,
        data: Any = None,
        auth: tuple[str, str] | None = None,
    ) -> Response:
        request = (session, method, url, headers, params, data, auth)
        try:
            return self._attempt(*request)
        except RequestsConnectionError as exc:
            if isinstance(exc, ConnectTimeout):
                raise  # nothing was sent
            if method.lower() in ("post", "put") and _NEXT_BATCH.search(urlsplit(url).path):
                raise CursorBatchLost("connection lost while fetching a cursor batch") from exc
            if not (self.resend_dropped and _dropped(exc)):
                raise
        return self._attempt(*request)

    def _attempt(
        self,
        session: Session,
        method: str,
        url: str,
        headers: MutableMapping[str, str] | None,
        params: MutableMapping[str, str] | None,
        data: Any,
        auth: tuple[str, str] | None,
    ) -> Response:
        started = time.perf_counter()

        def details(**more: Any) -> dict[str, Any]:
            path = urlsplit(url).path
            found: dict[str, Any] = {
                "method": method.upper(), "path": path,
                "seconds": round(time.perf_counter() - started, 2), **more,
            }
            if (query := _query_of(path, data)) is not None:
                found["query"] = query
            return found

        try:
            response = session.request(
                method=method, url=url, params=params, data=data, headers=headers, auth=auth,
                timeout=(self.connect_timeout, self.request_timeout),
            )
        except Exception as exc:
            logger.warning("database call failed",
                           extra=details(reason=_kind(exc), cause=_root_cause(exc)))
            raise
        if self.slow_seconds and time.perf_counter() - started >= self.slow_seconds:
            logger.warning("slow database call", extra=details(status=response.status_code))
        return Response(
            method=method, url=response.url, headers=response.headers,
            status_code=response.status_code, status_text=response.reason,
            raw_body=response.text,
        )
