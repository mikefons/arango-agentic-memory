"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useCallback, useEffect, useRef, useState } from "react";
import { getRoom, START_ROOM } from "@/lib/world";
import type { GameState } from "@/lib/tools";
import type { DungeonGraph } from "@/lib/graph";
import {
  ConfrontCard,
  PickupNote,
  RoomSceneCard,
  TalkCard,
  ToolSkeleton,
  type ConfrontView,
  type RoomView,
  type TalkView,
} from "./cards";
import { ThemeToggle } from "./ThemeToggle";
import { HealthStatus } from "./HealthStatus";
import { DungeonMap } from "./DungeonMap";
import { Dossier } from "./Dossier";
import { TabNav } from "./TabNav";
import { buildShareUrl } from "@/lib/share";
import {
  firstExpedition,
  heroId,
  nextExpedition,
  spendTorch,
  TORCH_BUDGET,
} from "@/lib/expedition";
import { HandoffBriefing } from "./HandoffBriefing";
import type { PrimeResult } from "@/lib/types";

const NEXT_HERO_TASK = "Descend into Ashfall Keep and expose the lies the last heroes could not.";
const BRIEFING_TOKENS = 1500;

const EMPTY_GAME: GameState = {
  roomId: START_ROOM, inventory: [], heardClaims: [], caughtClaims: [], ...firstExpedition(),
};

/** A fresh hero keeps the expedition/torch counters but starts the world anew. */
function freshHero(current: number): GameState {
  return { roomId: START_ROOM, inventory: [], heardClaims: [], caughtClaims: [],
           ...nextExpedition(current) };
}

const LS_KEY = "md-gamestate";
const MSG_KEY = "md-messages";

interface ToolOut {
  ok?: boolean;
  roomId?: string;
  name?: string;
  item?: string;
  heard?: string[];
  caught?: boolean;
  claimId?: string;
}

