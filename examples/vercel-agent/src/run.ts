/**
 * Minimal reference agent (DESIGN.md §20, Step 3.5b).
 *
 * A real `generateText` loop whose model is wrapped with the `arangoMemory`
 * middleware, so each turn retrieves+injects memory, stores the turn, and
 * captures tool calls as procedural memory — all against the running core.
 *
 * Run: see this folder's README (needs the core on :8080 + an Anthropic key).
 */

import { anthropic } from '@ai-sdk/anthropic';
import { arangoMemory } from '@arango-memory/vercel';
import { generateText, stepCountIs, tool, wrapLanguageModel } from 'ai';
import { z } from 'zod';

const coreUrl = process.env.ARANGO_MEMORY_CORE_URL ?? 'http://localhost:8080';
const modelId = process.env.MODEL ?? 'claude-sonnet-4-6';

const model = wrapLanguageModel({
  model: anthropic(modelId),
  middleware: arangoMemory({
    coreUrl,
    tenantId: 'demo-user',
    agentId: 'assistant',
    mode: 'lite',
  }),
});

const weather = tool({
  description: 'Look up the current weather for a city.',
  inputSchema: z.object({ city: z.string() }),
  execute: async ({ city }) => ({ city, conditions: 'sunny', tempC: 21 }),
});

async function turn(prompt: string): Promise<void> {
  const { text } = await generateText({
    model,
    tools: { weather },
    stopWhen: stepCountIs(5),
    prompt,
  });
  console.log(`\nUSER: ${prompt}\nASSISTANT: ${text}`);
}

// Turn 1 establishes a fact + exercises a tool (procedural memory).
// Turn 2 relies on memory recall from turn 1 (the model is told nothing here).
await turn("Hi, I'm Alex and I live in Lisbon. What's the weather there?");
await turn('Given what you know about me, what city should I check the forecast for next time?');
