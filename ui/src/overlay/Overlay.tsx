// Transparent always-on-top overlay window.
//
// P3: Shows a HUD bubble (recording indicator, transcript, answer) driven by
// hotkey-state and pipeline-result Tauri events.
// P4: Adds the SVG Annotation layer for grounding results.
// P5 (this file): When debug mode is enabled in settings, subscribes to
// per-stage pipeline progress events and renders the DebugHud — a dedicated
// debug panel in the bottom-left that shows:
//   • transcript ("caption of question")
//   • the downscaled screenshot the VLM received
//   • the VLM's natural-language description ("what image-to-text sees")
//   • raw grounding JSON / final step coordinates
//   • per-stage timings (ms)

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GroundingStep,
  HotkeyState,
  PipelineProgress,
  PipelineResult,
  Settings,
  api,
  defaultSettings,
  onHotkeyState,
  onPipelineProgress,
  onPipelineResult,
  playAudioB64,
} from "../lib/api";
import { Annotation } from "./Annotation";

// ──────────────────────────────────────────────────────────────────────────────

type Phase = "idle" | "recording" | "processing" | "result" | "error";

interface HudState {
  phase: Phase;
  transcript?: string;
  answer?: string;
  errorMsg?: string;
}

const IDLE: HudState = { phase: "idle" };

// Debug HUD state: one field per pipeline stage, progressively populated as
// sidecar progress events arrive.
interface DebugState {
  visible: boolean;
  transcript?: string;
  sttMs?: number;
  screenshotB64?: string;
  origSize?: [number, number];
  newSize?: [number, number];
  origBytes?: number;
  newBytes?: number;
  downscaleMs?: number;
  captionRunning: boolean;
  caption?: string;
  captionMs?: number;
  groundingRunning: boolean;
  groundingRaw?: string;
  steps?: GroundingStep[];
  groundingMs?: number;
  ttsRunning: boolean;
  ttsMs?: number;
  error?: string;
}

const DEBUG_IDLE: DebugState = {
  visible: false,
  captionRunning: false,
  groundingRunning: false,
  ttsRunning: false,
};

