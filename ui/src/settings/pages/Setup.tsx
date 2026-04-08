/**
 * First-run setup wizard — 5 steps:
 *   1. Permissions
 *   2. STT (mlx-whisper offline or OpenAI key)
 *   3. Vision LLM (Ollama offline or cloud key)
 *   4. TTS (Kokoro offline or OpenAI TTS key)
 *   5. Hotkey
 *
 * Progress during model downloads comes over the `sidecar://progress`
 * Tauri event (relayed from Python generator yields) so the UI stays
 * responsive while large weights are being pulled.
 */

import { useEffect, useRef, useState } from "react";
import {
  type Permissions,
  type Settings,
  type SidecarProgress,
  type SttStatus,
  type TtsStatus,
  type VlmStatus,
  api,
  onSidecarProgress,
} from "@/lib/api";

type Step = "permissions" | "stt" | "vlm" | "tts" | "hotkey";

const STEPS: Step[] = ["permissions", "stt", "vlm", "tts", "hotkey"];
const STEP_LABELS: Record<Step, string> = {
  permissions: "Permissions",
  stt: "Speech-to-Text",
  vlm: "Vision LLM",
  tts: "Text-to-Speech",
  hotkey: "Hotkey",
};

interface StepProgress {
  status: "idle" | "checking" | "downloading" | "done" | "error";
  progress: number; // 0-100
  message: string;
  error?: string;
}

const idle = (): StepProgress => ({ status: "idle", progress: 0, message: "" });

interface Props {
  settings: Settings;
  onComplete: (updated: Settings) => void;
}

