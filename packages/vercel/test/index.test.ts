import { afterEach, describe, expect, it, vi } from 'vitest';

import { arangoMemory } from '../src/index.js';

const OPTS = { coreUrl: 'http://core', tenantId: 't1', agentId: 'a1' };

interface StubResponse {
  ok: boolean;
  json: () => Promise<unknown>;
}

/** Route fetch by URL fragment; record every call's parsed JSON body. */
function mockFetch(handlers: Record<string, () => StubResponse>) {
  const bodies: { url: string; body: any }[] = [];
  const fn = vi.fn(async (url: string, init?: { body?: string }) => {
    bodies.push({ url, body: init?.body ? JSON.parse(init.body) : undefined });
    for (const [frag, make] of Object.entries(handlers)) {
      if (url.includes(frag)) return make();
    }
    return { ok: false, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fn);
  return { fn, bodies, of: (frag: string) => bodies.filter((b) => b.url.includes(frag)) };
}

function userPrompt(text: string) {
  return [{ role: 'user', content: [{ type: 'text', text }] }];
}

afterEach(() => vi.unstubAllGlobals());

describe('transformParams (retrieve + inject)', () => {
  it('injects retrieved context as a leading system message', async () => {
    mockFetch({ '/v1/retrieve': () => ({ ok: true, json: async () => ({ context: 'past fact' }) }) });
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: userPrompt('hello') };

    const out: any = await mw.transformParams!({ params, type: 'generate', model: {} as any });
    expect(out.prompt[0].role).toBe('system');
    expect(out.prompt[0].content).toContain('past fact');
  });

  it('passes through unchanged when the core fails (memory-less turn)', async () => {
    mockFetch({ '/v1/retrieve': () => { throw new Error('core down'); } });
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: userPrompt('hello') };

    const out: any = await mw.transformParams!({ params, type: 'generate', model: {} as any });
    expect(out).toBe(params);
  });

  it('skips retrieval when there is no user text', async () => {
    const { fn } = mockFetch({});
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: [{ role: 'system', content: 'x' }] };

    await mw.transformParams!({ params, type: 'generate', model: {} as any });
    expect(fn).not.toHaveBeenCalled();
  });
});

describe('wrapGenerate (store + tool capture)', () => {
  it('stores the turn after generation', async () => {
    const m = mockFetch({ '/v1/store': () => ({ ok: true, json: async () => ({}) }) });
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: userPrompt('remember this') };

    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/store').length).toBe(1));
    expect(m.of('/v1/store')[0].body.content).toBe('remember this');
  });

  it('records a completed tool call as procedural memory', async () => {
    const m = mockFetch({
      '/v1/store': () => ({ ok: true, json: async () => ({}) }),
      '/v1/step': () => ({ ok: true, json: async () => ({ step_id: 'S1' }) }),
    });
    const mw = arangoMemory(OPTS);
    const params: any = {
      prompt: [
        ...userPrompt('search please'),
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'tc1', toolName: 'search', input: { q: 'cats' } }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'tc1', toolName: 'search', output: { type: 'json', value: { n: 3 } } }] },
      ],
    };

    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/step').length).toBe(1));
    const body = m.of('/v1/step')[0].body;
    expect(body.tool_name).toBe('search');
    expect(body.arguments).toEqual({ q: 'cats' });
    expect(body.outcome).toBe('success');
  });

  it('marks an error tool result as failure', async () => {
    const m = mockFetch({
      '/v1/store': () => ({ ok: true, json: async () => ({}) }),
      '/v1/step': () => ({ ok: true, json: async () => ({ step_id: 'S1' }) }),
    });
    const mw = arangoMemory(OPTS);
    const params: any = {
      prompt: [
        ...userPrompt('do it'),
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'tc9', toolName: 'fetch', input: {} }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'tc9', toolName: 'fetch', output: { type: 'error-text', value: 'boom' } }] },
      ],
    };

    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/step').length).toBe(1));
    expect(m.of('/v1/step')[0].body.outcome).toBe('failure');
  });

  it('de-dupes a tool call across turns and chains prev_step_key', async () => {
    let n = 0;
    const m = mockFetch({
      '/v1/store': () => ({ ok: true, json: async () => ({}) }),
      '/v1/step': () => ({ ok: true, json: async () => ({ step_id: `S${++n}` }) }),
    });
    const mw = arangoMemory(OPTS);
    const base = [
      ...userPrompt('go'),
      { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'tc1', toolName: 'search', input: {} }] },
      { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'tc1', toolName: 'search', output: { type: 'json', value: {} } }] },
    ];
    const params1: any = { prompt: base };
    const params2: any = {
      prompt: [
        ...base,
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'tc2', toolName: 'book', input: {} }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'tc2', toolName: 'book', output: { type: 'json', value: {} } }] },
      ],
    };

    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params: params1, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/step').length).toBe(1));
    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params: params2, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/step').length).toBe(2));

    const steps = m.of('/v1/step');
    expect(steps[0].body.prev_step_key).toBeUndefined();   // first step
    expect(steps[1].body.tool_name).toBe('book');
    expect(steps[1].body.prev_step_key).toBe('S1');         // chained from the first
  });
});