export function Overlay() {
  const [hud, setHud] = useState<HudState>(IDLE);
  const [groundingSteps, setGroundingSteps] = useState<GroundingStep[]>([]);
  const [settings, setSettings] = useState<Settings>(defaultSettings());
  const [debug, setDebug] = useState<DebugState>(DEBUG_IDLE);
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debugDismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debugEnabled = settings.debug.enabled;

  const scheduleAutoDismiss = useCallback(() => {
    if (dismissTimer.current) clearTimeout(dismissTimer.current);
    dismissTimer.current = setTimeout(() => setHud(IDLE), 8000);
  }, []);

  // The debug HUD lingers slightly longer than the standard bubble so the
  // user has time to read the final coordinates + timings.
  const scheduleDebugDismiss = useCallback(() => {
    if (debugDismissTimer.current) clearTimeout(debugDismissTimer.current);
    debugDismissTimer.current = setTimeout(() => setDebug(DEBUG_IDLE), 12000);
  }, []);

  // Load settings once — we need debug.enabled before subscribing to progress.
  // Also reload on every recording-start so toggling it in settings applies
  // immediately without restarting the app.
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch(() => {
        /* keep defaults */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const subs: Promise<() => void>[] = [];

    subs.push(
      onHotkeyState((s: HotkeyState) => {
        if (s.state === "recording") {
          setHud({ phase: "recording" });
          // Refresh settings on every hold-to-talk so a just-toggled debug
          // flag applies on the very next recording.
          api.getSettings().then(setSettings).catch(() => {});
          // Reset the debug panel so stale state from a previous run
          // doesn't leak into the new one.
          setDebug({ ...DEBUG_IDLE, visible: false });
        } else if (s.state === "processing") {
          setHud({ phase: "processing" });
          // Show an empty debug panel immediately so the user sees progress
          // the moment processing begins.
          setDebug((d) => ({ ...d, visible: true }));
        } else if (s.state === "error") {
          setHud({ phase: "error", errorMsg: s.message });
          setDebug((d) => ({ ...d, error: s.message }));
          scheduleAutoDismiss();
          scheduleDebugDismiss();
        } else if (s.state === "idle") {
          // Only auto-dismiss to idle if we're not showing a result.
          setHud((prev) => (prev.phase === "result" ? prev : IDLE));
        }
      }),
    );

    subs.push(
      onPipelineResult(async (r: PipelineResult) => {
        // The sidecar short-circuited (e.g. silence → empty STT). Show a
        // brief notice in the HUD and skip the rest of the result-handling
        // (no steps, no audio playback).
        if (r.cancelled === "empty_transcript") {
          setHud({
            phase: "result",
            transcript: "",
            answer: "I didn't catch anything — try again.",
          });
          scheduleAutoDismiss();
          return;
        }
        setHud({ phase: "result", transcript: r.transcript, answer: r.answer });
        if (r.steps && r.steps.length > 0) {
          setGroundingSteps(r.steps);
        }
        // Final debug-state fill from the result payload (covers cases
        // where the progress relay dropped a frame).
        if (r.debug) {
          setDebug((d) => ({
            ...d,
            visible: true,
            transcript: r.debug?.transcript ?? d.transcript ?? r.transcript,
            screenshotB64: r.debug?.screenshot_b64 ?? d.screenshotB64,
            origSize: r.debug?.orig_size ?? d.origSize,
            newSize: r.debug?.new_size ?? d.newSize,
            origBytes: r.debug?.orig_bytes ?? d.origBytes,
            newBytes: r.debug?.new_bytes ?? d.newBytes,
            caption: r.debug?.caption ?? d.caption,
            groundingRaw: r.debug?.grounding_raw ?? d.groundingRaw,
            steps: r.debug?.steps ?? d.steps ?? r.steps,
            captionMs: r.debug?.timings?.caption_ms ?? d.captionMs,
            groundingMs: r.debug?.timings?.grounding_ms ?? d.groundingMs,
            sttMs: r.debug?.timings?.stt_ms ?? d.sttMs,
            downscaleMs: r.debug?.timings?.downscale_ms ?? d.downscaleMs,
            ttsMs: r.debug?.timings?.tts_ms ?? d.ttsMs,
            captionRunning: false,
            groundingRunning: false,
            ttsRunning: false,
          }));
          scheduleDebugDismiss();
        }
        scheduleAutoDismiss();
        if (r.audio_b64) {
          try {
            await playAudioB64(r.audio_b64);
          } catch (e) {
            console.warn("audio playback failed:", e);
          }
        }
      }),
    );

    // Per-stage progress (debug panel only).
    subs.push(
      onPipelineProgress((p: PipelineProgress) => {
        if (!debugEnabled) return;
        setDebug((d) => {
          const next: DebugState = { ...d, visible: true };
          switch (p.event) {
            case "stt_done":
              next.transcript = p.payload.transcript;
              next.sttMs = p.payload.elapsed_ms;
              break;
            case "image_downscaled":
              next.screenshotB64 = p.payload.image_b64;
              next.origSize = p.payload.orig_size;
              next.newSize = p.payload.new_size;
              next.origBytes = p.payload.orig_bytes;
              next.newBytes = p.payload.new_bytes;
              next.downscaleMs = p.payload.elapsed_ms;
              break;
            case "caption_start":
              next.captionRunning = true;
              break;
            case "caption_done":
              next.captionRunning = false;
              next.caption = p.payload.caption;
              next.captionMs = p.payload.elapsed_ms;
              break;
            case "grounding_start":
              next.groundingRunning = true;
              break;
            case "grounding_done":
              next.groundingRunning = false;
              next.steps = p.payload.steps;
              next.groundingRaw = p.payload.raw;
              next.groundingMs = p.payload.elapsed_ms;
              break;
            case "tts_start":
              next.ttsRunning = true;
              break;
            case "tts_done":
              next.ttsRunning = false;
              next.ttsMs = p.payload.elapsed_ms;
              break;
            case "error":
              next.error = p.payload.error;
              break;
          }
          return next;
        });
      }),
    );

    return () => {
      subs.forEach((p) => p.then((unsub) => unsub()));
      if (dismissTimer.current) clearTimeout(dismissTimer.current);
      if (debugDismissTimer.current) clearTimeout(debugDismissTimer.current);
    };
  }, [debugEnabled, scheduleAutoDismiss, scheduleDebugDismiss]);

  const hasHud = hud.phase !== "idle";
  const hasGrounding = groundingSteps.length > 0;
  const hasDebug = debugEnabled && debug.visible;

  if (!hasHud && !hasGrounding && !hasDebug) {
    // Invisible when idle — don't render anything that could block events.
    return null;
  }

  return (
    <>
      {/* SVG target markers — the app points, the user clicks. */}
      {hasGrounding && (
        <Annotation
          steps={groundingSteps}
          onDone={() => setGroundingSteps([])}
        />
      )}

      {/* Debug panel — bottom-left, only in debug mode. */}
      {hasDebug && <DebugHud debug={debug} />}

      {/* HUD bubble */}
      {hasHud && (
        <div
          style={{
            position: "fixed",
            bottom: 48,
            left: "50%",
            transform: "translateX(-50%)",
            maxWidth: 560,
            minWidth: 240,
            pointerEvents: "none",
            fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
          }}
        >
          <HudBubble hud={hud} />
        </div>
      )}
    </>
  );
}