export function DungeonGame() {
  const [game, setGame] = useState<GameState>(EMPTY_GAME);
  const [graph, setGraph] = useState<DungeonGraph>({ nodes: [], edges: [] });
  const [sceneArt, setSceneArt] = useState(false);
  const [input, setInput] = useState("");
  const gameRef = useRef(game);
  gameRef.current = game;
  const streamRef = useRef<HTMLDivElement>(null);

  // resume position from a prior session
  useEffect(() => {
    try {
      const saved = localStorage.getItem(LS_KEY);
      if (saved) setGame({ ...EMPTY_GAME, ...(JSON.parse(saved) as Partial<GameState>) });
    } catch {
      /* ignore */
    }
  }, []);

  const { messages, sendMessage, status, error, setMessages } = useChat({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
  });

  // Restore the transcript so switching tabs (or reloading) doesn't reset the
  // narrative — game position already persists via gameState above.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(MSG_KEY);
      if (saved) setMessages(JSON.parse(saved));
    } catch {
      /* ignore */
    }
  }, [setMessages]);

  // Persist the transcript once a turn settles.
  useEffect(() => {
    if (status !== "ready" || messages.length === 0) return;
    try {
      localStorage.setItem(MSG_KEY, JSON.stringify(messages));
    } catch {
      /* ignore */
    }
  }, [messages, status]);

  // fold completed tool outputs back into game state (+ persist)
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant") return;
    let next = gameRef.current;
    let changed = false;
    for (const part of last.parts) {
      if (!part.type.startsWith("tool-") || !("state" in part) || part.state !== "output-available") {
        continue;
      }
      const out = part.output as ToolOut;
      if ((part.type === "tool-move" || part.type === "tool-look") && out.roomId && out.roomId !== next.roomId) {
        next = { ...next, roomId: out.roomId };
        changed = true;
      }
      if (part.type === "tool-take" && out.ok && out.item && !next.inventory.includes(out.item)) {
        next = { ...next, inventory: [...next.inventory, out.item] };
        changed = true;
      }
      if (part.type === "tool-talk" && Array.isArray(out.heard)) {
        const merged = [...new Set([...next.heardClaims, ...out.heard])];
        if (merged.length !== next.heardClaims.length) {
          next = { ...next, heardClaims: merged };
          changed = true;
        }
      }
      if (part.type === "tool-confront" && out.caught && out.claimId && !next.caughtClaims.includes(out.claimId)) {
        next = { ...next, caughtClaims: [...next.caughtClaims, out.claimId] };
        changed = true;
      }
    }
    if (changed) {
      setGame(next);
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
    }
  }, [messages]);

  // keep the latest narration in view
  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // shared graph fetch (map + room cards); also re-run after a dream
  const refreshGraph = useCallback(() => {
    fetch("/api/graph")
      .then((r) => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then((g: DungeonGraph) => setGraph(g))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshGraph();
  }, [messages.length, refreshGraph]);

  // read config-gated feature flags once (scene art is off unless enabled)
  useEffect(() => {
    fetch("/api/flags")
      .then((r) => (r.ok ? r.json() : {}))
      .then((f: { sceneArt?: boolean }) => setSceneArt(!!f.sceneArt))
      .catch(() => undefined);
  }, []);

  // "the keep dreams" — trigger Dream State consolidation (§13)
  const [dreaming, setDreaming] = useState(false);
  const [lastDream, setLastDream] = useState<{ r: number; c: number; s: number } | null>(null);

  // append a DM-styled narration line into the story stream
  const narrate = useCallback(
    (text: string) => {
      setMessages((prev) => [
        ...prev,
        { id: `dream-${Date.now()}`, role: "assistant", parts: [{ type: "text", text }] },
      ]);
    },
    [setMessages],
  );

  const runDream = useCallback(async () => {
    setDreaming(true);
    try {
      // detect communities first so Dream State can scope conflict review to them
      await fetch("/api/community", { method: "POST" }).catch(() => undefined);
      const res = await fetch("/api/dream", { method: "POST" });
      const d = (await res.json()) as {
        reviewed?: number;
        consolidated?: number;
        superseded?: number;
        breaker_tripped?: boolean;
        error?: string;
      };
      if (d.error) {
        narrate("The keep cannot dream — the deep memory lies silent.");
      } else if (d.breaker_tripped) {
        setLastDream({ r: d.reviewed ?? 0, c: 0, s: 0 });
        narrate("The keep stirs in its sleep, then stills — too much would change at once.");
      } else {
        const r = d.reviewed ?? 0;
        const c = d.consolidated ?? 0;
        const s = d.superseded ?? 0;
        setLastDream({ r, c, s });
        narrate(
          r === 0
            ? "A hush falls over Ashfall Keep. It dreams, but finds nothing new to settle."
            : `A hush falls over Ashfall Keep. It settles its memories — ${r} recalled` +
                (c ? `, ${c} distilled into truth` : "") +
                (s ? `, ${s} cast out as false` : "") +
                ".",
        );
      }
      // recompute graph salience (PageRank centrality) after consolidation
      await fetch("/api/salience", { method: "POST" }).catch(() => undefined);
      refreshGraph();
    } catch {
      narrate("The keep cannot dream — the deep memory lies silent.");
    } finally {
      setDreaming(false);
    }
  }, [refreshGraph, narrate]);

  // ── Expedition lifecycle (E-1): torch → chronicle → next hero ──
  const persistGame = useCallback((g: GameState) => {
    setGame(g);
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(g));
    } catch {
      /* ignore */
    }
  }, []);

  // Chronicle → briefing → send (E-2). Phase gates the modal vs. the briefing screen.
  const [phase, setPhase] = useState<"playing" | "chronicling" | "briefing">("playing");
  const [briefing, setBriefing] = useState<PrimeResult | null>(null);

  // Retire the current hero (fresh hero, empty transcript). The guild ledger persists.
  const descendNextHero = useCallback(() => {
    const next = freshHero(gameRef.current.expedition);
    persistGame(next);
    try {
      localStorage.removeItem(MSG_KEY); // the new hero's context window is empty
    } catch {
      /* ignore */
    }
    setMessages([
      {
        id: `hero-${next.expedition}`,
        role: "assistant",
        parts: [
          {
            type: "text",
            text:
              `${next.heroId} descends into Ashfall Keep, torch freshly lit. ` +
              `The guild's ledger remembers what the last hero did not.`,
          },
        ],
      },
    ]);
    setBriefing(null);
    setPhase("playing");
  }, [persistGame, setMessages]);

  // Chronicle the departing hero, then prime the next one and show the briefing screen.
  const beginChronicle = useCallback(async () => {
    const g = gameRef.current;
    setPhase("chronicling");
    const room = getRoom(g.roomId);
    const summary =
      `Expedition ${g.expedition} (${g.heroId}): reached the ${room.name}, ` +
      `carried ${g.inventory.length} item(s), heard ${g.heardClaims.length} claim(s), ` +
      `caught ${g.caughtClaims.length} lie(s).`;
    await fetch("/api/chronicle", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ summary }),
    }).catch(() => undefined);
    await fetch("/api/flush", { method: "POST" }).catch(() => undefined); // MA-1 barrier
    await runDream(); // the keep settles what the hero learned
    const nextHero = heroId(g.expedition + 1);
    const brief = await fetch("/api/prime", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ task: NEXT_HERO_TASK, heroId: nextHero }),
    })
      .then((r) => (r.ok ? (r.json() as Promise<PrimeResult>) : null))
      .catch(() => null);
    setBriefing(brief ?? { context: "", hits: [], entities: [], steps: [], tokens_injected: 0 });
    setPhase("briefing");
  }, [runDream]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || status === "streaming" || status === "submitted") return;
    if (game.torch <= 0) return; // torch spent — chronicle before the next hero descends
    sendMessage({ text }, { body: { gameState: gameRef.current } });
    setInput("");
    persistGame({ ...game, torch: spendTorch(game.torch) });
  }

  const room = getRoom(game.roomId);
  const busy = status === "streaming" || status === "submitted";

  return (
    <div className="app">
      <header>
        <div className="header-left">
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
          <TabNav />
        </div>
        <div className="crumbs">
          <span>Ashfall Keep</span>
          <span className="sep">/</span>
          <span className="here">{room.name}</span>
        </div>
        <div className="head-right">
          <button
            className="dream-btn"
            onClick={runDream}
            disabled={dreaming}
            title="The keep dreams — run Dream State consolidation"
          >
            {dreaming ? "dreaming…" : "✦ dream"}
          </button>
          <button
            className="dream-btn share-btn"
            onClick={() =>
              window.open(
                buildShareUrl({
                  room: room.name,
                  items: game.inventory.length,
                  lies: game.caughtClaims.length,
                }),
                "_blank",
              )
            }
            title="Share this run — open the OG card image"
          >
            ⧉ share
          </button>
          <button
            className="dream-btn"
            onClick={descendNextHero}
            disabled={dreaming || phase !== "playing"}
            title="Flee — retire this hero without chronicling (its findings are lost)"
          >
            ⚑ flee
          </button>
          <span className="pill save" title={`Hero ${game.heroId} · torch ${game.torch}/${TORCH_BUDGET}`}>
            <span className="dot" />
            {game.heroId} · 🔥 {game.torch}
          </span>
          <ThemeToggle />
        </div>
      </header>

      <main>
        <DungeonMap currentRoom={room.name} graph={graph} />

        <section className="pane narrative">
          <div className="stream" ref={streamRef}>
            <div className="stream-inner">
              {messages.length === 0 && (
                <div className="dm intro">
                  You stand at the threshold of Ashfall Keep. Soot stains the archway; somewhere
                  below, water drips in the dark. <span className="em">Something here remembers you.</span>
                  <div className="hint">Try: “look around”, “go down”, “take the lantern”.</div>
                </div>
              )}

              {messages.map((m) => (
                <div className="turn" key={m.id}>
                  {m.role === "user" ? (
                    <div className="you">
                      <span className="label">You</span>
                      <span className="text">{textOf(m.parts)}</span>
                    </div>
                  ) : (
                    <>{m.parts.map((part, i) => renderPart(part, i, graph, sceneArt))}</>
                  )}
                </div>
              ))}

              {busy && <div className="dm thinking">the keep stirs…</div>}

              {error && (
                <div className="dm error">
                  The keep falls silent — the Dungeon Master could not answer.
                  <div className="hint">{error.message || "check the dev server logs"}</div>
                </div>
              )}
            </div>
          </div>

          <div className="composer-wrap">
            <form className="composer" onSubmit={submit}>
              <span className="prompt-glyph">›</span>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="What do you do?"
                aria-label="Your action"
              />
              <button className="send" type="submit" disabled={busy}>
                Act ⏎
              </button>
            </form>
            <div className="composer-hint">
              <span>look · move · take</span>
              <span>memory · full mode</span>
            </div>
          </div>
        </section>

        <Dossier game={game} />
      </main>

      <footer>
        <div className="left">
          <HealthStatus />
          <span className="stat">tenant <b>dungeon-player</b></span>
          <span className="stat">room <b>{room.id}</b></span>
        </div>
        <div className="right">
          {lastDream && (
            <span className="stat">
              last dream <b>{lastDream.r} reviewed · {lastDream.c} distilled · {lastDream.s} superseded</b>
            </span>
          )}
          <span className="stat">memory <b>full mode</b></span>
        </div>
      </footer>

      {game.torch <= 0 && phase === "playing" && (
        <div className="expedition-over" role="dialog" aria-modal="true">
          <div className="eo-card">
            <h2>{game.heroId}&rsquo;s torch gutters out.</h2>
            <p>
              The dark closes in. What this hero learned dies with them &mdash; unless the
              Chronicler writes it into the guild&rsquo;s ledger for the next to inherit.
            </p>
            <button className="send" onClick={beginChronicle}>
              The Chronicler writes &rarr; brief the next hero
            </button>
          </div>
        </div>
      )}

      {phase === "chronicling" && (
        <div className="expedition-over" role="dialog" aria-modal="true">
          <div className="eo-card">
            <h2>The Chronicler writes…</h2>
            <p>Committing the hero&rsquo;s findings to the guild ledger, then assembling the briefing.</p>
          </div>
        </div>
      )}

      {phase === "briefing" && briefing && (
        <HandoffBriefing
          briefing={briefing}
          maxTokens={BRIEFING_TOKENS}
          heroLabel={heroId(game.expedition + 1)}
          onSend={descendNextHero}
        />
      )}
    </div>
  );
}

