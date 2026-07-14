"use client";

import { useState } from "react";

/**
 * Onboarding (GUILD.md E-5) — a 3-beat intro that teaches the loop before the player
 * types a word: who the guild is (heroes are expendable, memory isn't), what the torch
 * is (the context window as a resource), and how you win (accumulated evidence names
 * the traitor). Shown once on first load; dismissible.
 */

interface Beat {
  glyph: string;
  title: string;
  body: string;
}

const BEATS: Beat[] = [
  {
    glyph: "✉",
    title: "A letter from the Guildmaster",
    body:
      "Ashfall Keep is haunted by a traitor whose lies are scattered too wide for any one " +
      "hero to catch. So the guild sends them one at a time — and remembers everything they " +
      "find. Heroes are expendable. The guild's memory is not.",
  },
  {
    glyph: "🔥",
    title: "The torch is your context window",
    body:
      "Each hero descends with a torch — a budget of turns. When it gutters out, the " +
      "Chronicler writes what they learned into the guild's shared ledger, and the next hero " +
      "inherits it in a briefing. What you don't chronicle is lost.",
  },
  {
    glyph: "⚖",
    title: "Name the traitor",
    body:
      "Talk to the keep's people, gather evidence, and catch them in contradictions. Each lie " +
      "the guild exposes persists across expeditions. Once enough are caught, accuse the " +
      "traitor to close the case — but a false accusation is fatal.",
  },
];

export function GuildIntro({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const beat = BEATS[step];
  const last = step === BEATS.length - 1;

  return (
    <div className="expedition-over intro-modal" role="dialog" aria-modal="true" aria-label="Welcome to the Guild">
      <div className="eo-card">
        <div className="intro-glyph">{beat.glyph}</div>
        <h2>{beat.title}</h2>
        <p>{beat.body}</p>

        <div className="intro-dots">
          {BEATS.map((_, i) => (
            <span key={i} className={`intro-dot${i === step ? " on" : ""}`} />
          ))}
        </div>

        <div className="eo-actions">
          <button className="send" onClick={() => (last ? onDone() : setStep(step + 1))}>
            {last ? "Descend →" : "Next →"}
          </button>
          {!last && (
            <button className="send ghost" onClick={onDone}>
              skip
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