// ──────────────────────────────────────────────────────────────────────────────

function HudBubble({ hud }: { hud: HudState }) {
  const bg =
    hud.phase === "recording"
      ? "rgba(220, 40, 40, 0.88)"
      : hud.phase === "error"
        ? "rgba(200, 50, 20, 0.88)"
        : "rgba(20, 20, 28, 0.88)";

  return (
    <div
      style={{
        background: bg,
        borderRadius: 16,
        padding: "12px 18px",
        color: "#fff",
        fontSize: 14,
        lineHeight: 1.5,
        backdropFilter: "blur(12px)",
        boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        transition: "background 0.2s",
      }}
    >
      {hud.phase === "recording" && <RecordingRow />}
      {hud.phase === "processing" && <ProcessingRow />}
      {hud.phase === "result" && (
        <ResultRows transcript={hud.transcript} answer={hud.answer} />
      )}
      {hud.phase === "error" && (
        <span style={{ opacity: 0.9 }}>⚠ {hud.errorMsg}</span>
      )}
    </div>
  );
}

function RecordingRow() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <PulseDot color="#ff6060" />
      <span style={{ fontWeight: 500 }}>Recording…</span>
    </div>
  );
}

function ProcessingRow() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <SpinnerDot />
      <span style={{ fontWeight: 500 }}>Processing…</span>
    </div>
  );
}

function ResultRows({
  transcript,
  answer,
}: {
  transcript?: string;
  answer?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {transcript && (
        <div style={{ opacity: 0.7, fontSize: 12 }}>
          <span style={{ marginRight: 4 }}>🎤</span>
          {transcript}
        </div>
      )}
      {answer && (
        <div style={{ fontWeight: 500 }}>
          <span style={{ marginRight: 4 }}>💬</span>
          {answer}
        </div>
      )}
    </div>
  );
}

function PulseDot({ color }: { color: string }) {
  return (
    <span
      style={{
        width: 10,
        height: 10,
        borderRadius: "50%",
        background: color,
        display: "inline-block",
        animation: "pulse 1s ease-in-out infinite",
      }}
    />
  );
}

