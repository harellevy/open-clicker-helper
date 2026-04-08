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
    "qwen2.5-vl:7b".into()
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