export function Setup({ settings, onComplete }: Props) {
  const [step, setStep] = useState<Step>("permissions");
  const [draft, setDraft] = useState<Settings>(settings);

  const [permissions, setPermissions] = useState<Permissions | null>(null);
  const [sttStatus, setSttStatus] = useState<SttStatus | null>(null);
  const [vlmStatus, setVlmStatus] = useState<VlmStatus | null>(null);
  const [ttsStatus, setTtsStatus] = useState<TtsStatus | null>(null);

  const [sttProgress, setSttProgress] = useState<StepProgress>(idle());
  const [vlmProgress, setVlmProgress] = useState<StepProgress>(idle());
  const [ttsProgress, setTtsProgress] = useState<StepProgress>(idle());

  const unlistenRef = useRef<(() => void) | null>(null);

  // Subscribe to sidecar progress events once on mount.
  useEffect(() => {
    onSidecarProgress(handleProgress).then((unlisten) => {
      unlistenRef.current = unlisten;
    });
    return () => unlistenRef.current?.();
  }, []);

  function handleProgress(p: SidecarProgress) {
    const { step: s, message = "", progress = 0, ok, error } = p.payload;
    const setter = s === "stt" ? setSttProgress : s === "vlm" ? setVlmProgress : setTtsProgress;

    if (p.event === "progress") {
      setter({ status: "downloading", progress, message });
    } else if (p.event === "status") {
      setter((prev) => ({ ...prev, status: "downloading", message }));
    } else if (p.event === "result") {
      setter({
        status: ok ? "done" : "error",
        progress: ok ? 100 : 0,
        message: ok ? "Ready" : (error ?? "failed"),
        error: error,
      });
      if (ok) {
        // Refresh status after successful download.
        if (s === "stt") fetchSttStatus();
        if (s === "vlm") fetchVlmStatus();
        if (s === "tts") fetchTtsStatus();
      }
    }
  }

  // ── Fetch helpers ────────────────────────────────────────────────────────

  async function fetchPermissions() {
    try {
      setPermissions(await api.getPermissions());
    } catch { /* ignore */ }
  }

  async function fetchSttStatus(autoDownload = false) {
    setSttProgress((p) => ({ ...p, status: "checking", message: "Checking…" }));
    try {
      const s = await api.setupCheckStt(draft.stt.mlx_model);
      setSttStatus(s);
      setSttProgress(idle());
      if (autoDownload && draft.stt.provider === "mlx-whisper" && (!s.installed || !s.model_cached)) {
        api.setupDownloadStt(draft.stt.mlx_model).catch(console.error);
      }
    } catch (e) {
      setSttProgress({ status: "error", progress: 0, message: String(e) });
    }
  }

  async function fetchVlmStatus() {
    setVlmProgress((p) => ({ ...p, status: "checking", message: "Checking…" }));
    try {
      const s = await api.setupCheckVlm(draft.vlm.ollama_model, draft.vlm.ollama_url);
      setVlmStatus(s);
      setVlmProgress(idle());
    } catch (e) {
      setVlmProgress({ status: "error", progress: 0, message: String(e) });
    }
  }

  async function fetchTtsStatus(autoDownload = false) {
    setTtsProgress((p) => ({ ...p, status: "checking", message: "Checking…" }));
    try {
      const s = await api.setupCheckTts(draft.tts.kokoro_voice);
      setTtsStatus(s);
      setTtsProgress(idle());
      if (autoDownload && draft.tts.provider === "kokoro" && (!s.installed || !s.voice_ready)) {
        api.setupDownloadTts(draft.tts.kokoro_voice).catch(console.error);
      }
    } catch (e) {
      setTtsProgress({ status: "error", progress: 0, message: String(e) });
    }
  }

  // Fetch status when entering each step; auto-start offline downloads.
  useEffect(() => {
    if (step === "permissions") fetchPermissions();
    if (step === "stt") fetchSttStatus(true);
    if (step === "vlm") fetchVlmStatus();
    if (step === "tts") fetchTtsStatus(true);
  }, [step]);

  // ── Navigation ───────────────────────────────────────────────────────────

  function goNext() {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1]);
  }

  function goPrev() {
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  }

  async function finish() {
    const updated: Settings = { ...draft, setup_complete: true };
    await api.saveSettings(updated);
    onComplete(updated);
  }

  // ── Render ───────────────────────────────────────────────────────────────

  const stepIdx = STEPS.indexOf(step);

  return (
    <div className="wizard">
      {/* Progress dots */}
      <div className="wizard__steps">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`wizard__dot ${i < stepIdx ? "wizard__dot--done" : i === stepIdx ? "wizard__dot--active" : ""}`}
            title={STEP_LABELS[s]}
          />
        ))}
      </div>

      <div className="wizard__body">
        <h2 className="wizard__title">{STEP_LABELS[step]}</h2>

        {step === "permissions" && (
          <PermissionsStep perms={permissions} onRefresh={fetchPermissions} />
        )}
        {step === "stt" && (
          <SttStep
            draft={draft}
            status={sttStatus}
            progress={sttProgress}
            onChange={(stt) => setDraft((d) => ({ ...d, stt }))}
            onDownload={() =>
              api.setupDownloadStt(draft.stt.mlx_model).catch(console.error)
            }
          />
        )}
        {step === "vlm" && (
          <VlmStep
            draft={draft}
            status={vlmStatus}
            progress={vlmProgress}
            onChange={(vlm) => setDraft((d) => ({ ...d, vlm }))}
            onPull={() =>
              api
                .setupDownloadVlm(draft.vlm.ollama_model, draft.vlm.ollama_url)
                .catch(console.error)
            }
          />
        )}
        {step === "tts" && (
          <TtsStep
            draft={draft}
            status={ttsStatus}
            progress={ttsProgress}
            onChange={(tts) => setDraft((d) => ({ ...d, tts }))}
            onDownload={() =>
              api.setupDownloadTts(draft.tts.kokoro_voice).catch(console.error)
            }
          />
        )}
        {step === "hotkey" && (
          <HotkeyStep
            hotkey={draft.hotkey}
            onChange={(hotkey) => setDraft((d) => ({ ...d, hotkey }))}
          />
        )}
      </div>

      {/* Footer nav */}
      <div className="wizard__footer">
        {stepIdx > 0 && (
          <button className="btn btn--ghost" onClick={goPrev}>
            Back
          </button>
        )}
        <span style={{ flex: 1 }} />
        {stepIdx < STEPS.length - 1 ? (
          <button className="btn btn--primary" onClick={goNext}>
            Continue
          </button>
        ) : (
          <button className="btn btn--primary" onClick={finish}>
            Done
          </button>
        )}
      </div>
    </div>
  );
}

// ── Sub-steps ─────────────────────────────────────────────────────────────────

