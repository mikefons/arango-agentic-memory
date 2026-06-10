"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useEffect, useRef, useState } from "react";
import { getRoom, START_ROOM } from "@/lib/world";
import type { GameState } from "@/lib/tools";
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

const EMPTY_GAME: GameState = { roomId: START_ROOM, inventory: [], heardClaims: [], caughtClaims: [] };

const LS_KEY = "md-gamestate";

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

  const { messages, sendMessage, status, error } = useChat({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
  });

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

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || status === "streaming" || status === "submitted") return;
    sendMessage({ text }, { body: { gameState: gameRef.current } });
    setInput("");
  }

  const room = getRoom(game.roomId);
  const busy = status === "streaming" || status === "submitted";

  return (
    <div className="app">
      <header>
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
        <div className="crumbs">
          <span>Ashfall Keep</span>
          <span className="sep">/</span>
          <span className="here">{room.name}</span>
        </div>
        <div className="head-right">
          <span className="pill save">
            <span className="dot" />
            run #1
          </span>
          <ThemeToggle />
        </div>
      </header>

      <main>
        <DungeonMap currentRoom={room.name} refreshKey={messages.length} />

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
                    <>{m.parts.map((part, i) => renderPart(part, i))}</>
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
          <span className="stat">memory <b>full mode</b></span>
        </div>
      </footer>
    </div>
  );
}

function textOf(parts: { type: string; text?: string }[]): string {
  return parts.filter((p) => p.type === "text").map((p) => p.text ?? "").join(" ");
}

type Part = { type: string; text?: string; state?: string; output?: unknown };

function renderPart(part: Part, i: number) {
  if (part.type === "text") return <p className="dm" key={i}>{part.text}</p>;
  if (!part.type.startsWith("tool-")) return null;

  const verb = part.type.replace("tool-", "");
  if (part.state !== "output-available") return <ToolSkeleton key={i} tool={verb} />;
  const out = part.output as RoomView & { ok?: boolean; item?: string; reason?: string };

  if (part.type === "tool-look") return <RoomSceneCard key={i} tool="look" view={out} />;
  if (part.type === "tool-move") {
    return out.ok ? (
      <RoomSceneCard key={i} tool="move" view={out} />
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
