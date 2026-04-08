/**
 * Annotation — iterative SVG cursor animation with click synthesis (P4.1).
 *
 * For each grounding step:
 *   1. Animate cursor along a bezier path to the target.
 *   2. Synthesise a real left-click via `click_at_normalized` IPC.
 *   3. Wait `stepDelayMs` for the UI to settle.
 *   4. Capture a fresh screenshot and re-ground the *next* step against it.
 *   5. Repeat until no more steps or `maxSteps` reached.
 *
 * Coordinate convention
 * ─────────────────────
 * Steps arrive with normalised coords (0–1). CSS positions are derived by
 * multiplying by window.innerWidth / innerHeight. The Rust click_at_normalized
 * command independently converts to physical pixels using the primary monitor.
 */

import { useEffect, useRef, useState } from "react";
import {
  captureScreen,
  clickAtNormalized,
  groundingLocate,
  GroundingStep,
} from "../lib/api";
import {
  AnimationHandle,
  Point,
  animate,
  bezierPath,
  easeInOut,
  lerp,
} from "./animator";

// ── Config ────────────────────────────────────────────────────────────────────

const ANIMATION_MS = 900;   // cursor travel time per step
const STEP_DELAY_MS = 800;  // wait after click for UI to settle
const MAX_STEPS = 8;        // safety cap against infinite loops

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AnnotationProps {
  steps: GroundingStep[];
  /** The original transcribed question — used when re-grounding. */
  transcript: string;
  /** Called when all steps finish (or we give up). */
  onDone?: () => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Annotation({ steps, transcript, onDone }: AnnotationProps) {
  const [cursorPos, setCursorPos] = useState<Point>({
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
  });
  const [pathD, setPathD] = useState("");
  const [pathProgress, setPathProgress] = useState(0);
  const [targetDot, setTargetDot] = useState<Point | null>(null);
  const [label, setLabel] = useState("");
  const [status, setStatus] = useState(""); // "Clicking…" | "Re-grounding…" | ""

  const animRef = useRef<AnimationHandle | null>(null);
  const cursorRef = useRef<Point>(cursorPos);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cursorRef.current = cursorPos;
  }, [cursorPos]);

  useEffect(() => {
    cancelledRef.current = false;
    if (steps.length === 0) return;
    runIterative(steps);
    return () => {
      cancelledRef.current = true;
      animRef.current?.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps]);

  async function runIterative(initialSteps: GroundingStep[]) {
    let remaining = [...initialSteps];
    let totalClicks = 0;

    while (remaining.length > 0 && totalClicks < MAX_STEPS) {
      if (cancelledRef.current) return;

      const step = remaining[0];

      // ── 1. Animate cursor to target ─────────────────────────────────────
      await animateToStep(step);
      if (cancelledRef.current) return;

      // ── 2. Click ─────────────────────────────────────────────────────────
      setStatus("Clicking…");
      try {
        await clickAtNormalized(step.x, step.y);
      } catch (e) {
        console.warn("click failed:", e);
      }
      totalClicks++;

      // ── 3. Wait for UI to settle ─────────────────────────────────────────
      await sleep(STEP_DELAY_MS);
      if (cancelledRef.current) return;

      // ── 4. Re-ground remaining steps against fresh screenshot ────────────
      remaining = remaining.slice(1); // pop the step we just executed

      if (remaining.length === 0) break; // nothing more to do

      setStatus("Re-grounding…");
      try {
        const png = await captureScreen();
        if (png) {
          const result = await groundingLocate(png, transcript);
          if (result.steps.length > 0) {
            remaining = result.steps;
          }
          // If re-grounding returns no steps, we stop (task complete).
        }
      } catch (e) {
        console.warn("re-grounding failed, continuing with existing steps:", e);
      }
      setStatus("");
    }

    // All done — linger briefly then notify parent.
    await sleep(600);
    if (!cancelledRef.current) {
      setPathD("");
      setTargetDot(null);
      setLabel("");
      setStatus("");
      onDone?.();
    }
  }

  function animateToStep(step: GroundingStep): Promise<void> {
    return new Promise((resolve) => {
      const from = { ...cursorRef.current };
      const to: Point = {
        x: step.x * window.innerWidth,
        y: step.y * window.innerHeight,
      };

      setPathD(bezierPath(from, to));
      setPathProgress(0);
      setTargetDot(to);
      setLabel(step.explanation);

      animRef.current = animate(
        ANIMATION_MS,
        (t) => {
          const eased = easeInOut(t);
          const pos: Point = { x: lerp(from.x, to.x, eased), y: lerp(from.y, to.y, eased) };
          setCursorPos(pos);
          cursorRef.current = pos;
          setPathProgress(eased);
        },
        () => resolve(),
      );
    });
  }

  if (steps.length === 0) return null;

  const svgW = window.innerWidth;
  const svgH = window.innerHeight;

  return (
    <svg
      style={{
        position: "fixed",
        inset: 0,
        width: "100vw",
        height: "100vh",
        pointerEvents: "none",
        overflow: "visible",
        zIndex: 9999,
      }}
      viewBox={`0 0 ${svgW} ${svgH}`}
    >
      {/* Bezier trail */}
      {pathD && (
        <path
          d={pathD}
          fill="none"
          stroke="rgba(100, 180, 255, 0.7)"
          strokeWidth={3}
          strokeLinecap="round"
          strokeDasharray={2000}
          strokeDashoffset={(1 - pathProgress) * 2000}
        />
      )}

      {/* Target ring */}
      {targetDot && (
        <g>
          <circle
            cx={targetDot.x}
            cy={targetDot.y}
            r={28}
            fill="none"
            stroke="rgba(100, 200, 255, 0.5)"
            strokeWidth={2}
          />
          <circle cx={targetDot.x} cy={targetDot.y} r={8} fill="rgba(100, 180, 255, 0.85)" />
        </g>
      )}

      {/* Cursor dot */}
      <circle
        cx={cursorPos.x}
        cy={cursorPos.y}
        r={7}
        fill="rgba(255, 255, 255, 0.95)"
        stroke="rgba(80, 160, 240, 0.9)"
        strokeWidth={2.5}
      />

      {/* Step label */}
      {label && targetDot && (
        <text
          x={targetDot.x}
          y={targetDot.y - 40}
          textAnchor="middle"
          fill="white"
          fontSize={13}
          fontFamily="-apple-system, BlinkMacSystemFont, sans-serif"
          stroke="rgba(0,0,0,0.6)"
          strokeWidth={3}
          paintOrder="stroke"
        >
          {label}
        </text>
      )}

      {/* Status badge (Clicking… / Re-grounding…) */}
      {status && (
        <g transform={`translate(${svgW / 2}, ${svgH - 80})`}>
          <rect x={-70} y={-18} width={140} height={28} rx={8} fill="rgba(20,20,28,0.85)" />
          <text
            textAnchor="middle"
            dy={4}
            fill="rgba(180,210,255,0.95)"
            fontSize={12}
            fontFamily="-apple-system, BlinkMacSystemFont, sans-serif"
          >
            {status}
          </text>
        </g>
      )}
    </svg>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
