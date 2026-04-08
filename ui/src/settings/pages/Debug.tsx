import { type Settings } from "@/lib/api";

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

/**
 * Debug-mode toggle page.
 *
 * When enabled, the overlay renders an extra bottom-left panel during every
 * push-to-talk cycle showing the transcript, the downscaled screenshot, what
 * the VLM describes on screen, the grounding decision, per-stage timings,
 * and any error messages.
 */
export function DebugPage({ settings, onChange }: Props) {
  function toggle(v: boolean) {
    onChange({ ...settings, debug: { ...settings.debug, enabled: v } });
  }

  return (
    <div className="page">
      <h2>Debug Mode</h2>
      <p className="page__desc">
        Turn on a per-stage trace of every push-to-talk cycle. A panel appears
        in the bottom-left of the screen while the pipeline is running and
        shows, in order:
      </p>
      <ol style={{ marginLeft: 16, lineHeight: 1.7, opacity: 0.85 }}>
        <li>The transcribed question (STT output)</li>
        <li>The downscaled screenshot that was sent to the vision model</li>
        <li>A short description of what the vision model saw</li>
        <li>The final click coordinates + raw model output</li>
        <li>The spoken response stage</li>
      </ol>
      <p className="page__desc" style={{ marginTop: 12 }}>
        Each row shows how long that stage took in milliseconds, so you can
        pinpoint the slowest part of the pipeline.
      </p>

      <section className="provider-section">
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
            checked={settings.debug.enabled}
            onChange={(e) => toggle(e.target.checked)}
          />
          <span style={{ fontWeight: 500 }}>
            Enable debug overlay {settings.debug.enabled ? "(on)" : "(off)"}
          </span>
        </label>
        <p style={{ marginTop: 8, fontSize: 12, opacity: 0.7 }}>
          Debug mode adds one extra "describe the screen" call to the vision
          model on every recording, so it will make each round-trip a bit
          slower. Turn it off once you're happy with the setup.
        </p>
      </section>
    </div>
  );
}
