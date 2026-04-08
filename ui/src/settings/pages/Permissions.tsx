import { useEffect, useState } from "react";
import { type Permissions, api } from "@/lib/api";

export function PermissionsPage() {
  const [perms, setPerms] = useState<Permissions | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function refresh() {
    setRefreshing(true);
    try {
      setPerms(await api.getPermissions());
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  return (
    <div className="page">
      <div className="page__header">
        <h2>Permissions</h2>
        <button className="btn btn--ghost btn--sm" onClick={refresh} disabled={refreshing}>
          {refreshing ? "Checking…" : "Refresh"}
        </button>
      </div>
      <p className="page__desc">
        open-clicker-helper requires three macOS permissions. Click{" "}
        <strong>Fix</strong> to open the relevant System Settings pane, grant
        access, then click <strong>Refresh</strong>.
      </p>
      <div className="perm-list">
        <PermRow
          label="Screen Recording"
          status={perms?.screen_recording ?? "unknown"}
          pane="screen_recording"
          note="Captures the focused window for the vision LLM."
        />
        <PermRow
          label="Accessibility"
          status={perms?.accessibility ?? "unknown"}
          pane="accessibility"
          note="Needed to perform simulated clicks (disable auto-click to skip)."
        />
        <PermRow
          label="Microphone"
          status={perms?.microphone ?? "unknown"}
          pane="microphone"
          note="Records your voice question."
        />
      </div>
    </div>
  );
}

function PermRow({
  label,
  status,
  pane,
  note,
}: {
  label: string;
  status: "granted" | "denied" | "unknown";
  pane: "screen_recording" | "accessibility" | "microphone";
  note: string;
}) {
  return (
    <div className="perm-row">
      <div className="perm-row__info">
        <span className="perm-row__label">{label}</span>
        <span className="perm-row__note">{note}</span>
      </div>
      <div className="perm-row__actions">
        <span className={`status status--${status}`}>{status}</span>
        {status !== "granted" && (
          <button
            className="btn btn--sm btn--ghost"
            onClick={() => api.openSystemSettings(pane)}
          >
            Fix →
          </button>
        )}
      </div>
    </div>
  );
}
