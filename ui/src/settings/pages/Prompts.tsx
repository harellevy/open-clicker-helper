import { type Settings } from "@/lib/api";

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

// Keep these strings in sync with the DEFAULT_*_SYSTEM_PROMPT constants in
// `sidecar/och_sidecar/grounding.py`. We show them as greyed-out placeholders
// so users know what they are replacing when they start typing.

const DEFAULT_GROUNDING_PROMPT = `You are a UI grounding assistant. Given a screenshot and a task description, output the screen coordinates where the user should click to complete the task.

IMPORTANT: Respond ONLY with a valid JSON object — no prose, no markdown fences.

Schema:
{
  "steps": [
    {
      "x": <float 0.0–1.0>,
      "y": <float 0.0–1.0>,
      "explanation": "<one short sentence>"
    }
  ]
}

Coordinates are normalised: (0, 0) is the top-left corner, (1, 1) is the bottom-right. For multi-step tasks include one entry per click in order.`;

const DEFAULT_CAPTION_PROMPT = `You are a UI observer. Describe what is visible on the user's screen in 1–3 short sentences so a downstream agent can decide what to click.

Focus on:
- the app and page/window in view
- the main interactive elements (buttons, inputs, menus) and their rough locations
- any dialog, modal, or notification currently on top

Respond with plain prose — no JSON, no lists, no code fences.`;

/**
 * System-prompt editor.
 *
 * Exposes the per-stage prompts used by the sidecar so power users can
 * customise how the VLM behaves without editing Python. An empty string
 * means "fall back to the sidecar's built-in default".
 */
export function PromptsPage({ settings, onChange }: Props) {
  function update(
    key: "grounding" | "caption",
    value: string,
  ) {
    onChange({
      ...settings,
      system_prompts: { ...settings.system_prompts, [key]: value },
    });
  }

  return (
    <div className="page">
      <h2>System Prompts</h2>
      <p className="page__desc">
        Customise the instructions the AI sees for each stage of the pipeline.
        Leave a field empty to use the built-in default.
      </p>

      <section className="provider-section">
        <h3>Grounding prompt</h3>
        <p style={{ opacity: 0.75, fontSize: 12, marginBottom: 8 }}>
          Sent to the vision model with every screenshot. The model must return
          JSON with normalised click coordinates.
        </p>
        <PromptEditor
          value={settings.system_prompts.grounding}
          placeholder={DEFAULT_GROUNDING_PROMPT}
          onChange={(v) => update("grounding", v)}
          onReset={() => update("grounding", "")}
        />
      </section>

      <section className="provider-section">
        <h3>Caption prompt (debug mode only)</h3>
        <p style={{ opacity: 0.75, fontSize: 12, marginBottom: 8 }}>
          Only used when <strong>Debug Mode</strong> is enabled. Asks the
          vision model to describe the screen in plain prose so the debug
          panel can show "what the model sees".
        </p>
        <PromptEditor
          value={settings.system_prompts.caption}
          placeholder={DEFAULT_CAPTION_PROMPT}
          onChange={(v) => update("caption", v)}
          onReset={() => update("caption", "")}
        />
      </section>
    </div>
  );
}

function PromptEditor({
  value,
  placeholder,
  onChange,
  onReset,
}: {
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
  onReset: () => void;
}) {
  const usingDefault = value.trim() === "";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <textarea
        className="input"
        style={{
          width: "100%",
          minHeight: 180,
          fontFamily:
            "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
          fontSize: 12,
          lineHeight: 1.5,
          padding: 10,
          whiteSpace: "pre-wrap",
        }}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 11, opacity: 0.65 }}>
          {usingDefault
            ? "Using built-in default"
            : `${value.length} characters (custom)`}
        </span>
        {!usingDefault && (
          <button className="btn btn--ghost btn--sm" onClick={onReset}>
            Reset to default
          </button>
        )}
      </div>
    </div>
  );
}
