import { afterEach, describe, expect, it, vi } from 'vitest';

import { arangoMemory, flush, prime } from '../src/index.js';

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

describe('captureResponses (MA-4 — store the model output)', () => {
  it('stores the assistant response as a second turn', async () => {
    const m = mockFetch({ '/v1/store': () => ({ ok: true, json: async () => ({}) }) });
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: userPrompt('ask') };
    const result = { content: [{ type: 'text', text: 'the answer is 42' }] };

    await mw.wrapGenerate!({ doGenerate: async () => result as any, params, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/store').length).toBe(2));
    const stored = m.of('/v1/store').map((s) => s.body.content);
    expect(stored).toContain('ask');
    expect(stored).toContain('[assistant] the answer is 42');
  });

  it('does not store the response when captureResponses is false', async () => {
    const m = mockFetch({ '/v1/store': () => ({ ok: true, json: async () => ({}) }) });
    const mw = arangoMemory({ ...OPTS, captureResponses: false });
    const params: any = { prompt: userPrompt('ask') };
    const result = { content: [{ type: 'text', text: 'hidden' }] };

    await mw.wrapGenerate!({ doGenerate: async () => result as any, params, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/store').length).toBe(1));
    expect(m.of('/v1/store')[0].body.content).toBe('ask');
  });

  it('taps the stream and stores the accumulated response onFinish', async () => {
    const m = mockFetch({ '/v1/store': () => ({ ok: true, json: async () => ({}) }) });
    const mw = arangoMemory(OPTS);
    const params: any = { prompt: userPrompt('ask') };
    const source = new ReadableStream({
      start(c) {
        c.enqueue({ type: 'text-delta', id: '1', delta: 'hel' });
        c.enqueue({ type: 'text-delta', id: '1', delta: 'lo' });
        c.close();
      },
    });

    const out: any = await mw.wrapStream!({ doStream: async () => ({ stream: source }) as any, params, model: {} as any });
    // Drain the returned stream so the tap's flush() runs.
    const reader = out.stream.getReader();
    while (!(await reader.read()).done) { /* consume */ }
    await vi.waitFor(() => expect(m.of('/v1/store').some((s) => s.body.content === '[assistant] hello')).toBe(true));
  });
});

describe('syncWrites + readAgentIds (MA-1b / MA-2b)', () => {
  it('sends sync:true on stores when syncWrites is set', async () => {
    const m = mockFetch({ '/v1/store': () => ({ ok: true, json: async () => ({}) }) });
    const mw = arangoMemory({ ...OPTS, syncWrites: true, captureResponses: false });
    await mw.wrapGenerate!({ doGenerate: async () => ({}) as any, params: { prompt: userPrompt('x') } as any, model: {} as any });
    await vi.waitFor(() => expect(m.of('/v1/store').length).toBe(1));
    expect(m.of('/v1/store')[0].body.sync).toBe(true);
  });

  it('threads readAgentIds into the retrieve ctx', async () => {
    const m = mockFetch({ '/v1/retrieve': () => ({ ok: true, json: async () => ({ context: '' }) }) });
    const mw = arangoMemory({ ...OPTS, readAgentIds: ['a1', 'crew::query'] });
    await mw.transformParams!({ params: { prompt: userPrompt('q') } as any, type: 'generate', model: {} as any });
    expect(m.of('/v1/retrieve')[0].body.ctx.read_agent_ids).toEqual(['a1', 'crew::query']);
  });
});

describe('prime + flush helpers (MA-3b / MA-1b)', () => {
  it('prime posts the task and returns the briefing', async () => {
    const briefing = { context: '## Relevant history\n- x', hits: [], entities: [], steps: [], tokens_injected: 5 };
    const m = mockFetch({ '/v1/prime': () => ({ ok: true, json: async () => briefing }) });
    const res = await prime({ coreUrl: 'http://core', task: 'brief me', tenantId: 't', agentId: 'b', readAgentIds: ['b', 'shared'] });
    expect(res).toEqual(briefing);
    expect(m.of('/v1/prime')[0].body.task).toBe('brief me');
    expect(m.of('/v1/prime')[0].body.ctx.read_agent_ids).toEqual(['b', 'shared']);
  });

  it('prime returns an empty briefing on fault', async () => {
    mockFetch({ '/v1/prime': () => { throw new Error('down'); } });
    const res = await prime({ coreUrl: 'http://core', task: 't', tenantId: 't', agentId: 'a' });
    expect(res.context).toBe('');
    expect(res.hits).toEqual([]);
  });

  it('flush posts the barrier and returns status', async () => {
    const m = mockFetch({ '/v1/flush': () => ({ ok: true, json: async () => ({ status: 'flushed' }) }) });
    const res = await flush({ coreUrl: 'http://core', tenantId: 't', agentId: 'a', timeoutMs: 3000 });
    expect(res.status).toBe('flushed');
    expect(m.of('/v1/flush')[0].body.timeout_ms).toBe(3000);
  });
});
