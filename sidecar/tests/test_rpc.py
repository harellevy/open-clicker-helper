"""Round-trip tests for the JSON-RPC framing.

We feed the server a BytesIO stdin and read the BytesIO stdout. No subprocess
or stdin tricks needed.
"""

from __future__ import annotations

import io
import json

from och_sidecar.handlers import build_dispatcher
from och_sidecar.rpc import RpcServer


def _run(requests: list[dict]) -> list[dict]:
    stdin = io.BytesIO(b"".join((json.dumps(r) + "\n").encode() for r in requests))
    stdout = io.BytesIO()
    server = RpcServer(stdin=stdin, stdout=stdout, dispatcher=build_dispatcher())
    server.serve_forever()
    stdout.seek(0)
    return [json.loads(line) for line in stdout.read().splitlines() if line.strip()]


def test_ping_returns_version() -> None:
    [resp] = _run([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "version": "0.0.1"}}


def test_unknown_method_returns_method_not_found() -> None:
    [resp] = _run([{"jsonrpc": "2.0", "id": 2, "method": "no.such.method"}])
    assert resp["error"]["code"] == -32601
    assert "no.such.method" in resp["error"]["message"]


def test_parse_error_for_garbage_line() -> None:
    stdin = io.BytesIO(b"this is not json\n")
    stdout = io.BytesIO()
    RpcServer(stdin=stdin, stdout=stdout, dispatcher=build_dispatcher()).serve_forever()
    stdout.seek(0)
    [resp] = [json.loads(line) for line in stdout.read().splitlines() if line.strip()]
    assert resp["error"]["code"] == -32700


def test_missing_jsonrpc_version_is_invalid_request() -> None:
    [resp] = _run([{"id": 3, "method": "ping"}])
    assert resp["error"]["code"] == -32600


def test_providers_list_shape() -> None:
    [resp] = _run([{"jsonrpc": "2.0", "id": 4, "method": "providers.list"}])
    assert set(resp["result"].keys()) == {"stt", "tts", "llm", "vlm"}