function PermissionsStep({
  perms,
  onRefresh,
}: {
  perms: Permissions | null;
  onRefresh: () => void;
}) {
  return (
    <div className="setup-section">
      <p className="setup-desc">
        open-clicker-helper needs three macOS permissions to work. Click{" "}
        <strong>Fix</strong> to open the relevant System Settings pane, grant
        access, then come back and click <strong>Refresh</strong>.
      </p>
      <div className="perm-list">
        <PermRow
          label="Screen Recording"
          status={perms?.screen_recording ?? "unknown"}
          pane="screen_recording"
          note="Required to screenshot the focused window before each query."
        />
        <PermRow
          label="Accessibility"
          status={perms?.accessibility ?? "unknown"}
          pane="accessibility"
          note="Required to simulate mouse clicks (optional — disable auto-click to skip)."
        />
        <PermRow
          label="Microphone"
          status={perms?.microphone ?? "unknown"}
          pane="microphone"
          note="Required to record your voice question."
        />
      </div>
      <button className="btn btn--ghost" onClick={onRefresh}>
        Refresh
      </button>
    </div>
  );
}

function PermRow({
  label,
  status,
  pane,
  note,
}: {
  label: string;
  status: "granted" | "denied" | "unknown";
  pane: "screen_recording" | "accessibility" | "microphone";
  note: string;
}) {
  return (
    <div className="perm-row">
      <div className="perm-row__info">
        <span className="perm-row__label">{label}</span>
        <span className="perm-row__note">{note}</span>
      </div>
      <div className="perm-row__actions">
        <span className={`status status--${status}`}>{status}</span>
        {status !== "granted" && (
          <button
            className="btn btn--sm btn--ghost"
            onClick={() => api.openSystemSettings(pane)}
          >
            Fix →
          </button>
        )}
      </div>
    </div>
  );
}

