/**
 * The Northwind Robotics data room (DR-0b) — a fictional Series-B target for the
 * Due-Diligence Room demo. Warehouse-automation robotics. The documents deliberately
 * disagree across time, source trust, and hidden relationships; `DEFECTS` is the oracle
 * of what a competent red-team should surface (used by acceptance + the golden run, DR-5a).
 *
 * All content is invented for the demo. Any resemblance to a real company is coincidental.
 */

import type { Defect, SourceDoc } from "./types";
import { sourceReliability } from "./reliability";

export const TARGET = {
  name: "Northwind Robotics",
  sector: "Warehouse automation robotics",
  round: "Series B",
  ceo: "Dana Reyes",
  cfo: "Marcus Cole",
} as const;

/** Build a SourceDoc, deriving `reliability` from the document kind. */
function doc(d: Omit<SourceDoc, "reliability">): SourceDoc {
  return { ...d, reliability: sourceReliability(d.type) };
}

export const DATA_ROOM: SourceDoc[] = [
  // ── Financial ──────────────────────────────────────────────────────────
  doc({
    id: "deck-financials",
    title: "Series B pitch deck — Financials",
    type: "pitch_deck",
    source: "Northwind management",
    as_of: "2026-01-15",
    forSpecialists: ["financial", "market"],
    text:
      "Northwind Robotics is raising a $40M Series B. We closed FY2025 at $8.0M ARR, up 3x " +
      "year over year, with net revenue retention of 130%. Gross margin is 68%. Our top " +
      "customers are Orion Retail, Vertex Foods, and Halcyon Grocers.",
  }),
  doc({
    id: "filing-q4",
    title: "Audited financial statements — Q4 FY2025",
    type: "audited_filing",
    source: "Brayton & Kell LLP (auditor)",
    as_of: "2026-03-10",
    forSpecialists: ["financial"],
    text:
      "Audited results for the fiscal year ended December 31, 2025. Total recognized revenue " +
      "was $5.2M on an annualized (ARR) basis. Revenue from Orion Retail represented 41% of " +
      "the total and is disclosed as a related-party transaction (see Note 7). No revenue was " +
      "recognized from Halcyon Grocers in the period.",
  }),
  doc({
    id: "churn-export",
    title: "Customer retention export — March 2026",
    type: "data_export",
    source: "Northwind CRM export",
    as_of: "2026-03-05",
    forSpecialists: ["financial", "market"],
    text:
      "Retention snapshot: of the top 5 accounts, 3 downgraded their contracts in Q4 2025. " +
      "Gross revenue churn for the period was 22%. Vertex Foods reduced its fleet from 40 to " +
      "12 units. Net revenue retention computed on this cohort was 84%.",
  }),
  doc({
    id: "cap-table",
    title: "Capitalization table — as filed",
    type: "cap_table",
    source: "Northwind counsel",
    as_of: "2026-02-01",
    forSpecialists: ["financial", "legal"],
    text:
      "Lead investor: Ridgeline Ventures (Series A, 22% fully diluted). Founders Dana Reyes " +
      "and Marcus Cole hold 48% combined. Ridgeline partner Marcus Cole also serves as " +
      "Northwind's CFO. Ridgeline Ventures holds a majority stake in Orion Retail.",
  }),

  // ── Legal ──────────────────────────────────────────────────────────────
  doc({
    id: "mgmt-qa",
    title: "Management Q&A — diligence responses",
    type: "management_qa",
    source: "Northwind management",
    as_of: "2026-01-20",
    forSpecialists: ["legal", "financial"],
    text:
      "Q: Any pending litigation? A: There is no material litigation pending or threatened " +
      "against the company. Q: Any related-party revenue? A: All customer revenue is " +
      "arm's-length. Q: IP ownership? A: All core navigation IP is proprietary and owned " +
      "by Northwind.",
  }),
  doc({
    id: "news-lawsuit",
    title: "Trade press — Vertex sues Northwind",
    type: "court_record",
    source: "RoboticsWire (court filing coverage)",
    as_of: "2026-02-12",
    forSpecialists: ["legal"],
    text:
      "Court records show Vertex Foods filed a breach-of-contract suit against Northwind " +
      "Robotics in the Superior Court on January 28, 2026, alleging undelivered units and " +
      "seeking $1.4M in damages. Northwind has not yet responded to the complaint.",
  }),
  doc({
    id: "contract-halcyon",
    title: "Halcyon Grocers — signed agreement",
    type: "signed_contract",
    source: "Executed contract (Halcyon Grocers)",
    as_of: "2025-12-18",
    forSpecialists: ["legal", "financial", "market"],
    text:
      "This Letter of Intent (non-binding) between Northwind Robotics and Halcyon Grocers " +
      "covers a paid pilot of up to 8 units at a total value of $400,000. It is expressly " +
      "not a commitment to a production rollout and may be terminated by either party.",
  }),
  doc({
    id: "org-chart",
    title: "Corporate structure & related parties",
    type: "org_chart",
    source: "Northwind counsel",
    as_of: "2026-02-01",
    forSpecialists: ["legal", "financial"],
    text:
      "Northwind Robotics Inc. wholly owns Northwind Logistics LLC. Note 7 (related parties): " +
      "Orion Retail is majority-owned by Ridgeline Ventures; Marcus Cole is a partner at " +
      "Ridgeline Ventures and Northwind's CFO. Transactions with Orion Retail are therefore " +
      "related-party in nature.",
  }),

  // ── Technical ─────────────────────────────────────────────────────────
  doc({
    id: "deck-product",
    title: "Series B pitch deck — Product & technology",
    type: "pitch_deck",
    source: "Northwind management",
    as_of: "2026-01-15",
    forSpecialists: ["technical"],
    text:
      "Our robots run proprietary SLAM navigation built in-house, delivering 99.9% uptime in " +
      "production. The platform is fully autonomous and defensible IP.",
  }),
  doc({
    id: "tech-audit",
    title: "Independent technical due-diligence note",
    type: "technical_audit",
    source: "Halden Technical Advisory",
    as_of: "2026-02-20",
    forSpecialists: ["technical"],
    text:
      "Code review findings: the core navigation stack is a fork of the open-source " +
      "'openslam-nav' library (BSD-licensed), with in-house tuning on top — not wholly " +
      "proprietary. Measured uptime across the Orion and Vertex pilots was 97.5%, not the " +
      "99.9% claimed in marketing materials.",
  }),
  doc({
    id: "pr-uptime",
    title: "Press release — reliability milestone",
    type: "press_release",
    source: "Northwind communications",
    as_of: "2025-10-02",
    forSpecialists: ["technical", "market"],
    text:
      "Northwind Robotics today announced its fleet surpassed one million autonomous picks " +
      "with industry-leading reliability across 12 distribution centers.",
  }),

  // ── Market ────────────────────────────────────────────────────────────
  doc({
    id: "pr-footprint",
    title: "Press release — operational footprint",
    type: "press_release",
    source: "Northwind communications",
    as_of: "2025-11-08",
    forSpecialists: ["market"],
    text:
      "Northwind now operates in 12 distribution centers across three regions, serving " +
      "grocery and general-merchandise customers.",
  }),
  doc({
    id: "blog-halcyon",
    title: "Industry blog — 'Northwind lands Halcyon'",
    type: "blog",
    source: "WarehouseWatchers (blog)",
    as_of: "2026-01-30",
    forSpecialists: ["market", "financial"],
    text:
      "Word on the street: Northwind Robotics just signed grocery chain Halcyon Grocers in a " +
      "deal rumored to be worth $2M annually — a huge win that cements them as the category " +
      "leader.",
  }),
  doc({
    id: "news-competitor",
    title: "Trade press — competitive landscape",
    type: "news",
    source: "RoboticsWire",
    as_of: "2026-01-25",
    forSpecialists: ["market"],
    text:
      "Automaton Inc remains the market leader in warehouse robotics by installed base. " +
      "Northwind Robotics and two other challengers compete for the mid-market grocery " +
      "segment, where switching costs are low.",
  }),
  doc({
    id: "customer-list",
    title: "Customer reference list",
    type: "management_qa",
    source: "Northwind management",
    as_of: "2026-01-18",
    forSpecialists: ["market", "financial"],
    text:
      "Reference customers: Orion Retail (flagship, largest deployment), Vertex Foods " +
      "(expanding), and Halcyon Grocers (newly signed). All three are cited as evidence of " +
      "strong commercial traction.",
  }),
];

