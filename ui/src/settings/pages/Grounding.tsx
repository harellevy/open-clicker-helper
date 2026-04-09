import { type GroundingMode, type Settings } from "@/lib/api";

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

/**
 * Grounding-pipeline settings: strategy selector + refinement toggle.
 *
 * The strategy selector controls how the app decides *where* to click for a
 * given question:
 *
 *   - Auto (default): try the macOS Accessibility tree first, fall back to
 *     the vision model if no on-screen label matches the question.
 *   - Accessibility only: native AX tree only — skip the vision model. Fast
 *     and offline, but returns no-op for questions about elements the app
 *     doesn't expose in its AX tree (most web content, canvases, games).
 *   - Vision only: skip AX entirely and always ask the VLM. Mirrors the
 *     pre-P4.2 behaviour and is useful for debugging the vision pipeline.
 */
export function GroundingPage({ settings, onChange }: Props) {
  function setMode(mode: GroundingMode) {
    onChange({ ...settings, grounding: { ...settings.grounding, mode } });
  }

  function setRefine(refine: boolean) {
    onChange({ ...settings, grounding: { ...settings.grounding, refine } });
  }

  return (
    <div className="page">
      <h2>Grounding</h2>
      <p className="page__desc">
        How the app decides where to click when you ask a question about the
        screen.
      </p>

      <section className="provider-section">
        <h3>Strategy</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <ModeRadio
            value="auto"
            current={settings.grounding.mode}
            label="Auto (recommended)"
            description="Try the macOS Accessibility tree first for a sub-millisecond hit. If nothing matches, fall back to the vision model."
            onChange={setMode}
          />
          <ModeRadio
            value="ax"
            current={settings.grounding.mode}
            label="Accessibility only"
            description="Use only the native AX tree. Fastest and fully offline, but can't see inside web views, canvases, or games."
            onChange={setMode}
          />
          <ModeRadio
            value="vlm"
            current={settings.grounding.mode}
            label="Vision only"
            description="Always ask the vision model. Same as pre-4.2 behaviour — slower but works on any pixels on screen."
            onChange={setMode}
          />
        </div>
      </section>

      <section className="provider-section">
        <h3>Refinement</h3>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={settings.grounding.refine}
            onChange={(e) => setRefine(e.target.checked)}
          />
          <span style={{ fontWeight: 500 }}>
            Two-pass refinement {settings.grounding.refine ? "(on)" : "(off)"}
          </span>
        </label>
        <p style={{ marginTop: 8, fontSize: 12, opacity: 0.7 }}>
          When on, a second vision-model pass runs on a full-resolution crop
          around each rough target to tighten pixel accuracy. Adds about one
          extra VLM call per step. Only applies when the vision model is used
          (modes: Auto, Vision only).
        </p>
      </section>
    </div>
  );
}

function ModeRadio({
  value,
  current,
  label,
  description,
  onChange,
}: {
  value: GroundingMode;
  current: GroundingMode;
  label: string;
  description: string;
  onChange: (m: GroundingMode) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        cursor: "pointer",
        padding: 8,
        borderRadius: 6,
        background: current === value ? "rgba(80,120,220,0.08)" : "transparent",
      }}
    >
      <input
        type="radio"
        name="grounding-mode"
        checked={current === value}
        onChange={() => onChange(value)}
        style={{ marginTop: 3 }}
      />
      <div>
        <div style={{ fontWeight: 500 }}>{label}</div>
        <div style={{ marginTop: 2, fontSize: 12, opacity: 0.7 }}>
          {description}
        </div>
      </div>
    </label>
  );
}
