import { useEffect, useState } from "react";
import { api, type Permissions, type SidecarHealth } from "@/lib/api";

export function App() {
  const [permissions, setPermissions] = useState<Permissions | null>(null);
  const [health, setHealth] = useState<SidecarHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [perms, h] = await Promise.all([
          api.getPermissions(),
          api.pingSidecar().catch(() => ({ ok: false, version: null })),
        ]);
        if (cancelled) return;
        setPermissions(perms);
        setHealth(h);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app">
      <header className="app__header">open-clicker-helper</header>
      <main className="app__main">
        <h2>Permissions</h2>
        {error && <p style={{ color: "crimson" }}>{error}</p>}
        {permissions ? (
          <ul>
            <li>
              Screen recording <StatusBadge value={permissions.screen_recording} />
            </li>
            <li>
              Accessibility <StatusBadge value={permissions.accessibility} />
            </li>
            <li>
              Microphone <StatusBadge value={permissions.microphone} />
            </li>
          </ul>
        ) : (
          <p>Loading…</p>
        )}

        <h2>Sidecar</h2>
        {health ? (
          <p>
            {health.ok ? `online (${health.version ?? "unknown"})` : "offline"}
          </p>
        ) : (
          <p>checking…</p>
        )}
      </main>
    </div>
  );
}

function StatusBadge({ value }: { value: "granted" | "denied" | "unknown" }) {
  return <span className={`status status--${value}`}>{value}</span>;
}
