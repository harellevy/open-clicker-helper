import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// ──────────────────────────────────────────────────────────────────────────────
// Permission types
// ──────────────────────────────────────────────────────────────────────────────

export type PermissionStatus = "granted" | "denied" | "unknown";

export interface Permissions {
  screen_recording: PermissionStatus;
  accessibility: PermissionStatus;
  microphone: PermissionStatus;
}

// ──────────────────────────────────────────────────────────────────────────────
// Settings types (mirror src-tauri/src/store.rs)
// ──────────────────────────────────────────────────────────────────────────────

export interface SttSettings {
  provider: "mlx-whisper" | "openai";
  mlx_model: string;
  openai_key: string | null;
}

export interface VlmSettings {
  provider: "ollama" | "openai" | "anthropic";
  ollama_model: string;
  ollama_url: string;
  openai_model: string;
  openai_key: string | null;
  anthropic_key: string | null;
}

export interface TtsSettings {
  provider: "kokoro" | "openai";
  kokoro_voice: string;
  openai_voice: string;
  openai_key: string | null;
}

export interface Settings {
  setup_complete: boolean;
  hotkey: string;
  stt: SttSettings;
  vlm: VlmSettings;
  tts: TtsSettings;
}

export function defaultSettings(): Settings {
  return {
    setup_complete: false,
    hotkey: "CommandOrControl+Shift+Space",
    stt: {
      provider: "mlx-whisper",
      mlx_model: "mlx-community/whisper-base-mlx",
      openai_key: null,
    },
    vlm: {
      provider: "ollama",
      ollama_model: "qwen2.5vl:7b",
      ollama_url: "http://localhost:11434",
      openai_model: "gpt-4o",
      openai_key: null,
      anthropic_key: null,
    },
    tts: {
      provider: "kokoro",
      kokoro_voice: "af_heart",
      openai_voice: "nova",
      openai_key: null,
    },
  };
}

// ──────────────────────────────────────────────────────────────────────────────
// Setup status types
// ──────────────────────────────────────────────────────────────────────────────

export interface SttStatus {
  installed: boolean;
  model_cached: boolean;
  model: string;
  message: string;
}

export interface VlmStatus {
  ollama_running: boolean;
  model_pulled: boolean;
  model: string;
  available_models: string[];
  message: string;
}

export interface TtsStatus {
  installed: boolean;
  voice_ready: boolean;
  voice: string;
  message: string;
}

export interface SetupStatus {
  stt: SttStatus;
  vlm: VlmStatus;
  tts: TtsStatus;
}

// Progress notification from the sidecar relay
export interface SidecarProgress {
  id: number;
  event: string;     // "status" | "progress" | "result"
  payload: {
    step: "stt" | "vlm" | "tts";
    message?: string;
    progress?: number; // 0-100
    ok?: boolean;
    error?: string;
  };
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms?: number;
  error?: string;
}

export interface SidecarHealth {
  ok: boolean;
  version: string | null;
}

// ──────────────────────────────────────────────────────────────────────────────
// API
// ──────────────────────────────────────────────────────────────────────────────

