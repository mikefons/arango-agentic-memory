/**
 * "Why this needed shared memory" — the point of the whole demo (DR-3g), given a real spotlight.
 *
 * `Takeaway` is the memo's closing argument: a card per capability with the value (`why`) always
 * visible and the mechanism (`how`) revealed on demand via a native <details> disclosure — so the
 * exec sees the payoff and the technical evaluator can open the how. `WhyStrip` is the compact
 * teaser pinned to the War Room results view so the message lands even without opening the memo.
 */

import { TAKEAWAY_LEDE, WHY_SHARED_MEMORY } from "@/lib/callouts";

export function Takeaway() {
  return (
    <section className="takeaway" aria-label="Why this needed shared memory">
      <h3 className="takeaway-title">Why this needed shared memory</h3>
      <p className="takeaway-lede">{TAKEAWAY_LEDE}</p>
      <div className="takeaway-grid">
        {WHY_SHARED_MEMORY.map((c) => (
          <article key={c.title} className="cap-card">
            <h4 className="cap-title">{c.title}</h4>
            <p className="cap-why">{c.why}</p>
            <details className="cap-how">
              <summary className="cap-how-summary">How it works</summary>
              <p className="cap-how-body">{c.how}</p>
            </details>
          </article>
        ))}
      </div>
    </section>
  );
}

export function WhyStrip({ onOpen }: { onOpen: () => void }) {
  return (
    <aside className="why-strip" aria-label="Why this worked">
      <div className="why-strip-head">
        <span className="why-strip-label">Why this worked</span>
        <span className="why-strip-sub">the memory capabilities behind the memo</span>
      </div>
      <ul className="why-strip-list">
        {WHY_SHARED_MEMORY.map((c) => (
          <li key={c.title} className="why-strip-item">{c.title}</li>
        ))}
      </ul>
      <button className="why-strip-link" onClick={onOpen}>
        See how, in the memo →
      </button>
    </aside>
  );
}
