"""Newline-delimited JSON-RPC 2.0 over a binary byte stream.

We deliberately stay schemaless about transport framing: one JSON object per
line. This is enough for stdio and avoids any HTTP/auth/port concerns. The
Tauri Rust client uses the same framing.

A single dispatcher dict maps `method` (str) to a callable taking a `params`
dict and returning a JSON-serialisable value or yielding (event_name, payload)
tuples for streaming progress.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Union

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Union[Any, Iterator[tuple[str, Any]]]]
Dispatcher = dict[str, Handler]


@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any = None


# JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcServer:
    """Read JSON-RPC requests from `stdin`, write responses to `stdout`."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, dispatcher: Dispatcher) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._dispatcher = dispatcher

    def serve_forever(self) -> None:
        for raw in iter(self._stdin.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                self._send_error(None, PARSE_ERROR, f"parse error: {e}")
                continue
            self._handle(request)

    # ---------------------------------------------------------------- internals

    def _handle(self, request: dict[str, Any]) -> None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            self._send_error(request.get("id") if isinstance(request, dict) else None,
                             INVALID_REQUEST, "missing jsonrpc=2.0")
            return

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str):
            self._send_error(req_id, INVALID_REQUEST, "method must be a string")
            return
        if not isinstance(params, dict):
            self._send_error(req_id, INVALID_PARAMS, "params must be an object")
            return

        handler = self._dispatcher.get(method)
        if handler is None:
            self._send_error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")
            return

        try:
            result = handler(params)
        except RpcError as e:
            self._send_error(req_id, e.code, e.message, e.data)
            return
        except Exception as e:  # noqa: BLE001
            log.exception("handler %s failed", method)
            self._send_error(req_id, INTERNAL_ERROR, str(e))
            return

        # Streaming handlers yield (event, payload) tuples and may finish with
        # ('result', value). We translate yields into JSON-RPC notifications
        # carrying `id` so the Rust client can correlate.
        #
        # Iteration is wrapped in try/except so that an exception raised
        # *inside* the generator (e.g. a ProviderError mid-pipeline) gets
        # reported as an RPC error instead of bubbling out of `_handle()`,
        # tearing down `serve_forever()`, and crashing the sidecar process.
        if hasattr(result, "__iter__") and not isinstance(result, (dict, list, str, bytes)):
            final: Any = None
            try:
                for event, payload in result:
                    if event == "result":
                        # Emit as notification first so the UI's
                        # handleProgress can transition to done/error,
                        # then send as the RPC response.
                        self._send_notification(method, {"id": req_id, "event": "result", "payload": payload})
                        final = payload
                        break
                    self._send_notification(method, {"id": req_id, "event": event, "payload": payload})
            except RpcError as e:
                self._send_error(req_id, e.code, e.message, e.data)
                return
            except Exception as e:  # noqa: BLE001
                log.exception("streaming handler %s failed", method)
                # Tell the UI explicitly so its progress bar can flip to
                # error, then send the JSON-RPC error response.
                self._send_notification(
                    method,
                    {"id": req_id, "event": "error", "payload": {"error": str(e)}},
                )
                self._send_error(req_id, INTERNAL_ERROR, str(e))
                return
            self._send_result(req_id, final)
        else:
            self._send_result(req_id, result)

    def _send_result(self, req_id: Any, result: Any) -> None:
        if req_id is None:
            return  # notification, no response expected
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send_error(self, req_id: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._write({"jsonrpc": "2.0", "id": req_id, "error": err})

    def _send_notification(self, method: str, params: Any) -> None:
        self._write({"jsonrpc": "2.0", "method": f"{method}.progress", "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self._stdout.write(line)
        self._stdout.flush()