/** Planted defects — the oracle for acceptance (DR-5a). At least 5, across all kinds. */
export const DEFECTS: Defect[] = [
  {
    id: "arr-drift",
    kind: "temporal_drift",
    summary: "Deck's $8.0M ARR (Jan) is superseded by the audited $5.2M (Mar).",
    subject: "Northwind Robotics ARR",
    docs: ["deck-financials", "filing-q4"],
    resolution:
      "Use the audited $5.2M; the deck's $8.0M is stale/inflated and should be superseded.",
  },
  {
    id: "litigation-contradiction",
    kind: "contradiction",
    summary: "Management says 'no material litigation'; court records show Vertex's suit.",
    subject: "Northwind litigation",
    docs: ["mgmt-qa", "news-lawsuit"],
    resolution:
      "Management claim is contradicted by a higher-trust court record — flag a material lawsuit.",
  },
  {
    id: "halcyon-reliability",
    kind: "reliability",
    summary: "Blog's rumored $2M Halcyon deal vs the signed $400K non-binding pilot.",
    subject: "Halcyon Grocers deal",
    docs: ["blog-halcyon", "contract-halcyon"],
    resolution:
      "Trust the signed contract: a $400K non-binding pilot, not a $2M booked deal.",
  },
  {
    id: "orion-related-party",
    kind: "related_party",
    summary:
      "Orion Retail (41% of revenue) is majority-owned by lead investor Ridgeline, whose " +
      "partner is Northwind's CFO — related-party revenue, not arm's-length.",
    subject: "Orion Retail revenue",
    docs: ["cap-table", "org-chart", "filing-q4"],
    resolution:
      "Discount Orion revenue as related-party; contradicts management's 'arm's-length' claim.",
  },
  {
    id: "nrr-contradiction",
    kind: "contradiction",
    summary: "Deck claims 130% NRR; the churn export shows 84% with 22% gross churn.",
    subject: "Northwind net revenue retention",
    docs: ["deck-financials", "churn-export"],
    resolution: "NRR ~84%, not 130% — the deck materially overstates retention.",
  },
  {
    id: "tech-reliability",
    kind: "reliability",
    summary:
      "Deck claims proprietary SLAM at 99.9% uptime; the tech audit finds an open-source " +
      "fork at 97.5% measured uptime.",
    subject: "Northwind navigation technology",
    docs: ["deck-product", "tech-audit"],
    resolution: "Nav IP is a tuned open-source fork; uptime ~97.5% — deck overstates both.",
  },
  {
    id: "footprint-stale",
    kind: "stale",
    summary:
      "'12 distribution centers' (Nov 2025) is uncontradicted but old — belief should stay " +
      "moderate pending a current source.",
    subject: "Northwind distribution centers",
    docs: ["pr-footprint"],
    resolution: "Treat '12 DCs' as unverified-current; corroborate before relying on it.",
  },
];

/** All documents in the data room. */
export function dataRoom(): SourceDoc[] {
  return DATA_ROOM;
}

/** Documents a given specialist should read. */
export function docsForSpecialist(id: SourceDoc["forSpecialists"][number]): SourceDoc[] {
  return DATA_ROOM.filter((d) => d.forSpecialists.includes(id));
}

/** The planted defects (the acceptance oracle). */
export function defects(): Defect[] {
  return DEFECTS;
}
