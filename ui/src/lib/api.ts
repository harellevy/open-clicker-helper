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

export interface DebugSettings {
  enabled: boolean;
}

export interface SystemPromptsSettings {
  /** Prompt for the VLM grounding call (the one that returns click JSON). */
  grounding: string;
  /** Prompt for the debug-only "describe the screen" caption call. */
  caption: string;
}

/**
 * Grounding strategy selector.
 *
 * - `"auto"` (default): probe the macOS Accessibility tree first, fall back
 *   to VLM if no candidate matches the question.
 * - `"ax"`: AX-only — refuse to run the VLM. Useful for latency-critical
 *   native-app workflows where a VLM miss is worse than a no-op.
 * - `"vlm"`: VLM-only — skip AX probing entirely (current pre-P4.2 behaviour).
 */
export type GroundingMode = "auto" | "ax" | "vlm";

export interface GroundingSettings {
  mode: GroundingMode;
  /**
   * When true, run a second VLM pass on a full-resolution crop around each
   * rough target to tighten pixel accuracy. Adds one VLM call per step.
   */
  refine: boolean;
}

export interface Settings {
  setup_complete: boolean;
  hotkey: string;
  stt: SttSettings;
  vlm: VlmSettings;
  tts: TtsSettings;
  debug: DebugSettings;
  system_prompts: SystemPromptsSettings;
  grounding: GroundingSettings;
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
    debug: { enabled: false },
    system_prompts: { grounding: "", caption: "" },
    grounding: { mode: "auto", refine: true },
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

  // Conversation history
  getHistory: () => invoke<SessionRecord[]>("get_history"),
  clearHistory: () => invoke<void>("clear_history"),
};

// ──────────────────────────────────────────────────────────────────────────────
// Conversation history (mirrors src-tauri/src/history.rs)
// ──────────────────────────────────────────────────────────────────────────────

export interface SessionRecord {
  /** Monotonically increasing id (unix-millis at write time). */
  id: number;
  /** Wall-clock time the pipeline finished (unix millis). */
  timestamp_ms: number;
  /** Transcribed user question. */
  transcript: string;
  /** Spoken assistant answer. */
  answer: string;
  /** Number of grounding steps produced (0 in text-only mode). */
  steps_count: number;
  /** "ax", "vlm", or null for text-only sessions. */
  grounding_source: string | null;
  /** End-to-end pipeline duration in milliseconds, if reported. */
  total_ms: number | null;
}

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

/** Per-stage timing measurements, in milliseconds. */
export interface PipelineTimings {
  stt_ms?: number;
  downscale_ms?: number;
  caption_ms?: number;
  grounding_ms?: number;
  refine_ms?: number;
  llm_ms?: number;
  tts_ms?: number;
  total_ms?: number;
}

/** Debug payload attached to pipeline results when debug mode is on. */
export interface PipelineDebug {
  transcript: string;
  caption?: string;
  screenshot_b64?: string;
  orig_size?: [number, number];
  new_size?: [number, number];
  orig_bytes?: number;
  new_bytes?: number;
  grounding_raw?: string;
  steps?: GroundingStep[];
  timings?: PipelineTimings;
  answer?: string;
}

/** Final result emitted when the pipeline completes successfully. */
export interface PipelineResult {
  transcript: string;
  answer: string;
  /** Base64-encoded WAV bytes of the TTS response. */
  audio_b64: string;
  /** Grounding steps (empty array in text-only mode). */
  steps: GroundingStep[];
  /** Per-stage timings (milliseconds). */
  timings?: PipelineTimings;
  /** Present only when debug mode is enabled in settings. */
  debug?: PipelineDebug;
  /**
   * Non-empty when the pipeline short-circuited without running the full
   * stages. Currently only "empty_transcript" — the STT produced no speech
   * so the VLM/TTS were skipped.
   */
  cancelled?: string;
}

/** Progress notification from the sidecar relay (pipeline stage updates). */
export interface PipelineProgress {
  id: number;
  event: string; // "stt_start" | "stt_done" | "image_downscaled" | ...
  payload: {
    transcript?: string;
    answer?: string;
    elapsed_ms?: number;
    caption?: string;
    image_b64?: string;
    orig_size?: [number, number];
    new_size?: [number, number];
    orig_bytes?: number;
    new_bytes?: number;
    steps?: GroundingStep[];
    raw?: string;
    error?: string;
  };
}

/** Known pipeline stage event names the overlay reacts to. */
const PIPELINE_EVENTS = new Set([
  "stt_start",
  "stt_done",
  "image_downscaled",
  "caption_start",
  "caption_done",
  "grounding_start",
  "grounding_done",
  "refine_start",
  "refine_done",
  "llm_start",
  "llm_done",
  "tts_start",
  "tts_done",
  "error",
]);

/** Listen for per-stage pipeline progress events (debug-mode UI).
 * Shares the `sidecar://progress` Tauri channel with setup downloads, but
 * filters by event name so those streams don't confuse each other. */
export function onPipelineProgress(
  cb: (p: PipelineProgress) => void,
): Promise<() => void> {
  return listen<PipelineProgress>("sidecar://progress", (ev) => {
    const payload = ev.payload as unknown as PipelineProgress;
    if (!payload || !PIPELINE_EVENTS.has(payload.event)) return;
    cb(payload);
  });
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

/** One clickable candidate returned by the macOS Accessibility walker. */
export interface AxCandidate {
  role: string;
  title: string;
  description: string;
  /** Screen-space frame in pixels. */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Walk the macOS Accessibility (AX) tree of the frontmost app's focused
 * window and return clickable candidates in screen-pixel coordinates.
 *
 * Returns an empty array on non-macOS platforms, when AX permission has not
 * been granted, or when the focused app exposes no AX tree.
 */
export function axLocate(): Promise<AxCandidate[]> {
  return invoke<AxCandidate[]>("ax_locate");
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
