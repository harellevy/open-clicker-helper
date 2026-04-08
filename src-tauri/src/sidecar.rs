//! Python sidecar lifecycle + JSON-RPC client.
//!
//! Spawn `uv run --project <project_dir> och-sidecar` as a child process,
//! pipe stdin/stdout, and expose a typed `call` method that sends one
//! request and awaits the matching response. Notifications (`*.progress`)
//! are forwarded to a broadcast channel that the Rust shell can subscribe
//! to and re-emit as Tauri events.
//!
//! Why stdio JSON-RPC instead of HTTP:
//! - sidecar dies automatically when Tauri drops the child handle
//! - no port collisions, no firewall prompts, no auth surface
//! - one fewer thing to crash on first launch

use std::collections::HashMap;
use std::path::Path;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{broadcast, oneshot, Mutex};

use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
    #[serde(default)]
    pub data: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub id: u64,
    pub event: String,
    pub payload: Value,
}

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, RpcError>>>>>;

pub struct Sidecar {
    next_id: AtomicU64,
    pending: Pending,
    stdin: Mutex<ChildStdin>,
    progress_tx: broadcast::Sender<Progress>,
    // We hold the child so dropping the Sidecar kills the process.
    _child: Child,
}

impl Sidecar {
    /// Spawn `uv run --project <sidecar_dir> och-sidecar`.
    /// `sidecar_dir` should be the bundled (or dev) path to the
    /// `sidecar/` directory containing `pyproject.toml`.
    pub async fn spawn(uv_binary: &Path, sidecar_dir: &Path) -> AppResult<Self> {
        let mut child = Command::new(uv_binary)
            .args([
                "run",
                "--project",
                sidecar_dir.to_string_lossy().as_ref(),
                "och-sidecar",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| AppError::Sidecar(format!("spawn failed: {e}")))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| AppError::Sidecar("no stdin handle".into()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| AppError::Sidecar("no stdout handle".into()))?;

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, _) = broadcast::channel::<Progress>(64);

        // Spawn the read loop. It owns the stdout reader for the lifetime of
        // the sidecar and routes responses back to whichever caller is awaiting.
        {
            let pending = pending.clone();
            let progress_tx = progress_tx.clone();
            tokio::spawn(async move {
                let mut reader = BufReader::new(stdout);
                let mut line = String::new();
                loop {
                    line.clear();
                    match reader.read_line(&mut line).await {
                        Ok(0) => {
                            tracing::warn!("sidecar stdout closed");
                            break;
                        }
                        Ok(_) => {
                            if let Err(e) = Self::route_message(&pending, &progress_tx, &line).await
                            {
                                tracing::warn!("router error: {e}");
                            }
                        }
                        Err(e) => {
                            tracing::error!("sidecar read error: {e}");
                            break;
                        }
                    }
                }
            });
        }

        Ok(Self {
            next_id: AtomicU64::new(1),
            pending,
            stdin: Mutex::new(stdin),
            progress_tx,
            _child: child,
        })
    }

    /// Subscribe to streaming progress notifications. Each subscription is
    /// independent (fan-out via tokio broadcast).
    pub fn progress(&self) -> broadcast::Receiver<Progress> {
        self.progress_tx.subscribe()
    }

    /// Send a request and await the response.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, AppError> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);

        let request = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        let mut serialised = serde_json::to_vec(&request).map_err(AppError::from)?;
        serialised.push(b'\n');

        {
            let mut stdin = self.stdin.lock().await;
            stdin
                .write_all(&serialised)
                .await
                .map_err(|e| AppError::Sidecar(format!("write: {e}")))?;
            stdin
                .flush()
                .await
                .map_err(|e| AppError::Sidecar(format!("flush: {e}")))?;
        }

