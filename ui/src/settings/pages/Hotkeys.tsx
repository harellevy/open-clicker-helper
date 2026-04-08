import { useState } from "react";
import { type Settings, api } from "@/lib/api";
import { HotkeyRecorder } from "./Setup";

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

export function HotkeysPage({ settings, onChange }: Props) {
  const [saved, setSaved] = useState(false);

  async function save(hotkey: string) {
    const updated = { ...settings, hotkey };
    onChange(updated);
    try {
      await api.saveSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div className="page">
      <h2>Hotkey</h2>
      <p className="page__desc">
        Hold this key combination to start recording your question. Release to
        submit. The shortcut is registered globally so it works even when
        open-clicker-helper is in the background.
      </p>

      <div className="hotkey-section">
        <label className="hotkey-label">Activation hotkey</label>
        <HotkeyRecorder value={settings.hotkey} onChange={save} />
        {saved && <span className="hotkey-saved">Saved</span>}
      </div>

      <div className="hotkey-hints">
        <h4>Tips</h4>
        <ul>
          <li>
            Use at least one modifier key (<kbd>⌘</kbd>, <kbd>⌃</kbd>,{" "}
            <kbd>⌥</kbd>, <kbd>⇧</kbd>) to avoid conflicts.
          </li>
          <li>
            The default <kbd>⌘ ⇧ Space</kbd> is a good starting point.
          </li>
          <li>
            If the hotkey stops responding, try toggling accessibility
            permission off and on in System Settings.
          </li>
        </ul>
      </div>
    </div>
  );
}
