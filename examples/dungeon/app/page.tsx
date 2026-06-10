import { ThemeToggle } from "@/components/ThemeToggle";
import { HealthStatus } from "@/components/HealthStatus";

export default function Page() {
  return (
    <div className="app">
      {/* ---------- header ---------- */}
      <header>
        <div className="brand">
          <span className="glyph">
            <svg width="20" height="18" viewBox="0 0 20 18" fill="none">
              <path d="M10 1 L19 17 L1 17 Z" stroke="currentColor" strokeWidth="1.4" fill="currentColor" fillOpacity="0.12" />
              <circle cx="10" cy="12" r="1.6" fill="currentColor" />
            </svg>
          </span>
          <span className="wordmark">
            Memory&nbsp;<b>Dungeon</b>
          </span>
        </div>
        <div className="crumbs">
          <span>Ashfall Keep</span>
          <span className="sep">/</span>
          <span className="here">The Gatehouse</span>
        </div>
        <div className="head-right">
          <span className="pill save">
            <span className="dot" />
            New run
          </span>
          <ThemeToggle />
        </div>
      </header>

      {/* ---------- main: 3-pane shell ---------- */}
      <main>
        <section className="pane map">
          <div className="pane-head">
            <span className="pane-title">Map</span>
            <span className="pane-meta">0 rooms</span>
          </div>
          <div className="placeholder">the knowledge graph renders here · 3.5c-2</div>
        </section>

        <section className="pane narrative">
          <div className="narrative-intro">
            <h1>Ashfall Keep</h1>
            <p>
              You stand at the threshold of a keep that should not still be standing. Soot stains the
              archway; somewhere below, water drips in the dark. <span className="em">Something here
              remembers you</span> — and not everyone you meet will tell you the truth.
            </p>
            <p style={{ marginTop: 18, fontFamily: "var(--font-mono), monospace", fontSize: 12, color: "var(--faint)", lineHeight: 1.6 }}>
              scaffold · 3.5c-0 — the playable loop, generative cards, and the lie engine arrive in
              3.5c-1 → 3.5c-3.
            </p>
          </div>
        </section>

        <aside className="pane dossier">
          <div className="pane-head">
            <span className="pane-title">Dossier</span>
            <span className="pane-meta">—</span>
          </div>
          <div className="placeholder">inventory · trust meters · contradiction ledger · 3.5c-3</div>
        </aside>
      </main>

      {/* ---------- status bar ---------- */}
      <footer>
        <div className="left">
          <HealthStatus />
          <span className="stat">tenant <b>player</b></span>
          <span className="stat">agent <b>dm</b></span>
        </div>
        <div className="right">
          <span className="stat">memory <b>full mode</b></span>
        </div>
      </footer>
    </div>
  );
}