export const api = {
  // Permissions
  getPermissions: () => invoke<Permissions>("get_permissions"),
  openSystemSettings: (pane: "screen_recording" | "accessibility" | "microphone") =>
    invoke<void>("open_system_settings", { pane }),

  // Settings store
  getSettings: () => invoke<Settings>("get_settings"),
  saveSettings: (settings: Settings) => invoke<void>("save_settings", { settings }),
  resetSettings: () => invoke<Settings>("reset_settings"),

  // Sidecar
  pingSidecar: () => invoke<SidecarHealth>("ping_sidecar"),

  // Setup checks
  setupCheckAll: (params?: {
    stt_model?: string;
    vlm_model?: string;
    ollama_url?: string;
    tts_voice?: string;
  }) => invoke<SetupStatus>("sidecar_call", { method: "setup.check", params: params ?? {} }),

  setupCheckStt: (model?: string) =>
    invoke<SttStatus>("sidecar_call", {
      method: "setup.check_stt",
      params: model ? { model } : {},
    }),

  setupCheckVlm: (model?: string, base_url?: string) =>
    invoke<VlmStatus>("sidecar_call", {
      method: "setup.check_vlm",
      params: { ...(model ? { model } : {}), ...(base_url ? { base_url } : {}) },
    }),

  setupCheckTts: (voice?: string) =>
    invoke<TtsStatus>("sidecar_call", {
      method: "setup.check_tts",
      params: voice ? { voice } : {},
    }),

  // Setup downloads (long-running; progress arrives via sidecar://progress events)
  setupDownloadStt: (model?: string) =>
    invoke<{ ok: boolean; step: string; error?: string }>("sidecar_call", {
      method: "setup.download_stt",
      params: model ? { model } : {},
    }),

  setupDownloadVlm: (model?: string, base_url?: string) =>
    invoke<{ ok: boolean; step: string; error?: string }>("sidecar_call", {
      method: "setup.download_vlm",
      params: { ...(model ? { model } : {}), ...(base_url ? { base_url } : {}) },
    }),

  setupDownloadTts: (voice?: string) =>
    invoke<{ ok: boolean; step: string; error?: string }>("sidecar_call", {
      method: "setup.download_tts",
      params: voice ? { voice } : {},
    }),

  // Provider connectivity test
  testProvider: (type: "stt" | "vlm" | "tts", provider: string, config: object) =>
    invoke<ProviderTestResult>("sidecar_call", {
      method: "providers.test",
      params: { type, provider, config },
    }),
};

/** Subscribe to progress notifications relayed from the Python sidecar. */
export function onSidecarProgress(
  cb: (p: SidecarProgress) => void
): Promise<() => void> {
  return listen<SidecarProgress>("sidecar://progress", (ev) => cb(ev.payload));
}

// ──────────────────────────────────────────────────────────────────────────────
// P3: Push-to-talk hotkey state + pipeline result
// ──────────────────────────────────────────────────────────────────────────────

/** State emitted by the Rust hotkey handler on every transition. */
export type HotkeyState =
  | { state: "idle" }
  | { state: "recording" }
  | { state: "processing" }
  | { state: "error"; message: string };

/** Final result emitted when the pipeline completes successfully. */
export interface PipelineResult {
  transcript: string;
  answer: string;
  /** Base64-encoded WAV bytes of the TTS response. */
  audio_b64: string;
  /** Grounding steps (empty array in text-only mode). */
  steps: GroundingStep[];
}

/** A single VLM-grounded action step with normalised coordinates. */
export interface GroundingStep {
  /** Normalised horizontal position (0 = left edge, 1 = right edge). */
  x: number;
  /** Normalised vertical position (0 = top edge, 1 = bottom edge). */
  y: number;
  explanation: string;
}

/** Listen for push-to-talk state transitions from the Rust shell. */
export function onHotkeyState(cb: (s: HotkeyState) => void): Promise<() => void> {
  return listen<HotkeyState>("hotkey-state", (ev) => cb(ev.payload));
}

/** Listen for pipeline completion events. */
export function onPipelineResult(cb: (r: PipelineResult) => void): Promise<() => void> {
  return listen<PipelineResult>("pipeline-result", (ev) => cb(ev.payload));
}

/**
 * Synthesise a left-click at normalised (0–1) screen coordinates.
 * Converts to physical pixels on the Rust side via the primary monitor size.
 */
export function clickAtNormalized(x: number, y: number): Promise<void> {
  return invoke<void>("click_at_normalized", { x, y });
}

/**
 * Re-ground a question against a new screenshot without STT/TTS.
 * Used by the iterative multi-step loop after each click.
 */
export function groundingLocate(
  imageb64: string,
  question: string,
  settings?: object,
): Promise<{ steps: GroundingStep[] }> {
  return invoke("sidecar_call", {
    method: "grounding.locate",
    params: { image_b64: imageb64, question, settings: settings ?? {} },
  });
}

/** Capture the primary display and return base64 PNG (null if permission denied). */
export function captureScreen(): Promise<string | null> {
  return invoke<string | null>("capture_screen");
}

/** Play a base64-encoded WAV file using the Web Audio API. */
export async function playAudioB64(b64: string): Promise<void> {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const ctx = new AudioContext();
  const buffer = await ctx.decodeAudioData(bytes.buffer);
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  src.start();
  return new Promise((resolve) => {
    src.onended = () => {
      void ctx.close();
      resolve();
    };
  });
}
