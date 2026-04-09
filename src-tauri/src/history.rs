//! Conversation history persistence.
//!
//! Stores the last `MAX_SESSIONS` push-to-talk cycles in a separate
//! `history.json` tauri-plugin-store file (kept apart from `settings.json`
//! because this is append-only log data, not user config). Each entry
//! captures just enough to show in a history list — the transcript, the
//! spoken answer, step count, grounding source, and timings — and is
//! written by `lib::process_recording` after a successful pipeline call.

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};

pub const HISTORY_STORE_FILE: &str = "history.json";
pub const HISTORY_KEY: &str = "sessions";

/// Maximum number of sessions retained. Older entries are dropped from the
/// front of the list when a new one is appended.
pub const MAX_SESSIONS: usize = 50;

/// One entry in the conversation-history log.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SessionRecord {
    /// Monotonically increasing id (unix-millis of write time).
    pub id: i64,
    /// Wall-clock time the pipeline finished (unix millis).
    pub timestamp_ms: i64,
    /// Transcribed user question.
    pub transcript: String,
    /// Spoken assistant answer.
    pub answer: String,
    /// Number of grounding steps produced (0 in text-only mode).
    pub steps_count: usize,
    /// `"ax"`, `"vlm"`, or `None` for text-only sessions.
    #[serde(default)]
    pub grounding_source: Option<String>,
    /// End-to-end pipeline duration in milliseconds, if the sidecar reported
    /// it.
    #[serde(default)]
    pub total_ms: Option<i64>,
}

/// Append a session record, truncating the list to [`MAX_SESSIONS`] from the
/// newest end.
pub fn append(app: &tauri::AppHandle, record: SessionRecord) -> AppResult<()> {
    use tauri_plugin_store::StoreExt;
    let store = app
        .store(HISTORY_STORE_FILE)
        .map_err(|e| AppError::Sidecar(format!("history store open: {e}")))?;

    let mut sessions: Vec<SessionRecord> = store
        .get(HISTORY_KEY)
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default();

    sessions.push(record);
    truncate_to_limit(&mut sessions, MAX_SESSIONS);

    store.set(
        HISTORY_KEY,
        serde_json::to_value(&sessions).map_err(AppError::from)?,
    );
    store
        .save()
        .map_err(|e| AppError::Sidecar(format!("history store save: {e}")))?;
    Ok(())
}

/// Load all stored sessions in chronological order (oldest first).
pub fn load(app: &tauri::AppHandle) -> AppResult<Vec<SessionRecord>> {
    use tauri_plugin_store::StoreExt;
    let store = app
        .store(HISTORY_STORE_FILE)
        .map_err(|e| AppError::Sidecar(format!("history store open: {e}")))?;
    Ok(store
        .get(HISTORY_KEY)
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default())
}

/// Remove every stored session.
pub fn clear(app: &tauri::AppHandle) -> AppResult<()> {
    use tauri_plugin_store::StoreExt;
    let store = app
        .store(HISTORY_STORE_FILE)
        .map_err(|e| AppError::Sidecar(format!("history store open: {e}")))?;
    store.delete(HISTORY_KEY);
    store
        .save()
        .map_err(|e| AppError::Sidecar(format!("history store save: {e}")))?;
    Ok(())
}

/// Build a [`SessionRecord`] from the pipeline result JSON returned by the
/// sidecar. Missing or malformed fields fall back to sensible defaults so a
/// partial result still produces a loggable entry.
pub fn record_from_pipeline_result(result: &serde_json::Value) -> SessionRecord {
    let now_ms = current_unix_ms();
    let transcript = result
        .get("transcript")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let answer = result
        .get("answer")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let steps_count = result
        .get("steps")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let grounding_source = result
        .get("grounding_source")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let total_ms = result
        .get("timings")
        .and_then(|v| v.get("total_ms"))
        .and_then(|v| v.as_i64());

    SessionRecord {
        id: now_ms,
        timestamp_ms: now_ms,
        transcript,
        answer,
        steps_count,
        grounding_source,
        total_ms,
    }
}

fn current_unix_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

fn truncate_to_limit(sessions: &mut Vec<SessionRecord>, limit: usize) {
    if sessions.len() > limit {
        let drop = sessions.len() - limit;
        sessions.drain(0..drop);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(id: i64) -> SessionRecord {
        SessionRecord {
            id,
            timestamp_ms: id,
            transcript: format!("q{id}"),
            answer: format!("a{id}"),
            steps_count: 1,
            grounding_source: Some("ax".into()),
            total_ms: Some(id),
        }
    }

    #[test]
    fn session_record_roundtrip_serde() {
        let r = fixture(42);
        let json = serde_json::to_string(&r).unwrap();
        let r2: SessionRecord = serde_json::from_str(&json).unwrap();
        assert_eq!(r, r2);
    }

    #[test]
    fn legacy_record_without_optional_fields_parses() {
        let json = r#"{
            "id": 1,
            "timestamp_ms": 1,
            "transcript": "hi",
            "answer": "hello",
            "steps_count": 0
        }"#;
        let r: SessionRecord = serde_json::from_str(json).unwrap();
        assert!(r.grounding_source.is_none());
        assert!(r.total_ms.is_none());
    }

    #[test]
    fn truncate_keeps_most_recent() {
        let mut v: Vec<SessionRecord> = (1..=5).map(fixture).collect();
        truncate_to_limit(&mut v, 3);
        assert_eq!(v.len(), 3);
        // Oldest two dropped; newest three retained in original order.
        assert_eq!(v[0].id, 3);
        assert_eq!(v[1].id, 4);
        assert_eq!(v[2].id, 5);
    }

    #[test]
    fn truncate_noop_when_under_limit() {
        let mut v: Vec<SessionRecord> = (1..=3).map(fixture).collect();
        truncate_to_limit(&mut v, 10);
        assert_eq!(v.len(), 3);
    }

    #[test]
    fn record_from_pipeline_result_full() {
        let result = serde_json::json!({
            "transcript": "click save",
            "answer": "I clicked save.",
            "steps": [
                {"x": 0.5, "y": 0.5, "explanation": "save"}
            ],
            "grounding_source": "ax",
            "timings": { "total_ms": 1234 }
        });
        let r = record_from_pipeline_result(&result);
        assert_eq!(r.transcript, "click save");
        assert_eq!(r.answer, "I clicked save.");
        assert_eq!(r.steps_count, 1);
        assert_eq!(r.grounding_source.as_deref(), Some("ax"));
        assert_eq!(r.total_ms, Some(1234));
    }

    #[test]
    fn record_from_pipeline_result_minimal() {
        let result = serde_json::json!({
            "transcript": "hi",
            "answer": "hello",
        });
        let r = record_from_pipeline_result(&result);
        assert_eq!(r.transcript, "hi");
        assert_eq!(r.answer, "hello");
        assert_eq!(r.steps_count, 0);
        assert!(r.grounding_source.is_none());
        assert!(r.total_ms.is_none());
    }

    #[test]
    fn record_from_pipeline_result_empty() {
        let result = serde_json::json!({});
        let r = record_from_pipeline_result(&result);
        assert!(r.transcript.is_empty());
        assert!(r.answer.is_empty());
        assert_eq!(r.steps_count, 0);
    }
}
