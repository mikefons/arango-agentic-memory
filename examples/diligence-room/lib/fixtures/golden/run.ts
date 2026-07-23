/**
 * The golden run (DR-3a) — a deterministic, correct end-to-end campaign the War Room can
 * replay when running canned (no key / stage safety), and the oracle the golden-run test
 * (DR-5a) checks a live run against.
 *
 * The graph is a real snapshot captured from the core (belief, salience, communities — incl.
 * the related-party cluster). The steps/disputes/memo are the *ideal* outputs, authored from
 * the planted-defect oracle so the reference is exactly right, not a stochastic sample.
 */

import type { CampaignStep } from "../../campaign";
import type { Dispute } from "../../agents/redteam";
import type { Memo } from "../../agents/synthesis";
import type { CoreGraph } from "../../room-state";
import graphJson from "./graph.json";

export const GOLDEN_GRAPH = graphJson as unknown as CoreGraph;

export const GOLDEN_STEPS: CampaignStep[] = [
  { name: "specialist:financial", status: "ok", detail: "5 claim(s)" },
  { name: "specialist:legal", status: "ok", detail: "4 claim(s)" },
  { name: "specialist:technical", status: "ok", detail: "2 claim(s)" },
  { name: "specialist:market", status: "ok", detail: "2 claim(s)" },
  { name: "flush:specialists", status: "ok" },
  { name: "consolidate", status: "ok", detail: "salience + 3 communities" },
  { name: "redteam", status: "ok", detail: "6 dispute(s)" },
  { name: "flush:redteam", status: "ok" },
  { name: "synthesis", status: "ok", detail: "6 finding(s) → pass" },
];

export const GOLDEN_DISPUTES: Dispute[] = [
  { subject: "Northwind ARR", kind: "temporal_drift", summary: "Audited $5.2M (Mar) supersedes deck $8.0M (Jan) — 35% overstatement.", winner: "$5.2M (audited)", loser: "$8.0M (deck)", confidence: 0.9 },
  { subject: "Net revenue retention", kind: "contradiction", summary: "CRM churn export shows 84% vs management's 130%.", winner: "84% (CRM)", loser: "130% (management)", confidence: 0.85 },
  { subject: "Litigation", kind: "contradiction", summary: "Court record shows Vertex's $1.4M suit vs management's 'no litigation'.", winner: "$1.4M suit (court)", loser: "no litigation (management)", confidence: 0.9 },
  { subject: "Halcyon Grocers deal", kind: "reliability", summary: "Signed $400K non-binding LOI vs blog's $2M rumor.", winner: "$400K LOI (contract)", loser: "$2M (blog)", confidence: 0.85 },
  { subject: "Orion Retail revenue", kind: "related_party", summary: "Orion (41% of revenue) is owned by lead investor Ridgeline, whose partner is the CFO — related-party, not arm's-length.", winner: "related-party (org chart + cap table)", loser: "arm's-length (management)", confidence: 0.9 },
  { subject: "Navigation technology", kind: "reliability", summary: "Audit finds an open-source fork at 97.5% vs deck's 'proprietary, 99.9%'.", winner: "open-source fork, 97.5% (audit)", loser: "proprietary, 99.9% (deck)", confidence: 0.8 },
];

export const GOLDEN_MEMO: Memo = {
  target: "Northwind Robotics",
  recommendation: "pass",
  thesis:
    "A credible warehouse-automation team, but the raised numbers materially overstate the " +
    "business and management's disclosures don't survive the audited record. Multiple " +
    "independent misstatements — revenue, retention, litigation, IP, and related-party " +
    "concentration — undermine confidence in the reported metrics.",
  findings: [
    { title: "ARR overstated ~35%", kind: "risk", detail: "The deck's $8.0M ARR is superseded by the audited $5.2M.", evidence: ["deck: $8.0M (2026-01-15)", "Brayton & Kell audited filing: $5.2M (2026-03-10)"], confidence: 0.9 },
    { title: "Undisclosed material litigation", kind: "risk", detail: "Management represented no litigation; court records show Vertex's $1.4M breach suit filed 8 days later.", evidence: ["management Q&A (2026-01-20)", "court record: Vertex suit $1.4M (2026-01-28)"], confidence: 0.9 },
    { title: "Related-party revenue concentration", kind: "risk", detail: "Orion (41% of revenue) is owned by the lead investor Ridgeline, whose partner is the CFO — not arm's-length.", evidence: ["audited filing: Orion 41%, related-party (Note 7)", "org chart: Orion owned by Ridgeline; CFO is a Ridgeline partner", "cap table: Ridgeline lead investor"], confidence: 0.9 },
    { title: "Net revenue retention overstated", kind: "risk", detail: "Deck claims 130% NRR; the CRM churn export shows 84%.", evidence: ["deck: 130% (2026-01-15)", "CRM churn export: 84% (2026-03-05)"], confidence: 0.85 },
    { title: "Technology & IP overstated", kind: "risk", detail: "Deck claims proprietary SLAM at 99.9%; the audit finds an open-source fork at 97.5%.", evidence: ["deck: proprietary, 99.9%", "Halden audit: open-source fork, 97.5%"], confidence: 0.8 },
    { title: "Real automation demand", kind: "strength", detail: "Multiple reference customers deploying units in a growing category.", evidence: ["customer list: Orion, Vertex, Halcyon"], confidence: 0.5 },
  ],
};

export interface GoldenRun {
  room: string;
  steps: CampaignStep[];
  disputes: Dispute[];
  memo: Memo;
  graph: CoreGraph;
}

export const GOLDEN_RUN: GoldenRun = {
  room: "golden",
  steps: GOLDEN_STEPS,
  disputes: GOLDEN_DISPUTES,
  memo: GOLDEN_MEMO,
  graph: GOLDEN_GRAPH,
};
