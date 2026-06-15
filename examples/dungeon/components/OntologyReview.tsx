"use client";

import { useCallback, useEffect, useState } from "react";
import { proposalSummary, type Proposal, type ProposalList } from "@/lib/ontology";

export function OntologyReview() {
  const [enabled, setEnabled] = useState(true);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/ontology", { cache: "no-store" });
      const data = (await res.json()) as ProposalList;
      setEnabled(data.enabled);
      setProposals(data.proposals);
    } catch {
      setEnabled(false);
      setProposals([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const scan = useCallback(async () => {
    setBusy(true);
    setNote(null);
    try {
      const res = await fetch("/api/ontology", { method: "POST" });
      const d = (await res.json()) as { proposed?: number; error?: string };
      setNote(
        d.error
          ? "The keep could not scan its bonds."
          : `Scanned the bonds — ${d.proposed ?? 0} new relationship${d.proposed === 1 ? "" : "s"} proposed.`,
      );
      await refresh();
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const decide = useCallback(
    async (key: string, decision: "approve" | "reject") => {
      setBusy(true);
      try {
        await fetch("/api/ontology/decide", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ key, decision }),
        });
        await refresh();
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  if (!enabled) {
    return (
      <div className="onto-empty">
        <p>Ontology evolution is disabled.</p>
        <p className="onto-hint">
          Set <code>ONTOLOGY_EVOLUTION=true</code> on the core to let the keep propose
          named bonds between the kinds of things it remembers.
        </p>
      </div>
    );
  }

  const pending = proposals.filter((p) => p.status === "pending");
  const decided = proposals.filter((p) => p.status !== "pending");

  return (
    <div className="onto">
      <div className="onto-bar">
        <p className="onto-lede">
          The keep notices which <em>kinds</em> of things keep appearing together and
          proposes a name for the bond. You decide what becomes truth.
        </p>
        <button className="dream-btn" onClick={scan} disabled={busy}>
          {busy ? "scanning…" : "✦ scan bonds"}
        </button>
      </div>
      {note && <p className="onto-note">{note}</p>}

      {pending.length === 0 && <p className="onto-note">No proposals await your judgment.</p>}
      <ul className="onto-list">
        {pending.map((p) => (
          <li key={p._key} className="onto-card">
            <div className="onto-summary">
              <span className="onto-rel">{p.proposed_relationship}</span>
              <span className="onto-types">
                {p.label_a} → {p.label_b}
              </span>
              <span className="onto-support">{p.support} sightings</span>
            </div>
            <div className="onto-actions">
              <button className="dream-btn" onClick={() => decide(p._key, "approve")} disabled={busy}>
                approve
              </button>
              <button
                className="dream-btn share-btn"
                onClick={() => decide(p._key, "reject")}
                disabled={busy}
              >
                reject
              </button>
            </div>
          </li>
        ))}
      </ul>

      {decided.length > 0 && (
        <div className="onto-decided">
          <h3>Settled</h3>
          <ul>
            {decided.map((p) => (
              <li key={p._key} className={`onto-settled ${p.status}`}>
                <span>{proposalSummary(p)}</span>
                <span className="onto-status">{p.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