function textOf(parts: { type: string; text?: string }[]): string {
  return parts.filter((p) => p.type === "text").map((p) => p.text ?? "").join(" ");
}

type Part = { type: string; text?: string; state?: string; output?: unknown };

function renderPart(part: Part, i: number, graph: DungeonGraph, sceneArt: boolean) {
  if (part.type === "text") return <p className="dm" key={i}>{part.text}</p>;
  if (!part.type.startsWith("tool-")) return null;

  const verb = part.type.replace("tool-", "");
  if (part.state !== "output-available") return <ToolSkeleton key={i} tool={verb} />;
  const out = part.output as RoomView & { ok?: boolean; item?: string; reason?: string };

  if (part.type === "tool-look")
    return <RoomSceneCard key={i} tool="look" view={out} graph={graph} sceneArt={sceneArt} />;
  if (part.type === "tool-move") {
    return out.ok ? (
      <RoomSceneCard key={i} tool="move" view={out} graph={graph} sceneArt={sceneArt} />
    ) : (
      <div className="tool-skel" key={i}>↳ {out.reason ?? "the way is barred"}</div>
    );
  }
  if (part.type === "tool-take") {
    return <PickupNote key={i} ok={out.ok} item={out.item} reason={out.reason} />;
  }
  if (part.type === "tool-talk") {
    return out.ok ? (
      <TalkCard key={i} view={part.output as TalkView} />
    ) : (
      <div className="tool-skel" key={i}>↳ {out.reason ?? "no one answers"}</div>
    );
  }
  if (part.type === "tool-confront") {
    return out.ok ? (
      <ConfrontCard key={i} view={part.output as ConfrontView} />
    ) : (
      <div className="tool-skel" key={i}>↳ {out.reason ?? "no such claim"}</div>
    );
  }
  return null;
}
