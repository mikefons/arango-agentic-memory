import {
  convertToModelMessages,
  stepCountIs,
  streamText,
  wrapLanguageModel,
  type UIMessage,
} from "ai";
import { arangoMemory } from "@arango-memory/vercel";
import { DM_SYSTEM } from "@/lib/prompt";
import { getFlags } from "@/lib/flags";
import { resolveModel } from "@/lib/model";
import { makeTools, type GameState } from "@/lib/tools";
import { firstExpedition, GUILD_TIER } from "@/lib/expedition";
import { START_ROOM } from "@/lib/world";

const HINT_INSTRUCTION =
  "\n\nThe player may be stuck: weave one gentle, diegetic hint toward an " +
  "unresolved contradiction into your narration, without naming the lie outright.";

export const maxDuration = 30;

// Strip any trailing slash so `${coreUrl}/v1/...` never becomes `//v1/...` (→ 404).
const CORE_URL = (process.env.CORE_URL ?? "http://127.0.0.1:8080").replace(/\/+$/, "");
const CORE_API_KEY = process.env.CORE_API_KEY;  // bearer key when the core enforces auth (§17)

// One playthrough = one tenant. Within it, each expedition is an ephemeral hero
// (its own agent_id); the shared guild ledger (GUILD_TIER) outlives them (E-1).
const TENANT = "dungeon-player";

export async function POST(req: Request) {
  const { messages, gameState }: { messages: UIMessage[]; gameState?: GameState } = await req.json();
  const state: GameState = gameState ?? {
    roomId: START_ROOM,
    inventory: [],
    heardClaims: [],
    caughtClaims: [],
    ...firstExpedition(),
  };
  const session = `expedition-${state.expedition}`;
  // World-fact tools write to the shared guild ledger so their findings outlive the hero.
  const ctx = { tenant_id: TENANT, agent_id: GUILD_TIER, session_id: session };

  // The hero's DM: its own private conversational memory (agent_id = heroId), reading
  // across itself + the guild ledger in one fused pass (MA-2 read_agent_ids).
  const model = wrapLanguageModel({
    model: resolveModel(),
    middleware: arangoMemory({
      coreUrl: CORE_URL,
      tenantId: TENANT,
      agentId: state.heroId,
      sessionId: session,
      readAgentIds: [state.heroId, GUILD_TIER],
      mode: "full",
      apiKey: CORE_API_KEY,
    }),
  });

  const flags = await getFlags();
  const result = streamText({
    model,
    system: flags.hint ? DM_SYSTEM + HINT_INSTRUCTION : DM_SYSTEM,
    messages: convertToModelMessages(messages),
    tools: makeTools(ctx, state),
    stopWhen: stepCountIs(5),
  });

  return result.toUIMessageStreamResponse();
}
