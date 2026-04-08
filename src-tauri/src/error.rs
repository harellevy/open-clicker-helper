use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("platform error: {0}")]
    Platform(String),
    #[error("sidecar error: {0}")]
    Sidecar(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("serde error: {0}")]
    Serde(#[from] serde_json::Error),
}

pub type AppResult<T> = Result<T, AppError>;

// Tauri commands need a Serialize error.
impl Serialize for AppError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(&self.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn platform_error_serializes_to_string() {
        let e = AppError::Platform("no device".into());
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(
            v,
            serde_json::Value::String("platform error: no device".into())
        );
    }

    #[test]
    fn sidecar_error_serializes_to_string() {
        let e = AppError::Sidecar("timeout".into());
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(
            v,
            serde_json::Value::String("sidecar error: timeout".into())
        );
    }

    #[test]
    fn io_error_display() {
        let e = AppError::Io(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "file missing",
        ));
        assert!(e.to_string().contains("file missing"));
    }

    #[test]
    fn serde_error_converts_from() {
        let bad: Result<serde_json::Value, _> = serde_json::from_str("{bad}");
        let e: AppError = bad.unwrap_err().into();
        assert!(matches!(e, AppError::Serde(_)));
    }
}