        match rx.await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(rpc_err)) => Err(AppError::Sidecar(format!(
                "rpc error {}: {}",
                rpc_err.code, rpc_err.message
            ))),
            Err(_canceled) => Err(AppError::Sidecar("response channel closed".into())),
        }
    }

    async fn route_message(
        pending: &Pending,
        progress_tx: &broadcast::Sender<Progress>,
        raw: &str,
    ) -> Result<(), AppError> {
        let v: Value = serde_json::from_str(raw.trim())?;
        let obj = v
            .as_object()
            .ok_or_else(|| AppError::Sidecar("non-object message".into()))?;

        // Notification path: {"jsonrpc":"2.0","method":"<m>.progress","params":{...}}
        if obj.contains_key("method") {
            let params = obj.get("params").cloned().unwrap_or(Value::Null);
            let id = params.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let event = params
                .get("event")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let payload = params.get("payload").cloned().unwrap_or(Value::Null);
            let _ = progress_tx.send(Progress { id, event, payload });
            return Ok(());
        }

        // Response path: {"jsonrpc":"2.0","id":N,"result"|"error":...}
        let id = obj
            .get("id")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| AppError::Sidecar("response missing id".into()))?;
        let mut pending = pending.lock().await;
        let Some(sender) = pending.remove(&id) else {
            tracing::warn!("dropped response for unknown id {id}");
            return Ok(());
        };
        if let Some(result) = obj.get("result") {
            let _ = sender.send(Ok(result.clone()));
        } else if let Some(err) = obj.get("error") {
            let rpc_err: RpcError =
                serde_json::from_value(err.clone()).unwrap_or_else(|e| RpcError {
                    code: -32603,
                    message: format!("malformed error: {e}"),
                    data: None,
                });
            let _ = sender.send(Err(rpc_err));
        } else {
            let _ = sender.send(Err(RpcError {
                code: -32603,
                message: "response missing result/error".into(),
                data: None,
            }));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn route_response_resolves_pending_sender() {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, _rx) = broadcast::channel::<Progress>(8);
        let (tx, rx) = oneshot::channel();
        pending.lock().await.insert(42, tx);

        let msg = r#"{"jsonrpc":"2.0","id":42,"result":{"ok":true}}"#;
        Sidecar::route_message(&pending, &progress_tx, msg)
            .await
            .unwrap();

        let result = rx.await.unwrap().unwrap();
        assert_eq!(result["ok"], true);
        assert!(pending.lock().await.is_empty());
    }

    #[tokio::test]
    async fn route_rpc_error_sends_err_to_sender() {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, _rx) = broadcast::channel::<Progress>(8);
        let (tx, rx) = oneshot::channel();
        pending.lock().await.insert(7, tx);

        let msg =
            r#"{"jsonrpc":"2.0","id":7,"error":{"code":-32601,"message":"Method not found"}}"#;
        Sidecar::route_message(&pending, &progress_tx, msg)
            .await
            .unwrap();

        let err = rx.await.unwrap().unwrap_err();
        assert_eq!(err.code, -32601);
        assert_eq!(err.message, "Method not found");
    }

    #[tokio::test]
    async fn route_notification_broadcasts_to_progress() {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, mut rx) = broadcast::channel::<Progress>(8);

        let msg = r#"{"jsonrpc":"2.0","method":"pipeline.progress","params":{"id":1,"event":"stt_done","payload":{"transcript":"hello"}}}"#;
        Sidecar::route_message(&pending, &progress_tx, msg)
            .await
            .unwrap();

        let progress = rx.recv().await.unwrap();
        assert_eq!(progress.id, 1);
        assert_eq!(progress.event, "stt_done");
        assert_eq!(progress.payload["transcript"], "hello");
    }

    #[tokio::test]
    async fn route_unknown_id_response_is_silently_dropped() {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, _rx) = broadcast::channel::<Progress>(8);

        // id 99 has no registered sender — should not panic or error
        let msg = r#"{"jsonrpc":"2.0","id":99,"result":{"ok":true}}"#;
        Sidecar::route_message(&pending, &progress_tx, msg)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn route_malformed_json_returns_error() {
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (progress_tx, _rx) = broadcast::channel::<Progress>(8);

        let result = Sidecar::route_message(&pending, &progress_tx, "not json at all").await;
        assert!(result.is_err());
    }
}
