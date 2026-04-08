//! Persistent application settings via tauri-plugin-store.
//!
//! A single JSON file (`settings.json`) in the app's data dir holds the full
//! config.  All fields have sensible defaults so the store is safe to read
//! before the setup wizard has run.

use serde::{Deserialize, Serialize};

pub const STORE_FILE: &str = "settings.json";
pub const SETTINGS_KEY: &str = "settings";

/// Top-level settings persisted to `settings.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// `false` until the user completes (or skips) the first-run wizard.
    #[serde(default)]
    pub setup_complete: bool,

    /// Tauri global-shortcut accelerator string.
    #[serde(default = "default_hotkey")]
    pub hotkey: String,

    #[serde(default)]
    pub stt: SttSettings,
    #[serde(default)]
    pub vlm: VlmSettings,
    #[serde(default)]
    pub tts: TtsSettings,

    /// Debug mode — when enabled the overlay shows per-stage timings, a
    /// downscaled screenshot preview, and the raw VLM output.
    #[serde(default)]
    pub debug: DebugSettings,

    /// User-editable system prompts, one per stage of the pipeline.
    #[serde(default)]
    pub system_prompts: SystemPrompts,
}

fn default_hotkey() -> String {
    "CommandOrControl+Shift+Space".into()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            setup_complete: false,
            hotkey: default_hotkey(),
            stt: SttSettings::default(),
            vlm: VlmSettings::default(),
            tts: TtsSettings::default(),
            debug: DebugSettings::default(),
            system_prompts: SystemPrompts::default(),
        }
    }
}

/// Debug-mode toggle (plus a knob for how long each overlay stage sticks
/// around, so users can tune the UX to their tolerance).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DebugSettings {
    #[serde(default)]
    pub enabled: bool,
}

impl Default for DebugSettings {
    fn default() -> Self {
        Self { enabled: false }
    }
}

/// System prompts the user can customise per stage of the pipeline. These
/// strings are forwarded verbatim to the sidecar; an empty string means "use
/// the sidecar's built-in default".
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemPrompts {
    #[serde(default)]
    pub grounding: String,
    #[serde(default)]
    pub caption: String,
}

impl Default for SystemPrompts {
    fn default() -> Self {
        Self {
            grounding: String::new(),
            caption: String::new(),
        }
    }
}

/// Speech-to-text configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SttSettings {
    /// `"mlx-whisper"` (offline, Apple Silicon only) or `"openai"`.
    #[serde(default = "stt_default_provider")]
    pub provider: String,
    /// HuggingFace model id used by mlx-whisper.
    #[serde(default = "stt_default_mlx_model")]
    pub mlx_model: String,
    #[serde(default)]
    pub openai_key: Option<String>,
}

fn stt_default_provider() -> String {
    "mlx-whisper".into()
}
fn stt_default_mlx_model() -> String {
    "mlx-community/whisper-base-mlx".into()
}

impl Default for SttSettings {
    fn default() -> Self {
        Self {
            provider: stt_default_provider(),
            mlx_model: stt_default_mlx_model(),
            openai_key: None,
        }
    }
}

/// Vision-language model configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VlmSettings {
    /// `"ollama"`, `"openai"`, or `"anthropic"`.
    #[serde(default = "vlm_default_provider")]
    pub provider: String,
    #[serde(default = "vlm_default_ollama_model")]
    pub ollama_model: String,
    /// Base URL for a local Ollama instance.
    #[serde(default = "vlm_default_ollama_url")]
    pub ollama_url: String,
    #[serde(default = "vlm_default_openai_model")]
    pub openai_model: String,
    #[serde(default)]
    pub openai_key: Option<String>,
    #[serde(default)]
    pub anthropic_key: Option<String>,
}

fn vlm_default_provider() -> String {
    "ollama".into()
}
fn vlm_default_ollama_model() -> String {
    "qwen2.5vl:7b".into()
}
fn vlm_default_ollama_url() -> String {
    "http://localhost:11434".into()
}
fn vlm_default_openai_model() -> String {
    "gpt-4o".into()
}

impl Default for VlmSettings {
    fn default() -> Self {
        Self {
            provider: vlm_default_provider(),
            ollama_model: vlm_default_ollama_model(),
            ollama_url: vlm_default_ollama_url(),
            openai_model: vlm_default_openai_model(),
            openai_key: None,
            anthropic_key: None,
        }
    }
}

