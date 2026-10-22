import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { apply } from '../index.js';
import { TOOL_DEFS } from '../lib/tools.js';

function fakeContext() {
  const registered = [];
  return {
    registered,
    ctx: {
      tools: { register(definition) { registered.push(definition); return () => {}; } },
      effect(fn) { fn(); return () => {}; },
    },
  };
}

// 用真实存在的文件/目录构造有效配置（不写死机器路径）
const PROJECT_DIR = mkdtempSync(path.join(tmpdir(), 'godot-rag-project-'));
const CFG = {
  pythonExe: process.execPath, projectDir: PROJECT_DIR,
  indexDir: path.join(PROJECT_DIR, 'data', 'index'),
  defaultK: 5, defaultMode: 'hybrid', device: 'auto', prewarm: false,
  toolTimeoutMs: 120000, docsBaseUrl: 'https://docs.godotengine.org/en/stable/',
};

function fakeBackend(result, meta = { pid: 1, device: 'cuda', rag_version: '0.2.0' }) {
  return {
    meta,
    async request() { return { result, meta }; },
    async warmupInBackground() {},
    async dispose() {},
  };
}

test('apply 注册 4 个固定名称的工具', () => {
  const { ctx, registered } = fakeContext();
  apply(ctx, CFG);
  assert.deepEqual(registered.map((tool) => tool.name), [
    'godot_search_docs', 'godot_find_symbol', 'godot_get_doc', 'godot_rag_stats',
  ]);
  assert.equal(TOOL_DEFS.length, 4);
});

test('搜索工具渲染标题、URL 与片段', async () => {
  const { ctx, registered } = fakeContext();
  const result = {
    query: 'add_child', mode: 'hybrid', k: 3,
    results: [{ rank: 1, score: 0.02, title: 'Node', symbol: 'Node.add_child',
      breadcrumbs: ['Godot Docs', 'Class reference', 'Node'],
      url: 'https://docs.godotengine.org/en/stable/classes/class_node.html',
      text: 'Adds a child node.' }],
  };
  const backend = fakeBackend(result);
  apply(ctx, CFG, { backend });
  const search = registered.find((tool) => tool.name === 'godot_search_docs');
  const value = await search.execute({ query: 'add_child', k: 3 });
  assert.equal(value.backend.pid, 1);
  const blocks = search.output.render({}, value);
  assert.match(blocks[0].text, /Node/);
  assert.match(blocks[0].text, /class_node\.html/);
  assert.equal(blocks[0].type, 'text');
});

test('配置无效时工具抛出 CONFIG_INVALID 诊断', async () => {
  const { ctx, registered } = fakeContext();
  apply(
    ctx,
    { pythonExe: path.join(PROJECT_DIR, 'no-such-python'), projectDir: path.join(PROJECT_DIR, 'no-such-dir') },
    { env: {} },
  );
  const search = registered.find((tool) => tool.name === 'godot_search_docs');
  await assert.rejects(
    () => search.execute({ query: 'x' }),
    (err) => /pythonExe/.test(err.message) && /<PYTHON_EXE>/.test(err.message),
  );
});

test('后端错误被转成可读 message', async () => {
  const { ctx, registered } = fakeContext();
  const backend = {
    meta: null,
    async request() { const err = new Error('索引尚未构建'); err.code = 'INDEX_NOT_READY'; err.hint = 'scripts\\run.py build'; throw err; },
    async warmupInBackground() {}, async dispose() {},
  };
  apply(ctx, CFG, { backend });
  const stats = registered.find((tool) => tool.name === 'godot_rag_stats');
  await assert.rejects(() => stats.execute({}), (err) => /INDEX_NOT_READY/.test(err.message) && /scripts/.test(err.message));
});
