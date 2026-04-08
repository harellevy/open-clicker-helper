/**
 * Annotation — SVG cursor-path animation for visual grounding results.
 *
 * Renders a full-screen SVG overlay (pointer-events: none) that animates
 * a cursor from the current mouse position to each grounding step in
 * sequence, following a bezier path with spring-physics easing.
 *
 * Coordinate convention
 * ─────────────────────
 * Grounding steps arrive with normalised coords (0–1).  We multiply by
 * window.innerWidth / window.innerHeight to get CSS px.
 */

import { useEffect, useRef, useState } from "react";
import {
  AnimationHandle,
  Point,
  animate,
  bezierPath,
  easeInOut,
  lerp,
} from "./animator";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface GroundingStep {
  x: number; // normalised 0–1
  y: number;
  explanation: string;
}

interface AnnotationProps {
  steps: GroundingStep[];
  /** Called when all step animations have finished. */
  onDone?: () => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function Annotation({ steps, onDone }: AnnotationProps) {
  const [cursorPos, setCursorPos] = useState<Point>({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
  const [pathD, setPathD] = useState<string>("");
  const [pathProgress, setPathProgress] = useState(0); // 0–1 for stroke-dashoffset
  const [targetDot, setTargetDot] = useState<Point | null>(null);
  const [label, setLabel] = useState<string>("");
  const animRef = useRef<AnimationHandle | null>(null);
  const stepsRef = useRef<GroundingStep[]>(steps);
  const cursorRef = useRef<Point>(cursorPos);

  // Keep cursorRef in sync for use inside animation callbacks
  useEffect(() => {
    cursorRef.current = cursorPos;
  }, [cursorPos]);

  useEffect(() => {
    stepsRef.current = steps;
    if (steps.length === 0) return;
    animRef.current?.cancel();
    runSteps(steps, 0);
    return () => { animRef.current?.cancel(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps]);

  function runSteps(stepList: GroundingStep[], index: number) {
    if (index >= stepList.length) {
      // All done — linger 1 s then notify parent
      setTimeout(() => {
        setPathD("");
        setTargetDot(null);
        setLabel("");
        onDone?.();
      }, 1000);
      return;
    }

    const step = stepList[index];
    const from = { ...cursorRef.current };
    const to: Point = {
      x: step.x * window.innerWidth,
      y: step.y * window.innerHeight,
    };

    const path = bezierPath(from, to);
    setPathD(path);
    setPathProgress(0);
    setTargetDot(to);
    setLabel(step.explanation);

    const DURATION_MS = 900;

    animRef.current = animate(
      DURATION_MS,
      (t) => {
        const eased = easeInOut(t);
        const pos: Point = { x: lerp(from.x, to.x, eased), y: lerp(from.y, to.y, eased) };
        setCursorPos(pos);
        cursorRef.current = pos;
        setPathProgress(eased);
      },
      () => {
        // Pause 300 ms between steps then continue
        setTimeout(() => runSteps(stepList, index + 1), 300);
      },
    );
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
          style={{ transition: "none" }}
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
            style={{ animation: "pulse-ring 1s ease-in-out infinite" }}
          />
          <circle
            cx={targetDot.x}
            cy={targetDot.y}
            r={8}
            fill="rgba(100, 180, 255, 0.85)"
          />
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
    </svg>
  );
}
