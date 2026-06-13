# Vercel AI SDK Adapter

`@arango-memory/vercel` (`packages/vercel/`) — a thin `LanguageModelV2Middleware`
(no memory logic of its own). It (1) retrieves memory and **injects** it before a
turn, (2) durably **stores** the turn after, and (3) **captures completed tool
calls** as procedural memory. Every fault degrades to a working, memory-less turn
(DESIGN.md §15/§20). Talks to the core over HTTP (`/v1`).

## Install
```jsonc
// package.json — consumed via a workspace file: dep in this monorepo
{ "dependencies": { "@arango-memory/vercel": "file:../../packages/vercel", "ai": "^5.0.0" } }
```

## Usage
```ts
import { streamText, wrapLanguageModel, convertToModelMessages } from "ai";
import { gateway } from "@ai-sdk/gateway";
import { arangoMemory } from "@arango-memory/vercel";

const model = wrapLanguageModel({
  model: gateway("anthropic/claude-sonnet-4.5"),
  middleware: arangoMemory({
    coreUrl: process.env.CORE_URL!,   // e.g. http://127.0.0.1:8080
    tenantId: "acme",
    agentId: "assistant-1",
    sessionId: "run-42",              // optional
    mode: "full",                     // "lite" | "full" (default: core's setting)
    maxMemoryTokens: 1500,            // optional
    retrieveTimeoutMs: 800,           // optional — abort slow retrieval so a turn never hangs
    captureToolTraces: true,          // optional, default true
  }),
});

const result = streamText({ model, messages: convertToModelMessages(messages), tools });
```

## Notes
- **Injection:** prepends a `[MEMORY CONTEXT]` system block from `/v1/retrieve`
  (read access). No context → unchanged params.
- **Store:** best-effort `POST /v1/store` after `generate`/`stream` (write access);
  durability lives in the core's queue, so the turn never blocks.
- **Tool capture:** pairs `tool-call` + `tool-result` parts from the prompt history
  (deduped by `toolCallId`, chained via `prev_step_key`) → `POST /v1/step`. A
  LanguageModel middleware only sees a tool's outcome on the *next* turn, so there's
  an inherent one-turn lag.
- Reference app: [`examples/dungeon`](../../examples/dungeon) (and the minimal
  `examples/vercel-agent`).
