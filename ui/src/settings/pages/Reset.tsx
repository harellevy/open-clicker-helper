import { useState } from "react";
import { type Settings, api } from "@/lib/api";

interface Props {
  onReset: (s: Settings) => void;
}

export function ResetPage({ onReset }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doReset() {
    setBusy(true);
    setError(null);
    try {
      const defaults = await api.resetSettings();
      onReset(defaults);
      // setup_complete is now false → App re-renders the setup wizard automatically
    } catch (e) {
      setError(String(e));
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className="page">
      <h2>Reset</h2>
      <p className="page__desc">
        Clear all saved settings and restart the first-run setup wizard. Model
        weights already downloaded are kept — only the app configuration is
        erased.
      </p>

      <div className="reset-section">
        <h3>Factory reset</h3>
        <p>
          Resets provider selection, API keys, and hotkey to defaults. The
          setup wizard will appear again on next page load.
        </p>

        {!confirming ? (
          <button className="btn btn--danger" onClick={() => setConfirming(true)}>
            Reset to defaults
          </button>
        ) : (
          <div className="reset-confirm">
            <p className="reset-confirm__msg">
              Are you sure? All settings will be cleared.
            </p>
            <div className="reset-confirm__actions">
              <button
                className="btn btn--ghost"
                onClick={() => setConfirming(false)}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                className="btn btn--danger"
                onClick={doReset}
                disabled={busy}
              >
                {busy ? "Resetting…" : "Yes, reset"}
              </button>
            </div>
          </div>
        )}

        {error && <p className="setup-error">{error}</p>}
      </div>

      <div className="reset-section">
        <h3>Remove model weights</h3>
        <p>
          To force re-download of STT / TTS models, delete these directories
          from Terminal:
        </p>
        <pre className="reset-code">{`rm -rf ~/.cache/huggingface/hub/models--mlx-community--whisper-base-mlx
rm -rf ~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M`}</pre>
      </div>

      <div className="reset-section">
        <h3>Remove Python environment</h3>
        <p>Forces a full reinstall of sidecar dependencies on next launch:</p>
        <pre className="reset-code">rm -rf sidecar/.venv</pre>
      </div>
    </div>
  );
}
