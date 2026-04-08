// Transparent always-on-top window. Hosts both the cursor-following HUD and the
// click annotation layer. P0 just renders a placeholder dot — the animator and
// HUD states arrive in P3/P4.

export function Overlay() {
  return (
    <svg
      width="100%"
      height="100%"
      style={{
        position: "fixed",
        inset: 0,
        pointerEvents: "none",
      }}
    >
      <circle cx="50%" cy="50%" r="6" fill="rgba(255, 80, 80, 0.85)" />
    </svg>
  );
}
