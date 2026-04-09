import { useState } from "react";
import { type ProviderTestResult, type Settings, api } from "@/lib/api";
import { VoiceSelect } from "@/components/VoiceSelect";

const KOKORO_VOICES = [
  { value: "af_heart", label: "af_heart (English, female)" },
  { value: "am_adam", label: "am_adam (English, male)" },
  { value: "bf_emma", label: "bf_emma (British English, female)" },
  { value: "bm_lewis", label: "bm_lewis (British English, male)" },
];

const OPENAI_VOICES = ["alloy", "echo", "fable", "nova", "onyx", "shimmer"].map(
  (v) => ({ value: v, label: v }),
);

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
}

type TestState = "idle" | "testing" | "ok" | "error";
interface TestResult {
  state: TestState;
  message: string;
}

const idle = (): TestResult => ({ state: "idle", message: "" });

export function ProvidersPage({ settings, onChange }: Props) {
  const [sttTest, setSttTest] = useState<TestResult>(idle());
  const [vlmTest, setVlmTest] = useState<TestResult>(idle());
  const [ttsTest, setTtsTest] = useState<TestResult>(idle());

  async function test(
    type: "stt" | "vlm" | "tts",
    provider: string,
    config: object,
    setTest: (r: TestResult) => void,
  ) {
    setTest({ state: "testing", message: "Testing…" });
    try {
      const result: ProviderTestResult = await api.testProvider(type, provider, config);
      setTest({
        state: result.ok ? "ok" : "error",
        message: result.ok
          ? `Connected (${result.latency_ms ?? "?"}ms)`
          : (result.error ?? "failed"),
      });
    } catch (e) {
      setTest({ state: "error", message: String(e) });
    }
  }

  function save(updated: Settings) {
    onChange(updated);
    api.saveSettings(updated).catch(console.error);
  }

  return (
    <div className="page">
      <h2>Providers</h2>
      <p className="page__desc">
        Configure which AI provider to use for each capability. Click{" "}
        <strong>Test</strong> to verify connectivity before saving.
      </p>

      {/* STT */}
      <section className="provider-section">
        <h3>Speech-to-Text</h3>
        <ProviderToggle
          options={[
            { id: "mlx-whisper", label: "mlx-whisper (offline)" },
            { id: "openai", label: "OpenAI Whisper" },
          ]}
          value={settings.stt.provider}
          onChange={(v) =>
            save({ ...settings, stt: { ...settings.stt, provider: v as "mlx-whisper" | "openai" } })
          }
        />
        {settings.stt.provider === "mlx-whisper" ? (
          <SettingRow label="Model">
            <input
              className="input"
              value={settings.stt.mlx_model}
              onChange={(e) =>
                save({ ...settings, stt: { ...settings.stt, mlx_model: e.target.value } })
              }
            />
          </SettingRow>
        ) : (
          <SettingRow label="API key">
            <input
              className="input"
              type="password"
              placeholder="sk-…"
              value={settings.stt.openai_key ?? ""}
              onChange={(e) =>
                save({ ...settings, stt: { ...settings.stt, openai_key: e.target.value || null } })
              }
            />
          </SettingRow>
        )}
        <TestButton
          result={sttTest}
          onTest={() =>
            test("stt", settings.stt.provider, {
              mlx_model: settings.stt.mlx_model,
              openai_key: settings.stt.openai_key,
            }, setSttTest)
          }
        />
      </section>

      {/* VLM */}
      <section className="provider-section">
        <h3>Vision LLM</h3>
        <ProviderToggle
          options={[
            { id: "ollama", label: "Ollama (offline)" },
            { id: "openai", label: "OpenAI GPT-4o" },
            { id: "anthropic", label: "Anthropic Claude" },
          ]}
          value={settings.vlm.provider}
          onChange={(v) =>
            save({ ...settings, vlm: { ...settings.vlm, provider: v as "ollama" | "openai" | "anthropic" } })
          }
        />
        {settings.vlm.provider === "ollama" && (
          <>
            <SettingRow label="Ollama URL">
              <input
                className="input"
                value={settings.vlm.ollama_url}
                onChange={(e) =>
                  save({ ...settings, vlm: { ...settings.vlm, ollama_url: e.target.value } })
                }
              />
            </SettingRow>
            <SettingRow label="Model">
              <input
                className="input"
                value={settings.vlm.ollama_model}
                onChange={(e) =>
                  save({ ...settings, vlm: { ...settings.vlm, ollama_model: e.target.value } })
                }
              />
            </SettingRow>
          </>
        )}
        {settings.vlm.provider === "openai" && (
          <>
            <SettingRow label="API key">
              <input
                className="input"
                type="password"
                placeholder="sk-…"
                value={settings.vlm.openai_key ?? ""}
                onChange={(e) =>
                  save({ ...settings, vlm: { ...settings.vlm, openai_key: e.target.value || null } })
                }
              />
            </SettingRow>
            <SettingRow label="Model">
              <input
                className="input"
                value={settings.vlm.openai_model}
                onChange={(e) =>
                  save({ ...settings, vlm: { ...settings.vlm, openai_model: e.target.value } })
                }
              />
            </SettingRow>
          </>
        )}
        {settings.vlm.provider === "anthropic" && (
          <SettingRow label="API key">
            <input
              className="input"
              type="password"
              placeholder="sk-ant-…"
              value={settings.vlm.anthropic_key ?? ""}
              onChange={(e) =>
                save({ ...settings, vlm: { ...settings.vlm, anthropic_key: e.target.value || null } })
              }
            />
          </SettingRow>
        )}
        <TestButton
          result={vlmTest}
          onTest={() =>
            test("vlm", settings.vlm.provider, {
              ollama_url: settings.vlm.ollama_url,
              openai_key: settings.vlm.openai_key,
              anthropic_key: settings.vlm.anthropic_key,
            }, setVlmTest)
          }
        />
      </section>

      {/* TTS */}
      <section className="provider-section">
        <h3>Text-to-Speech</h3>
        <ProviderToggle
          options={[
            { id: "kokoro", label: "Kokoro (offline)" },
            { id: "openai", label: "OpenAI TTS" },
          ]}
          value={settings.tts.provider}
          onChange={(v) =>
            save({ ...settings, tts: { ...settings.tts, provider: v as "kokoro" | "openai" } })
          }
        />
        {settings.tts.provider === "kokoro" ? (
          <SettingRow label="Voice">
            <VoiceSelect
              value={settings.tts.kokoro_voice}
              options={KOKORO_VOICES}
              ariaLabel="Kokoro voice"
              onChange={(v) =>
                save({ ...settings, tts: { ...settings.tts, kokoro_voice: v } })
              }
            />
          </SettingRow>
        ) : (
          <>
            <SettingRow label="API key">
              <input
                className="input"
                type="password"
                placeholder="sk-…"
                value={settings.tts.openai_key ?? ""}
                onChange={(e) =>
                  save({ ...settings, tts: { ...settings.tts, openai_key: e.target.value || null } })
                }
              />
            </SettingRow>
            <SettingRow label="Voice">
              <VoiceSelect
                value={settings.tts.openai_voice}
                options={OPENAI_VOICES}
                ariaLabel="OpenAI voice"
                onChange={(v) =>
                  save({ ...settings, tts: { ...settings.tts, openai_voice: v } })
                }
              />
            </SettingRow>
          </>
        )}
        <TestButton
          result={ttsTest}
          onTest={() =>
            test("tts", settings.tts.provider, {
              kokoro_voice: settings.tts.kokoro_voice,
              openai_key: settings.tts.openai_key,
            }, setTtsTest)
          }
        />
      </section>
    </div>
  );
}

function ProviderToggle({
  options,
  value,
  onChange,
}: {
  options: { id: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="provider-toggle">
      {options.map((o) => (
        <button
          key={o.id}
          className={`provider-toggle__btn ${value === o.id ? "provider-toggle__btn--active" : ""}`}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function SettingRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="setup-row">
      <label>{label}</label>
      {children}
    </div>
  );
}

function TestButton({ result, onTest }: { result: TestResult; onTest: () => void }) {
  return (
    <div className="test-row">
      <button
        className="btn btn--ghost btn--sm"
        onClick={onTest}
        disabled={result.state === "testing"}
      >
        {result.state === "testing" ? "Testing…" : "Test connection"}
      </button>
      {result.state !== "idle" && result.state !== "testing" && (
        <span className={`test-result test-result--${result.state}`}>
          {result.message}
        </span>
      )}
    </div>
  );
}
