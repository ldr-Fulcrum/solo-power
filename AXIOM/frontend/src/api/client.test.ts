import { afterEach, expect, it, vi } from 'vitest';
import { Api, ApiError } from './client';

afterEach(() => vi.unstubAllGlobals());
it('sends a typed mutation with domain-bound fixture identity and a replay key', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'synthetic' }), { status: 201 }));
  vi.stubGlobal('fetch', fetcher);
  await new Api('bakery').createTask({ title: 'Synthetic task' });
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe('/api/v1/tasks');
  expect(options.headers.Authorization).toBe('Bearer synthetic-dani-bakery');
  expect(options.headers['Idempotency-Key']).toBeTruthy();
  expect(JSON.parse(options.body)).toEqual({ title: 'Synthetic task' });
});
it('preserves a supplied idempotency key across retries', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })));
  vi.stubGlobal('fetch', fetcher);
  const api = new Api('personal');
  await api.request('/memory', 'POST', { value: 'Synthetic reminder', remember: true }, 'same-key');
  await api.request('/memory', 'POST', { value: 'Synthetic reminder', remember: true }, 'same-key');
  expect(fetcher.mock.calls.map(call => call[1].headers['Idempotency-Key'])).toEqual(['same-key', 'same-key']);
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe('Bearer synthetic-dani-personal');
});
it('surfaces backend failure and correlation without claiming completion', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'RUNTIME_UNAVAILABLE', message: 'No runtime connected', correlation_id: 'synthetic-correlation' } }), { status: 503 })));
  try { await new Api('bakery').request('/assistant/messages', 'POST', { message: 'hello' }); throw new Error('Expected rejection'); }
  catch (error) { expect(error).toBeInstanceOf(ApiError); expect((error as ApiError).correlationId).toBe('synthetic-correlation'); expect((error as Error).message).toBe('No runtime connected'); }
});