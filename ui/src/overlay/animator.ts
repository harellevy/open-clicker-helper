/**
 * Spring-physics easing and SVG path helpers for cursor animation.
 *
 * All coordinates are in CSS viewport units (px or vw/vh %) unless noted.
 */

// ── Easing ────────────────────────────────────────────────────────────────────

/** Ease-in-out cubic — smooth start and end. */
export function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
}

/**
 * Simple under-damped spring evaluator.
 *
 * Returns the displacement ratio at time `t` (seconds) given:
 *  - `stiffness` k  (default 200)
 *  - `damping`   c  (default 20)
 *  - `mass`      m  (default 1)
 *
 * The result oscillates from 1 → 0 (displacement from target).
 * Clamp to [0, 1] before using as a lerp factor.
 */
export function spring(
  t: number,
  stiffness = 200,
  damping = 20,
  mass = 1,
): number {
  const omega0 = Math.sqrt(stiffness / mass);
  const zeta = damping / (2 * Math.sqrt(stiffness * mass));

  if (zeta < 1) {
    // Under-damped
    const omegaD = omega0 * Math.sqrt(1 - zeta * zeta);
    const envelope = Math.exp(-zeta * omega0 * t);
    return 1 - envelope * (Math.cos(omegaD * t) + (zeta / Math.sqrt(1 - zeta * zeta)) * Math.sin(omegaD * t));
  }

  if (zeta === 1) {
    // Critically damped
    const e = Math.exp(-omega0 * t);
    return 1 - e * (1 + omega0 * t);
  }

  // Over-damped
  const r1 = -omega0 * (zeta - Math.sqrt(zeta * zeta - 1));
  const r2 = -omega0 * (zeta + Math.sqrt(zeta * zeta - 1));
  const A = r2 / (r2 - r1);
  const B = -r1 / (r2 - r1);
  return 1 - A * Math.exp(r1 * t) - B * Math.exp(r2 * t);
}

/** Linear interpolation between a and b at ratio t (0–1). */
export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

// ── SVG path helpers ──────────────────────────────────────────────────────────

export interface Point {
  x: number;
  y: number;
}

/**
 * Build a cubic-bezier SVG path string from `from` to `to`.
 * The control points create a natural-looking arc.
 */
export function bezierPath(from: Point, to: Point): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;

  // Control points: pull slightly perpendicular to the direction of travel
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const perpX = -dy * 0.25;
  const perpY = dx * 0.25;

  const c1 = { x: from.x + dx * 0.3 + perpX, y: from.y + dy * 0.3 + perpY };
  const c2 = { x: to.x - dx * 0.3 + perpX, y: to.y - dy * 0.3 + perpY };

  void mx; void my; // suppress unused-var lint

  return (
    `M ${from.x} ${from.y} ` +
    `C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${to.x} ${to.y}`
  );
}

// ── Animation frame runner ────────────────────────────────────────────────────

export interface AnimationHandle {
  cancel: () => void;
}

/**
 * Run an animation for `durationMs` milliseconds.
 *
 * `onFrame(t)` is called on every animation frame with `t` in [0, 1].
 * Returns a handle with a `cancel()` method.
 */
export function animate(
  durationMs: number,
  onFrame: (t: number) => void,
  onDone?: () => void,
): AnimationHandle {
  let rafId: number;
  let startTime: number | null = null;
  let cancelled = false;

  const step = (now: number) => {
    if (cancelled) return;
    if (startTime === null) startTime = now;
    const elapsed = now - startTime;
    const t = Math.min(elapsed / durationMs, 1);
    onFrame(t);
    if (t < 1) {
      rafId = requestAnimationFrame(step);
    } else {
      onDone?.();
    }
  };

  rafId = requestAnimationFrame(step);
  return { cancel: () => { cancelled = true; cancelAnimationFrame(rafId); } };
}