/// Text-to-speech configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtsSettings {
    /// `"kokoro"` (offline) or `"openai"`.
    #[serde(default = "tts_default_provider")]
    pub provider: String,
    /// Kokoro voice id.
    #[serde(default = "tts_default_voice")]
    pub kokoro_voice: String,
    /// OpenAI TTS voice name.
    #[serde(default = "tts_default_openai_voice")]
    pub openai_voice: String,
    #[serde(default)]
    pub openai_key: Option<String>,
}

fn tts_default_provider() -> String {
    "kokoro".into()
}
fn tts_default_voice() -> String {
    "af_heart".into()
}
fn tts_default_openai_voice() -> String {
    "nova".into()
}

impl Default for TtsSettings {
    fn default() -> Self {
        Self {
            provider: tts_default_provider(),
            kokoro_voice: tts_default_voice(),
            openai_voice: tts_default_openai_voice(),
            openai_key: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_default_values() {
        let s = Settings::default();
        assert!(!s.setup_complete);
        assert_eq!(s.hotkey, "CommandOrControl+Shift+Space");
        assert_eq!(s.stt.provider, "mlx-whisper");
        assert_eq!(s.stt.mlx_model, "mlx-community/whisper-base-mlx");
        assert_eq!(s.vlm.provider, "ollama");
        assert_eq!(s.vlm.ollama_model, "qwen2.5vl:7b");
        assert_eq!(s.vlm.ollama_url, "http://localhost:11434");
        assert_eq!(s.tts.provider, "kokoro");
        assert_eq!(s.tts.kokoro_voice, "af_heart");
    }

    #[test]
    fn settings_roundtrip_serde() {
        let s = Settings::default();
        let json = serde_json::to_string(&s).unwrap();
        let s2: Settings = serde_json::from_str(&json).unwrap();
        assert_eq!(s.hotkey, s2.hotkey);
        assert_eq!(s.setup_complete, s2.setup_complete);
        assert_eq!(s.stt.provider, s2.stt.provider);
        assert_eq!(s.vlm.ollama_url, s2.vlm.ollama_url);
        assert_eq!(s.tts.kokoro_voice, s2.tts.kokoro_voice);
    }

    #[test]
    fn settings_missing_fields_use_defaults() {
        let s: Settings = serde_json::from_str(r#"{"hotkey":"Ctrl+Alt+X"}"#).unwrap();
        assert_eq!(s.hotkey, "Ctrl+Alt+X");
        assert!(!s.setup_complete);
        assert_eq!(s.stt.provider, "mlx-whisper");
        assert_eq!(s.tts.provider, "kokoro");
    }

    #[test]
    fn empty_object_deserializes_to_defaults() {
        let s: Settings = serde_json::from_str("{}").unwrap();
        assert_eq!(s.hotkey, "CommandOrControl+Shift+Space");
        assert!(!s.setup_complete);
    }

    #[test]
    fn setup_complete_roundtrips() {
        let json = r#"{"setup_complete":true}"#;
        let s: Settings = serde_json::from_str(json).unwrap();
        assert!(s.setup_complete);
    }

    #[test]
    fn debug_defaults_off() {
        let s = Settings::default();
        assert!(!s.debug.enabled);
    }

    #[test]
    fn debug_enabled_roundtrips() {
        let json = r#"{"debug":{"enabled":true}}"#;
        let s: Settings = serde_json::from_str(json).unwrap();
        assert!(s.debug.enabled);
    }

    #[test]
    fn system_prompts_default_empty() {
        let s = Settings::default();
        assert!(s.system_prompts.grounding.is_empty());
        assert!(s.system_prompts.caption.is_empty());
    }

    #[test]
    fn system_prompts_roundtrip() {
        let json = r#"{"system_prompts":{"grounding":"hi","caption":"describe"}}"#;
        let s: Settings = serde_json::from_str(json).unwrap();
        assert_eq!(s.system_prompts.grounding, "hi");
        assert_eq!(s.system_prompts.caption, "describe");
    }

    #[test]
    fn legacy_settings_without_new_fields_still_parse() {
        // A settings.json written by an older build must deserialize cleanly
        // — new fields fall back to defaults.
        let json = r#"{"setup_complete":true,"hotkey":"Ctrl+Shift+X"}"#;
        let s: Settings = serde_json::from_str(json).unwrap();
        assert!(!s.debug.enabled);
        assert!(s.system_prompts.grounding.is_empty());
    }
}
