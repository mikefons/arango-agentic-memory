import type { Dispute } from "@/lib/agents/redteam";
import { DISPUTE_KIND } from "@/lib/dispute-map";

/**
 * The contradiction feed (DR-3d) — the red-team's findings, each a card that shows the
 * winner/loser resolution (what memory trusted and what it superseded) and lights up its
 * cluster in the evidence graph on hover. This is the payoff of *shared* memory: no single
 * specialist could see these; the red-team found them by reading everyone's claims at once.
 */
export function ContradictionFeed({
  disputes,
  activeIndex,
  onHover,
  running,
}: {
  disputes: Dispute[];
  activeIndex: number | null;
  onHover: (index: number | null) => void;
  running: boolean;
}) {
  return (
    <div className="feed">
      <div className="feed-title">
        Contradictions
        {disputes.length > 0 && <span className="feed-count">{disputes.length}</span>}
      </div>

      {disputes.length === 0 ? (
        <p className="feed-empty">
          {running
            ? "The red-team is cross-examining shared memory…"
            : "Run the campaign — the red-team surfaces contradictions no single specialist can see."}
        </p>
      ) : (
        <ul className="feed-list">
          {disputes.map((d, i) => {
            const meta = DISPUTE_KIND[d.kind];
            const active = activeIndex === i;
            return (
              <li
                key={`${d.subject}-${i}`}
                className={`disp disp-${meta.tone}${active ? " disp-active" : ""}`}
                onMouseEnter={() => onHover(i)}
                onMouseLeave={() => onHover(null)}
              >
                <div className="disp-head">
                  <span className="disp-kind">
                    <span className="disp-glyph">{meta.glyph}</span>
                    {meta.label}
                  </span>
                  <span className="disp-conf" title={`red-team confidence ${Math.round(d.confidence * 100)}%`}>
                    {Math.round(d.confidence * 100)}%
                  </span>
                </div>
                <div className="disp-subject">{d.subject}</div>
                <p className="disp-summary">{d.summary}</p>
                {(d.winner || d.loser) && (
                  <div className="disp-resolve">
                    {d.winner && (
                      <span className="disp-winner" title="trusted">
                        <span className="disp-mark">✓</span> {d.winner}
                      </span>
                    )}
                    {d.loser && (
                      <span className="disp-loser" title="superseded / contradicted">
                        <span className="disp-mark">✕</span> {d.loser}
                      </span>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
