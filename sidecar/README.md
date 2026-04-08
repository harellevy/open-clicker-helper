# och-sidecar

Python AI service for `open-clicker-helper`. Spawned by the Tauri shell over
stdio. All requests/responses are newline-delimited JSON-RPC 2.0 messages on
stdin/stdout. Logs go to stderr.

## Run standalone (for development)

```bash
uv run --project sidecar och-sidecar
# then on stdin:
{"jsonrpc":"2.0","id":1,"method":"ping"}
```

## Methods (P1)

- `ping` → `{ok: true, version: "0.0.1"}`
- `providers.list` → `{stt: [...], tts: [...], llm: [...], vlm: [...]}`
- `providers.test` → `{ok, error?}`
- `pipeline.run` → progress events + final result (P3/P4)

## Adding a provider

Subclass the appropriate ABC in `och_sidecar/providers/base.py` and register
it in `och_sidecar/providers/__init__.py`. Each provider declares its config
schema as a Pydantic model so the React settings UI can render the form
generically.
