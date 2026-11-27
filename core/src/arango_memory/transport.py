"""The ArangoDB HTTP transport: a shorter timeout for connecting and sending, and a log line
for any call that fails or is slow.

python-arango sends a request again after a connection error without saying so, which makes a
call that stalled and then succeeded look like one slow call. Each of those attempts passes
through here, so each failure is logged with what kind it was: `ConnectTimeout` (no
connection), `ReadTimeout` (connected, no answer) or `ConnectionError` (the connection broke).
(Underneath, urllib3 also retries failed connects and warns about those itself.) Host names,
bind values and documents are never logged.
"""

from __future__ import annotations

import json
import time
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlsplit

from arango.http import DefaultHTTPClient
from arango.response import Response
from requests import Session

from .telemetry.logging import logger

_QUERY_CHARS = 120


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
    ) -> None:
        super().__init__(request_timeout=request_timeout, retry_attempts=retry_attempts)
        self.connect_timeout = min(connect_timeout, request_timeout)
        self.slow_seconds = slow_seconds  # 0 = don't report slow calls

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
                           extra=details(reason=type(exc).__name__, cause=_root_cause(exc)))
            raise
        if self.slow_seconds and time.perf_counter() - started >= self.slow_seconds:
            logger.warning("slow database call", extra=details(status=response.status_code))
        return Response(
            method=method, url=response.url, headers=response.headers,
            status_code=response.status_code, status_text=response.reason,
            raw_body=response.text,
        )
