import { useEffect, useState } from "react";
import { type Settings, api, defaultSettings } from "@/lib/api";
import { Setup } from "./pages/Setup";
import { PermissionsPage } from "./pages/Permissions";
import { ProvidersPage } from "./pages/Providers";
import { HotkeysPage } from "./pages/Hotkeys";
import { DebugPage } from "./pages/Debug";
import { PromptsPage } from "./pages/Prompts";
import { ResetPage } from "./pages/Reset";

type Page =
  | "permissions"
  | "providers"
  | "hotkeys"
  | "prompts"
  | "debug"
  | "reset";

export function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [page, setPage] = useState<Page>("permissions");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getSettings()
      .then(setSettings)
      .catch(() => setSettings(defaultSettings()))
      .finally(() => setLoading(false));
  }, []);

  function handleSettings(updated: Settings) {
    setSettings(updated);
    api.saveSettings(updated).catch(console.error);
  }

  if (loading || !settings) {
    return <div className="app app--loading">Loading…</div>;
  }

  // Show the first-run wizard until setup_complete is true.
  if (!settings.setup_complete) {
    return (
      <div className="app">
        <header className="app__header">open-clicker-helper — Setup</header>
        <Setup settings={settings} onComplete={setSettings} />
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app__header">open-clicker-helper</header>
      <div className="app__body">
        <nav className="sidebar">
          <NavItem id="permissions" label="Permissions" current={page} onClick={setPage} />
          <NavItem id="providers" label="Providers" current={page} onClick={setPage} />
          <NavItem id="hotkeys" label="Hotkey" current={page} onClick={setPage} />
          <NavItem id="prompts" label="System Prompts" current={page} onClick={setPage} />
          <NavItem id="debug" label="Debug" current={page} onClick={setPage} />
          <NavItem id="reset" label="Reset" current={page} onClick={setPage} />
        </nav>
        <main className="main-content">
          {page === "permissions" && <PermissionsPage />}
          {page === "providers" && (
            <ProvidersPage settings={settings} onChange={handleSettings} />
          )}
          {page === "hotkeys" && (
            <HotkeysPage settings={settings} onChange={handleSettings} />
          )}
          {page === "prompts" && (
            <PromptsPage settings={settings} onChange={handleSettings} />
          )}
          {page === "debug" && (
            <DebugPage settings={settings} onChange={handleSettings} />
          )}
          {page === "reset" && (
            <ResetPage onReset={setSettings} />
          )}
        </main>
      </div>
    </div>
  );
}

function NavItem({
  id,
  label,
  current,
  onClick,
}: {
  id: Page;
  label: string;
  current: Page;
  onClick: (p: Page) => void;
}) {
  return (
    <button
      className={`nav-item ${current === id ? "nav-item--active" : ""}`}
      onClick={() => onClick(id)}
    >
      {label}
    </button>
  );
}
