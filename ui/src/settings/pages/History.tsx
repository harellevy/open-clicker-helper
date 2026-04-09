import { useCallback, useEffect, useState } from "react";
import { type SessionRecord, api } from "@/lib/api";

/**
 * Conversation history page — lists the most recent push-to-talk cycles.
 *
 * Records are written by the Rust shell after every successful pipeline.run
 * call (see `process_recording` in `lib.rs`) and capped at 50 entries by
 * `history::MAX_SESSIONS`.
 */
export function HistoryPage() {
  const [sessions, setSessions] = useState<SessionRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const list = await api.getHistory();
      // Most-recent first.
      setSessions([...list].reverse());
      setError(null);
    } catch (e) {
      setError(String(e));
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function doClear() {
    setBusy(true);
    try {
      await api.clearHistory();
      setSessions([]);
      setConfirming(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h2>History</h2>
      <p className="page__desc">
        The last {sessions?.length ?? 0} push-to-talk sessions are listed below
        (most recent first). Only the question, answer, and timing metadata are
        kept — no audio or screenshots.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button className="btn btn--ghost" onClick={() => void reload()}>
          Refresh
        </button>
        {sessions && sessions.length > 0 && !confirming && (
          <button
            className="btn btn--danger"
            onClick={() => setConfirming(true)}
          >
            Clear history
          </button>
        )}
        {confirming && (
          <>
            <button
              className="btn btn--ghost"
              onClick={() => setConfirming(false)}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              className="btn btn--danger"
              onClick={doClear}
              disabled={busy}
            >
              {busy ? "Clearing…" : "Yes, clear all"}
            </button>
          </>
        )}
      </div>

      {error && <p className="setup-error">{error}</p>}

      {sessions === null && <p>Loading…</p>}
      {sessions !== null && sessions.length === 0 && (
        <p style={{ opacity: 0.6 }}>No sessions yet.</p>
      )}

      {sessions !== null && sessions.length > 0 && (
        <ul className="history-list">
          {sessions.map((s) => (
            <HistoryEntry key={s.id} session={s} />
          ))}
        </ul>
      )}
    </div>
  );
}

function HistoryEntry({ session }: { session: SessionRecord }) {
  const when = new Date(session.timestamp_ms).toLocaleString();
  return (
    <li className="history-entry">
      <div className="history-entry__meta">
        <span>{when}</span>
        <span>
          {session.grounding_source ? `via ${session.grounding_source}` : "text-only"}
          {session.steps_count > 0 ? ` · ${session.steps_count} step${session.steps_count === 1 ? "" : "s"}` : ""}
          {session.total_ms != null ? ` · ${session.total_ms} ms` : ""}
        </span>
      </div>
      <div className="history-entry__q">Q: {session.transcript || <em>(empty)</em>}</div>
      <div className="history-entry__a">A: {session.answer || <em>(empty)</em>}</div>
    </li>
  );
}
