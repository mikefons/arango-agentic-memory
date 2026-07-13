"use client";

import { useMemo, useState } from "react";
import { keptTokens, toBriefingItems, type BriefingItem } from "@/lib/briefing";
import { GUILD_TIER } from "@/lib/expedition";
import type { PrimeResult } from "@/lib/types";

const SECTION_TITLE: Record<BriefingItem["kind"], string> = {
  history: "Relevant history",
  entity: "Key entities",
  tool: "Prior tool runs",
};
const ORDER: BriefingItem["kind"][] = ["history", "entity", "tool"];

/**
 * The handoff briefing (E-2) — renders a `prime()` result between expeditions: what the
 * incoming hero inherits from the guild ledger, with provenance badges and a token
 * budget the player can spend by pinning/dropping items.
 */
export function HandoffBriefing({
  briefing,
  maxTokens,
  heroLabel,
  busy,
  onSend,
}: {
  briefing: PrimeResult;
  maxTokens: number;
  heroLabel: string;
  busy?: boolean;
  onSend: () => void;
}) {
  const items = useMemo(() => toBriefingItems(briefing), [briefing]);
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const kept = keptTokens(items, dropped);
  const pct = Math.min(100, Math.round((kept / maxTokens) * 100));

  const toggle = (id: string) =>
    setDropped((d) => {
      const n = new Set(d);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="briefing-overlay" role="dialog" aria-modal="true">
      <div className="briefing-card">
        <h2>The Chronicler briefs {heroLabel}</h2>
        <p className="briefing-sub">
          What the guild remembers &mdash; pin what matters, drop the rest.
        </p>

        <div className="budget">
          <div className="budget-bar">
            <div className="budget-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="budget-label">
            {kept} / {maxTokens} tokens
          </span>
        </div>

        <div className="briefing-body">
          {items.length === 0 && (
            <p className="briefing-empty">
              The ledger is bare &mdash; this hero descends into the unknown.
            </p>
          )}
          {ORDER.map((kind) => {
            const group = items.filter((i) => i.kind === kind);
            if (!group.length) return null;
            return (
              <section key={kind}>
                <h3>{SECTION_TITLE[kind]}</h3>
                <ul>
                  {group.map((it) => (
                    <li key={it.id} className={dropped.has(it.id) ? "dropped" : ""}>
                      <button
                        className="pin"
                        onClick={() => toggle(it.id)}
                        title={dropped.has(it.id) ? "restore" : "drop"}
                        aria-label={dropped.has(it.id) ? "restore item" : "drop item"}
                      >
                        {dropped.has(it.id) ? "+" : "×"}
                      </button>
                      <span className="btext">{it.text}</span>
                      {it.kind === "history" && it.agent && (
                        <span className="prov">{it.agent === GUILD_TIER ? "guild" : it.agent}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>

        <button className="send-hero" onClick={onSend} disabled={busy}>
          Send in {heroLabel} &rarr;
        </button>
      </div>
    </div>
  );
}
