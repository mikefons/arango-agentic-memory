"use client";

import { useState } from "react";
import type { Memo } from "@/lib/agents/synthesis";
import { RECOMMENDATION, findingTally, memoFilename, memoToMarkdown } from "@/lib/memo-export";

/**
 * The investment memo (DR-3e) — the deliverable. A slide-over that shows the recommendation,
 * thesis, and findings, each with its evidence chain traced back to shared memory. Export writes
 * the memo to Markdown; copy puts it on the clipboard. Client-only (download + clipboard).
 */
export function MemoPanel({ memo, onClose }: { memo: Memo; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const rec = RECOMMENDATION[memo.recommendation];
  const { risks, strengths } = findingTally(memo);

  const download = () => {
    const blob = new Blob([memoToMarkdown(memo)], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = memoFilename(memo);
    a.click();
    URL.revokeObjectURL(url);
  };

  const copy = async () => {
    await navigator.clipboard.writeText(memoToMarkdown(memo));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="memo-overlay" role="dialog" aria-modal="true" aria-label="Investment memo">
      <button className="memo-scrim" aria-label="Close memo" onClick={onClose} />
      <aside className="memo">
        <header className="memo-head">
          <div>
            <span className="memo-kicker">Investment memo</span>
            <h2 className="memo-target">{memo.target}</h2>
          </div>
          <button className="memo-x" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="memo-verdict">
          <span className={`memo-rec memo-rec-${rec.tone}`}>{rec.label}</span>
          <span className="memo-tally">
            {risks} risk{risks === 1 ? "" : "s"} · {strengths} strength{strengths === 1 ? "" : "s"}
          </span>
        </div>

        <p className="memo-thesis">{memo.thesis}</p>

        <div className="memo-actions">
          <button className="memo-btn" onClick={download}>
            ↓ Export Markdown
          </button>
          <button className="memo-btn" onClick={copy}>
            {copied ? "✓ Copied" : "⧉ Copy"}
          </button>
        </div>

        <Section title="Risks" kind="risk" memo={memo} />
        <Section title="Strengths" kind="strength" memo={memo} />
      </aside>
    </div>
  );
}

function Section({ title, kind, memo }: { title: string; kind: "risk" | "strength"; memo: Memo }) {
  const items = memo.findings.filter((f) => f.kind === kind);
  if (items.length === 0) return null;
  return (
    <section className="memo-section">
      <h3 className={`memo-section-title memo-${kind}`}>{title}</h3>
      {items.map((f, i) => (
        <article key={`${f.title}-${i}`} className="finding">
          <div className="finding-head">
            <span className="finding-title">{f.title}</span>
            <span className="finding-conf" title={`confidence ${Math.round(f.confidence * 100)}%`}>
              {Math.round(f.confidence * 100)}%
            </span>
          </div>
          <p className="finding-detail">{f.detail}</p>
          {f.evidence.length > 0 && (
            <ul className="chain">
              {f.evidence.map((e, j) => (
                <li key={j} className="chain-link">
                  {e}
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
    </section>
  );
}