function SttStep({
  draft,
  status,
  progress,
  onChange,
  onDownload,
}: {
  draft: Settings;
  status: SttStatus | null;
  progress: StepProgress;
  onChange: (s: Settings["stt"]) => void;
  onDownload: () => void;
}) {
  const isOffline = draft.stt.provider === "mlx-whisper";
  const isReady = status?.installed && status?.model_cached;
  const isActive = progress.status === "downloading" || progress.status === "checking";

  return (
    <div className="setup-section">
      <p className="setup-desc">
        Speech-to-text converts your voice question into text. The default
        (mlx-whisper) runs fully offline on Apple Silicon. Switch to OpenAI
        Whisper if you don't need privacy or lack the ~150 MB of disk space.
      </p>
      <ProviderToggle
        options={[
          { id: "mlx-whisper", label: "mlx-whisper (offline)" },
          { id: "openai", label: "OpenAI Whisper" },
        ]}
        value={draft.stt.provider}
        onChange={(v) => onChange({ ...draft.stt, provider: v as "mlx-whisper" | "openai" })}
      />

      {isOffline ? (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>Model</label>
            <input
              className="input"
              value={draft.stt.mlx_model}
              onChange={(e) => onChange({ ...draft.stt, mlx_model: e.target.value })}
            />
          </div>
          {status && (
            <StatusLine ok={isReady ?? false} message={status.message} />
          )}
          {isActive && (
            <ProgressBar
              progress={progress.progress}
              message={progress.message}
            />
          )}
          {progress.status === "error" && (
            <>
              <p className="setup-error">{progress.error}</p>
              <button className="btn btn--primary" onClick={onDownload}>
                Retry download
              </button>
            </>
          )}
          {!isReady && !isActive && progress.status !== "error" && (
            <button className="btn btn--ghost btn--sm" onClick={onDownload}>
              Download manually
            </button>
          )}
        </div>
      ) : (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>OpenAI API key</label>
            <input
              className="input"
              type="password"
              placeholder="sk-…"
              value={draft.stt.openai_key ?? ""}
              onChange={(e) =>
                onChange({ ...draft.stt, openai_key: e.target.value || null })
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}

function VlmStep({
  draft,
  status,
  progress,
  onChange,
  onPull,
}: {
  draft: Settings;
  status: VlmStatus | null;
  progress: StepProgress;
  onChange: (v: Settings["vlm"]) => void;
  onPull: () => void;
}) {
  const isOffline = draft.vlm.provider === "ollama";
  const isReady = status?.ollama_running && status?.model_pulled;
  const isActive = progress.status === "downloading" || progress.status === "checking";

  return (
    <div className="setup-section">
      <p className="setup-desc">
        The vision LLM looks at a screenshot and figures out where to click.
        Ollama (offline) uses qwen2.5vl:7b locally — needs ~5 GB free disk. Cloud
        providers (OpenAI, Anthropic) work without a GPU but send your screen to
        the internet.
      </p>
      <ProviderToggle
        options={[
          { id: "ollama", label: "Ollama (offline)" },
          { id: "openai", label: "OpenAI GPT-4o" },
          { id: "anthropic", label: "Anthropic Claude" },
        ]}
        value={draft.vlm.provider}
        onChange={(v) =>
          onChange({ ...draft.vlm, provider: v as "ollama" | "openai" | "anthropic" })
        }
      />

      {isOffline ? (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>Ollama URL</label>
            <input
              className="input"
              value={draft.vlm.ollama_url}
              onChange={(e) => onChange({ ...draft.vlm, ollama_url: e.target.value })}
            />
          </div>
          <div className="setup-row">
            <label>Model</label>
            <input
              className="input"
              value={draft.vlm.ollama_model}
              onChange={(e) => onChange({ ...draft.vlm, ollama_model: e.target.value })}
            />
          </div>
          {status && (
            <>
              <StatusLine ok={!!status.ollama_running} message={status.ollama_running ? "Ollama running" : "Ollama not running — download from ollama.com/download"} />
              {status.ollama_running && (
                <StatusLine ok={!!status.model_pulled} message={status.model_pulled ? `${draft.vlm.ollama_model} ready` : `${draft.vlm.ollama_model} not pulled yet`} />
              )}
            </>
          )}
          {!isReady && !isActive && status?.ollama_running && (
            <button className="btn btn--primary" onClick={onPull}>
              Pull {draft.vlm.ollama_model}
            </button>
          )}
          {isActive && (
            <ProgressBar progress={progress.progress} message={progress.message} />
          )}
          {progress.status === "error" && (
            <p className="setup-error">{progress.error}</p>
          )}
        </div>
      ) : draft.vlm.provider === "openai" ? (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>OpenAI API key</label>
            <input
              className="input"
              type="password"
              placeholder="sk-…"
              value={draft.vlm.openai_key ?? ""}
              onChange={(e) =>
                onChange({ ...draft.vlm, openai_key: e.target.value || null })
              }
            />
          </div>
          <div className="setup-row">
            <label>Model</label>
            <input
              className="input"
              value={draft.vlm.openai_model}
              onChange={(e) => onChange({ ...draft.vlm, openai_model: e.target.value })}
            />
          </div>
        </div>
      ) : (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>Anthropic API key</label>
            <input
              className="input"
              type="password"
              placeholder="sk-ant-…"
              value={draft.vlm.anthropic_key ?? ""}
              onChange={(e) =>
                onChange({ ...draft.vlm, anthropic_key: e.target.value || null })
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}

function TtsStep({
  draft,
  status,
  progress,
  onChange,
  onDownload,
}: {
  draft: Settings;
  status: TtsStatus | null;
  progress: StepProgress;
  onChange: (t: Settings["tts"]) => void;
  onDownload: () => void;
}) {
  const isOffline = draft.tts.provider === "kokoro";
  const isReady = status?.installed && status?.voice_ready;
  const isActive = progress.status === "downloading" || progress.status === "checking";

  return (
    <div className="setup-section">
      <p className="setup-desc">
        Text-to-speech reads the LLM's explanation back to you. Kokoro is a
        fast, offline neural TTS model (~82 MB). OpenAI TTS is higher quality
        but requires an API key and network access.
      </p>
      <ProviderToggle
        options={[
          { id: "kokoro", label: "Kokoro (offline)" },
          { id: "openai", label: "OpenAI TTS" },
        ]}
        value={draft.tts.provider}
        onChange={(v) => onChange({ ...draft.tts, provider: v as "kokoro" | "openai" })}
      />

      {isOffline ? (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>Voice</label>
            <select
              className="input"
              value={draft.tts.kokoro_voice}
              onChange={(e) => onChange({ ...draft.tts, kokoro_voice: e.target.value })}
            >
              <option value="af_heart">af_heart (English, female)</option>
              <option value="am_adam">am_adam (English, male)</option>
              <option value="bf_emma">bf_emma (British English, female)</option>
              <option value="bm_lewis">bm_lewis (British English, male)</option>
            </select>
          </div>
          {status && <StatusLine ok={isReady ?? false} message={status.message} />}
          {isActive && (
            <ProgressBar progress={progress.progress} message={progress.message} />
          )}
          {progress.status === "error" && (
            <>
              <p className="setup-error">{progress.error}</p>
              <button className="btn btn--primary" onClick={onDownload}>
                Retry download
              </button>
            </>
          )}
          {!isReady && !isActive && progress.status !== "error" && (
            <button className="btn btn--ghost btn--sm" onClick={onDownload}>
              Download manually
            </button>
          )}
        </div>
      ) : (
        <div className="setup-model-block">
          <div className="setup-row">
            <label>OpenAI API key</label>
            <input
              className="input"
              type="password"
              placeholder="sk-…"
              value={draft.tts.openai_key ?? ""}
              onChange={(e) =>
                onChange({ ...draft.tts, openai_key: e.target.value || null })
              }
            />
          </div>
          <div className="setup-row">
            <label>Voice</label>
            <select
              className="input"
              value={draft.tts.openai_voice}
              onChange={(e) => onChange({ ...draft.tts, openai_voice: e.target.value })}
            >
              {["alloy", "echo", "fable", "nova", "onyx", "shimmer"].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
        </div>
      )}
    </div>
  );
}

function HotkeyStep({
  hotkey,
  onChange,
}: {
  hotkey: string;
  onChange: (h: string) => void;
}) {
  return (
    <div className="setup-section">
      <p className="setup-desc">
        Hold this key combination to start a recording session. Release to send
        your question. You can change it later in Settings.
      </p>
      <HotkeyRecorder value={hotkey} onChange={onChange} />
      <p className="setup-hint">
        Default: <code>⌘ ⇧ Space</code>
      </p>
    </div>
  );
}

// ── Shared mini-components ─────────────────────────────────────────────────────

export function HotkeyRecorder({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [keys, setKeys] = useState<string[]>([]);

  function handleKeyDown(e: React.KeyboardEvent) {
    e.preventDefault();
    const parts: string[] = [];
    if (e.metaKey) parts.push("CommandOrControl");
    else if (e.ctrlKey) parts.push("CommandOrControl");
    if (e.shiftKey) parts.push("Shift");
    if (e.altKey) parts.push("Alt");
    const key = e.key;
    if (!["Meta", "Control", "Shift", "Alt"].includes(key)) {
      parts.push(key === " " ? "Space" : key.length === 1 ? key.toUpperCase() : key);
    }
    setKeys(parts);
    if (parts.length >= 2 && !["Meta", "Control", "Shift", "Alt"].includes(key)) {
      onChange(parts.join("+"));
      setRecording(false);
    }
  }

  return (
    <div className="hotkey-recorder">
      {recording ? (
        <div
          className="hotkey-recorder__capture"
          tabIndex={0}
          autoFocus
          onKeyDown={handleKeyDown}
          onBlur={() => setRecording(false)}
        >
          {keys.length ? keys.join(" + ") : "Press keys…"}
        </div>
      ) : (
        <div className="hotkey-recorder__display">
          <kbd>{value}</kbd>
          <button className="btn btn--sm btn--ghost" onClick={() => { setKeys([]); setRecording(true); }}>
            Change
          </button>
        </div>
      )}
    </div>
  );
}

function ProviderToggle({
  options,
  value,
  onChange,
}: {
  options: { id: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="provider-toggle">
      {options.map((o) => (
        <button
          key={o.id}
          className={`provider-toggle__btn ${value === o.id ? "provider-toggle__btn--active" : ""}`}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function ProgressBar({ progress, message }: { progress: number; message: string }) {
  return (
    <div className="progress-wrap">
      <div className="progress-bar">
        <div className="progress-bar__fill" style={{ width: `${progress}%` }} />
      </div>
      <span className="progress-bar__label">{message || "Working…"}</span>
    </div>
  );
}

function StatusLine({ ok, message }: { ok: boolean; message: string }) {
  return (
    <div className={`status-line ${ok ? "status-line--ok" : "status-line--warn"}`}>
      <span>{ok ? "✓" : "⚠"}</span>
      <span>{message}</span>
    </div>
  );
}
