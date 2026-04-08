/**
 * Annotation — shows the VLM's target location(s) on screen.
 *
 * Design note: earlier versions synthesized an actual mouse click via
 * `click_at_normalized`. That's been removed — the app now only *points at*
 * where to click, and the user does the clicking themselves. Rationale:
 *
 *   1. Safety / consent — nobody wants an AI moving their mouse unprompted.
 *   2. VLM coordinates are imperfect; handing the final decision back to the
 *      user avoids confidently-wrong clicks on the wrong element.
 *   3. Removes the iterative re-grounding loop (we can't see whether the user
 *      actually clicked what we suggested, so there's nothing to chain off).
 *
 * Visual behaviour
 * ─────────────────
 *   • A brief cursor-trail animation fades in pointing at the first step.
 *   • Every step is rendered as a numbered target ring with its explanation,
 *     so multi-step plans stay visible as a numbered checklist.
 *   • The overlay auto-dismisses after `LINGER_MS` so nothing gets stuck.
 *
 * Coordinates are normalised (0–1) and multiplied by window.innerWidth /
 * innerHeight at render time.
 */

import { useEffect, useRef, useState } from "react";
import { GroundingStep } from "../lib/api";
import {
  AnimationHandle,
  Point,
  animate,
  bezierPath,
  easeInOut,
  lerp,
} from "./animator";

// ── Config ────────────────────────────────────────────────────────────────────

const ANIMATION_MS = 900;    // cursor travel time
const LINGER_MS = 6000;      // how long the target markers stay visible

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AnnotationProps {
  steps: GroundingStep[];
  /** Called when the overlay auto-dismisses. */
  onDone?: () => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Annotation({ steps, onDone }: AnnotationProps) {
  const [cursorPos, setCursorPos] = useState<Point>({
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
  });
  const [pathD, setPathD] = useState("");
  const [pathProgress, setPathProgress] = useState(0);

  const animRef = useRef<AnimationHandle | null>(null);
  const cursorRef = useRef<Point>(cursorPos);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cursorRef.current = cursorPos;
  }, [cursorPos]);

  useEffect(() => {
    cancelledRef.current = false;
    if (steps.length === 0) return;

    // Animate the cursor to the first target, then linger so the user can
    // read the plan and click for themselves.
    const first = steps[0];
    animateTo(first).then(() => {
      if (cancelledRef.current) return;
      const t = setTimeout(() => {
        if (!cancelledRef.current) onDone?.();
      }, LINGER_MS);
      return () => clearTimeout(t);
    });

    return () => {
      cancelledRef.current = true;
      animRef.current?.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps]);

  function animateTo(step: GroundingStep): Promise<void> {
    return new Promise((resolve) => {
      const from = { ...cursorRef.current };
      const to: Point = {
        x: step.x * window.innerWidth,
        y: step.y * window.innerHeight,
      };

      setPathD(bezierPath(from, to));
      setPathProgress(0);

      animRef.current = animate(
        ANIMATION_MS,
        (t) => {
          const eased = easeInOut(t);
          const pos: Point = {
            x: lerp(from.x, to.x, eased),
            y: lerp(from.y, to.y, eased),
          };
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
      {/* Bezier trail to the first step */}
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

      {/* Numbered target rings for every step */}
      {steps.map((step, i) => {
        const cx = step.x * svgW;
        const cy = step.y * svgH;
        const primary = i === 0;
        return (
          <g key={i}>
            <circle
              cx={cx}
              cy={cy}
              r={primary ? 36 : 26}
              fill="none"
              stroke={
                primary
                  ? "rgba(120, 210, 255, 0.85)"
                  : "rgba(120, 210, 255, 0.5)"
              }
              strokeWidth={primary ? 3 : 2}
            />
            <circle
              cx={cx}
              cy={cy}
              r={primary ? 10 : 7}
              fill={
                primary
                  ? "rgba(120, 200, 255, 0.95)"
                  : "rgba(120, 200, 255, 0.65)"
              }
            />
            {steps.length > 1 && (
              <text
                x={cx}
                y={cy + 4}
                textAnchor="middle"
                fill="#0a0f1a"
                fontSize={12}
                fontWeight={700}
                fontFamily="-apple-system, BlinkMacSystemFont, sans-serif"
              >
                {i + 1}
              </text>
            )}
            <text
              x={cx}
              y={cy - (primary ? 48 : 36)}
              textAnchor="middle"
              fill="white"
              fontSize={13}
              fontFamily="-apple-system, BlinkMacSystemFont, sans-serif"
              stroke="rgba(0,0,0,0.65)"
              strokeWidth={3}
              paintOrder="stroke"
            >
              {steps.length > 1 ? `${i + 1}. ${step.explanation}` : step.explanation}
            </text>
          </g>
        );
      })}

      {/* Cursor dot (anim target) */}
      <circle
        cx={cursorPos.x}
        cy={cursorPos.y}
        r={7}
        fill="rgba(255, 255, 255, 0.95)"
        stroke="rgba(80, 160, 240, 0.9)"
        strokeWidth={2.5}
      />
    </svg>
  );
}
