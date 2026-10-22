import test from 'node:test';
import assert from 'node:assert/strict';
import { RagBackend, BackendError } from '../lib/backend.js';
import { createFakeChild } from './fake-child.js';

const CFG = {
  pythonExe: 'python', projectDir: '/tmp/project', indexDir: '/tmp/index',
  device: 'auto', toolTimeoutMs: 2000,
};

function okChild(handler = (request) => ({ id: request.id, ok: true, result: { echo: request.method } })) {
  return createFakeChild({ onRequest: (request, respond) => respond(handler(request)) });
}

test('成功请求返回 result 与 meta', async () => {
  const child = createFakeChild({
    onRequest: (request, respond) => respond({ id: request.id, ok: true, result: { pong: 1 }, meta: { pid: 7 } }),
  });
  const backend = new RagBackend(CFG, { spawnImpl: () => child });
  const response = await backend.request('search', { query: 'x' });
  assert.deepEqual(response.result, { pong: 1 });
  assert.deepEqual(response.meta, { pid: 7 });
  assert.deepEqual(backend.meta, { pid: 7 });
  await backend.dispose();
});

test('并发请求按 id 匹配（响应乱序）', async () => {
  let first = true;
  const child = createFakeChild({
    onRequest: (request, respond) => {
      if (request.method === 'ping') return respond({ id: request.id, ok: true, result: {} });
      if (first) { first = false; setTimeout(() => respond({ id: request.id, ok: true, result: { slow: true } }), 20); }
      else respond({ id: request.id, ok: true, result: { fast: true } });
    },
  });
  const backend = new RagBackend(CFG, { spawnImpl: () => child });
  const [slow, fast] = await Promise.all([backend.request('search', {}), backend.request('find_symbol', {})]);
  assert.deepEqual(slow.result, { slow: true });
  assert.deepEqual(fast.result, { fast: true });
  await backend.dispose();
});

test('超时返回 TIMEOUT 并杀掉子进程', async () => {
  let killed = false;
  const child = createFakeChild({
    onRequest: (request, respond) => { if (request.method === 'ping') respond({ id: request.id, ok: true, result: {} }); },
    onKill: () => { killed = true; },
  });
  const backend = new RagBackend(CFG, { spawnImpl: () => child });
  await assert.rejects(
    () => backend.request('search', {}, { timeoutMs: 30 }),
    (err) => err instanceof BackendError && err.code === 'TIMEOUT',
  );
  assert.equal(killed, true);
});

test('崩溃后下一次请求自动重启', async () => {
  let spawns = 0;
  const backend = new RagBackend(CFG, {
    backoff: () => 0,
    spawnImpl: () => {
      spawns += 1;
      const child = okChild();
      if (spawns === 1) setTimeout(() => child.emit('exit', 1, null), 5);
      return child;
    },
  });
  await backend.request('ping');
  await new Promise((resolve) => setTimeout(resolve, 30));
  await backend.request('ping');
  assert.equal(spawns, 2);
  await backend.dispose();
});

test('连续失败进入 failed 并返回 BACKEND_FAILED', async () => {
  const backend = new RagBackend(CFG, {
    backoff: () => 0,
    spawnImpl: () => {
      const child = createFakeChild({ onRequest: () => {} });
      setTimeout(() => child.emit('error', new Error('boom')), 1);
      return child;
    },
  });
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await backend.request('ping').catch(() => {});
  }
  await assert.rejects(
    () => backend.request('ping'),
    (err) => err instanceof BackendError && ['BACKEND_FAILED', 'SPAWN_FAILED', 'START_FAILED'].includes(err.code),
  );
});

test('dispose 拒绝在途请求并终止子进程', async () => {
  let killed = false;
  const child = createFakeChild({
    onRequest: (request, respond) => { if (request.method === 'ping') respond({ id: request.id, ok: true, result: {} }); },
    onKill: () => { killed = true; },
  });
  const backend = new RagBackend(CFG, { spawnImpl: () => child, sleep: async () => {} });
  const pending = backend.request('search', {});
  await backend.dispose();
  await assert.rejects(pending, (err) => err instanceof BackendError);
  assert.equal(killed, true);
});
