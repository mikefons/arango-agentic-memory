import type { Memo } from "@/lib/agents/synthesis";
import { RECOMMENDATION, findingTally } from "@/lib/memo-export";

/**
 * A notice that the investment memo is ready, with a button to open it. The memo does not open on
 * its own: it is a slide-over that covers the contradiction feed, which the viewer may still be
 * reading when synthesis finishes.
 */
export function MemoReadyNotice({
  memo,
  onOpen,
  onDismiss,
}: {
  memo: Memo;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const rec = RECOMMENDATION[memo.recommendation];
  const { risks } = findingTally(memo);
  return (
    <div className="memo-notice" role="status" aria-live="polite">
      <span className={`memo-notice-dot memo-notice-${rec.tone}`} aria-hidden />
      <div className="memo-notice-text">
        <b>Investment memo ready</b>
        <span>
          {rec.label} · {risks} risk{risks === 1 ? "" : "s"} flagged
        </span>
      </div>
      <button className="memo-notice-open" onClick={onOpen}>
        Open memo →
      </button>
      <button className="memo-notice-x" onClick={onDismiss} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}
