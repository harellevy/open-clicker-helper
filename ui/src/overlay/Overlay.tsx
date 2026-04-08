// Transparent always-on-top overlay window.
//
// P3: Shows a HUD bubble (recording indicator, transcript, answer) driven by
// hotkey-state and pipeline-result Tauri events.
// P4: Adds the SVG Annotation layer for grounding results.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GroundingStep,
  HotkeyState,
  PipelineResult,
  onHotkeyState,
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

export function Overlay() {
  const [hud, setHud] = useState<HudState>(IDLE);
  const [groundingSteps, setGroundingSteps] = useState<GroundingStep[]>([]);
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleAutoDismiss = useCallback(() => {
    if (dismissTimer.current) clearTimeout(dismissTimer.current);
    dismissTimer.current = setTimeout(() => setHud(IDLE), 8000);
  }, []);

  useEffect(() => {
    const subs: Promise<() => void>[] = [];

    subs.push(
      onHotkeyState((s: HotkeyState) => {
        if (s.state === "recording") setHud({ phase: "recording" });
        else if (s.state === "processing") setHud({ phase: "processing" });
        else if (s.state === "error") {
          setHud({ phase: "error", errorMsg: s.message });
          scheduleAutoDismiss();
        } else if (s.state === "idle") {
          // Only auto-dismiss to idle if we're not showing a result.
          setHud((prev) => (prev.phase === "result" ? prev : IDLE));
        }
      }),
    );

    subs.push(
      onPipelineResult(async (r: PipelineResult) => {
        setHud({ phase: "result", transcript: r.transcript, answer: r.answer });
        // Show grounding annotation if steps were returned
        if (r.steps && r.steps.length > 0) {
          setGroundingSteps(r.steps);
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

    return () => {
      subs.forEach((p) => p.then((unsub) => unsub()));
      if (dismissTimer.current) clearTimeout(dismissTimer.current);
    };
  }, [scheduleAutoDismiss]);

  if (hud.phase === "idle" && groundingSteps.length === 0) {
    // Invisible when idle — don't render anything that could block events.
    return null;
  }

  return (
    <>
      {/* SVG cursor-path annotation (P4: grounding results) */}
      {groundingSteps.length > 0 && (
        <Annotation
          steps={groundingSteps}
          onDone={() => setGroundingSteps([])}
        />
      )}

      {/* HUD bubble */}
      {hud.phase !== "idle" && (
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
