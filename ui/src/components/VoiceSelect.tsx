import { useEffect, useRef, useState } from "react";

export interface VoiceOption {
  /** Stored value written back to the settings store. */
  value: string;
  /** Visible label shown inside the dropdown row. */
  label: string;
}

interface Props {
  value: string;
  options: VoiceOption[];
  onChange: (value: string) => void;
  /** Optional aria-label for the trigger button. */
  ariaLabel?: string;
}

/**
 * Custom voice dropdown: same visual feel as the native `<select>` used
 * elsewhere in the settings UI, plus a speaker icon on the left of every
 * row. Clicking a row selects the voice and closes the panel; clicking the
 * speaker icon previews the text via `speechSynthesis` using the macOS
 * `Carmit` voice without closing the panel.
 *
 * We use a custom popup (not a native `<select>`) because HTML `<option>`
 * elements can't contain interactive children.
 */
export function VoiceSelect({ value, options, onChange, ariaLabel }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close the panel on any click outside the component.
  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = options.find((o) => o.value === value) ?? options[0];

  return (
    <div className="voice-select" ref={rootRef}>
      <button
        type="button"
        className="input voice-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel ?? "Voice"}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="voice-select__trigger-label">
          {current?.label ?? value}
        </span>
        <span className="voice-select__chevron" aria-hidden>▾</span>
      </button>

      {open && (
        <ul className="voice-select__menu" role="listbox">
          {options.map((o) => {
            const selected = o.value === value;
            return (
              <li
                key={o.value}
                className={`voice-select__option ${selected ? "voice-select__option--active" : ""}`}
                role="option"
                aria-selected={selected}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                <button
                  type="button"
                  className="voice-select__speaker"
                  aria-label={`Preview ${o.label}`}
                  title="Preview"
                  onClick={(e) => {
                    // Preview must NOT select the voice or close the menu.
                    e.stopPropagation();
                    speakWithCarmit(o.value);
                  }}
                >
                  <SpeakerIcon />
                </button>
                <span className="voice-select__label">{o.label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function SpeakerIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M8 2 L4 5 H1.5 V11 H4 L8 14 Z" />
      <path d="M11 5.5 Q13 8 11 10.5" />
      <path d="M12.5 3.5 Q15.5 8 12.5 12.5" />
    </svg>
  );
}

/**
 * Preview the given text with the macOS `Carmit` voice. Silently no-ops if
 * the Web Speech API is unavailable or the voice catalogue hasn't loaded
 * yet — voices are fetched lazily on first use and cached.
 */
function speakWithCarmit(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const synth = window.speechSynthesis;

  const speakNow = () => {
    // Cancel any in-flight preview so rapid clicks feel responsive.
    synth.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    const carmit = synth.getVoices().find((v) => v.name === "Carmit");
    if (carmit) {
      utter.voice = carmit;
      utter.lang = carmit.lang;
    }
    synth.speak(utter);
  };

  // Chrome/Safari sometimes return [] on first call; wait for voiceschanged.
  if (synth.getVoices().length === 0) {
    const onVoices = () => {
      synth.removeEventListener("voiceschanged", onVoices);
      speakNow();
    };
    synth.addEventListener("voiceschanged", onVoices);
    // Trigger the list to populate.
    synth.getVoices();
    return;
  }
  speakNow();
}