function SpinnerDot() {
  return (
    <span
      style={{
        width: 10,
        height: 10,
        borderRadius: "50%",
        border: "2px solid rgba(255,255,255,0.3)",
        borderTopColor: "#fff",
        display: "inline-block",
        animation: "spin 0.7s linear infinite",
      }}
    />
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Debug HUD — bottom-left per-stage state tracker.

function DebugHud({ debug }: { debug: DebugState }) {
  return (
    <div
      style={{
        position: "fixed",
        left: 16,
        bottom: 16,
        width: 360,
        maxHeight: "80vh",
        overflow: "auto",
        background: "rgba(8, 12, 20, 0.92)",
        borderRadius: 14,
        padding: "14px 16px",
        color: "#e4e7ee",
        fontSize: 12,
        lineHeight: 1.45,
        fontFamily:
          "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
        backdropFilter: "blur(14px)",
        boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
        border: "1px solid rgba(255,255,255,0.08)",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 10,
          fontSize: 11,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          color: "#7fc9ff",
        }}
      >
        <span>debug</span>
        <span style={{ opacity: 0.4 }}>|</span>
        <span style={{ opacity: 0.75 }}>pipeline trace</span>
      </div>

      {/* 1. Transcript — the "caption of the question" */}
      <DebugRow
        label="1 · question"
        ms={debug.sttMs}
        running={!debug.transcript && !debug.error}
      >
        {debug.transcript ? (
          <span style={{ color: "#fff" }}>{debug.transcript}</span>
        ) : (
          <span style={{ opacity: 0.5 }}>listening…</span>
        )}
      </DebugRow>

      {/* 2. Downscaled screenshot */}
      <DebugRow
        label="2 · screenshot"
        ms={debug.downscaleMs}
        running={!debug.screenshotB64 && !!debug.transcript && !debug.error}
      >
        {debug.screenshotB64 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <img
              src={`data:image/png;base64,${debug.screenshotB64}`}
              alt="downscaled screenshot"
              style={{
                width: "100%",
                maxHeight: 150,
                objectFit: "contain",
                borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.1)",
                background: "#000",
              }}
            />
            <span style={{ opacity: 0.7, fontSize: 10 }}>
              {debug.origSize && debug.newSize
                ? `${debug.origSize[0]}×${debug.origSize[1]} → ${debug.newSize[0]}×${debug.newSize[1]}`
                : ""}
              {debug.origBytes && debug.newBytes
                ? `  ·  ${formatBytes(debug.origBytes)} → ${formatBytes(debug.newBytes)}`
                : ""}
            </span>
          </div>
        ) : (
          <span style={{ opacity: 0.5 }}>capturing…</span>
        )}
      </DebugRow>

      {/* 3. VLM caption — "what the image-to-text sees" */}
      <DebugRow label="3 · VLM sees" ms={debug.captionMs} running={debug.captionRunning}>
        {debug.caption ? (
          <span style={{ color: "#ffe8a8" }}>{debug.caption}</span>
        ) : debug.captionRunning ? (
          <span style={{ opacity: 0.5 }}>looking at the screen…</span>
        ) : (
          <span style={{ opacity: 0.4 }}>(waiting)</span>
        )}
      </DebugRow>

      {/* 4. Grounding decision */}
      <DebugRow
        label="4 · decision"
        ms={debug.groundingMs}
        running={debug.groundingRunning}
      >
        {debug.steps && debug.steps.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {debug.steps.map((s, i) => (
              <div key={i}>
                <span style={{ color: "#9cf" }}>
                  #{i + 1} ({s.x.toFixed(3)}, {s.y.toFixed(3)})
                </span>{" "}
                <span style={{ color: "#fff" }}>{s.explanation}</span>
              </div>
            ))}
          </div>
        ) : debug.groundingRunning ? (
          <span style={{ opacity: 0.5 }}>thinking…</span>
        ) : (
          <span style={{ opacity: 0.4 }}>(waiting)</span>
        )}
        {debug.groundingRaw && (
          <details style={{ marginTop: 6 }}>
            <summary style={{ opacity: 0.55, cursor: "pointer" }}>raw VLM output</summary>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                marginTop: 4,
                fontSize: 10,
                opacity: 0.75,
              }}
            >
              {debug.groundingRaw}
            </pre>
          </details>
        )}
      </DebugRow>

      {/* 5. TTS */}
      <DebugRow label="5 · speak" ms={debug.ttsMs} running={debug.ttsRunning}>
        {debug.ttsMs != null ? (
          <span style={{ opacity: 0.75 }}>audio played</span>
        ) : debug.ttsRunning ? (
          <span style={{ opacity: 0.5 }}>synthesising…</span>
        ) : (
          <span style={{ opacity: 0.4 }}>(waiting)</span>
        )}
      </DebugRow>

      {debug.error && (
        <div
          style={{
            marginTop: 6,
            padding: "6px 8px",
            borderRadius: 6,
            background: "rgba(220, 60, 60, 0.18)",
            color: "#ffb2b2",
          }}
        >
          ⚠ {debug.error}
        </div>
      )}
    </div>
  );
}

function DebugRow({
  label,
  ms,
  running,
  children,
}: {
  label: string;
  ms?: number;
  running?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        marginBottom: 10,
        paddingBottom: 8,
        borderBottom: "1px dashed rgba(255,255,255,0.06)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          opacity: 0.62,
          fontSize: 10,
          letterSpacing: 0.4,
          textTransform: "uppercase",
        }}
      >
        <span>{label}</span>
        <span>
          {running ? (
            <SpinnerDot />
          ) : ms != null ? (
            <span style={{ color: "#8fe38f" }}>{ms} ms</span>
          ) : null}
        </span>
      </div>
      <div>{children}</div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
