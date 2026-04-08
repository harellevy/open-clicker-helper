"""Entry point: read newline-delimited JSON-RPC from stdin, dispatch, write
responses to stdout. All logs go to stderr so they never collide with the
JSON stream.
"""

from __future__ import annotations

import logging
import sys

from .rpc import RpcServer
from .handlers import build_dispatcher


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s sidecar %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("och_sidecar")
    log.info("starting och-sidecar")

    dispatcher = build_dispatcher()
    server = RpcServer(stdin=sys.stdin.buffer, stdout=sys.stdout.buffer, dispatcher=dispatcher)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
    except Exception:  # noqa: BLE001
        log.exception("fatal error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
