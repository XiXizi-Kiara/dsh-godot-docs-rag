import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { UsageLog } from '../lib/usage-log.js';

function tempLogPath() {
  const dir = mkdtempSync(path.join(tmpdir(), 'godot-rag-log-'));
  return path.join(dir, 'logs', 'rag_usage.jsonl');
}

function execOf(name, args) {
  return { name, callId: 'call_1', arguments: args, agent: { id: 'session_1' } };
}

function resultOf(value, isError = false) {
  return isError
    ? { isError: true, content: [], error: { message: 'x', info: { code: 'INDEX_NOT_READY' } } }
    : { isError: false, value, content: [] };
}

async function readLines(file) {
  const text = await fs.readFile(file, 'utf8');
  return text.trim().split('\n').map((line) => JSON.parse(line));
}

test('记录 tool_call：字段齐全且不含正文', async () => {
  const logPath = tempLogPath();
  const log = new UsageLog({ logPath, maxBytes: 1024 * 1024, maxFiles: 5, redactQueries: false, toolPrefix: 'godot_' });
  log.recordToolCall(
    execOf('godot_search_docs', { query: '节点', k: 5, mode: 'hybrid' }),
    resultOf({ query: '节点', results: [
      { rank: 1, doc_id: 'classes/class_node', chunk_id: 'c1', symbol: 'Node.add_child', score: 0.02, text: 'long body'.repeat(100) },
    ], backend: { pid: 1 } }),
    123,
  );
  await log.close();
  const [event] = await readLines(logPath);
  assert.equal(event.v, 1);
  assert.equal(event.kind, 'tool_call');
  assert.equal(event.tool, 'godot_search_docs');
  assert.equal(event.latency_ms, 123);
  assert.equal(event.results[0].doc_id, 'classes/class_node');
  assert.equal(event.results[0].text, undefined);
  assert.equal(event.backend.pid, 1);
});

test('错误调用记录 error_code，且 results 最多 20 条', async () => {
  const logPath = tempLogPath();
  const log = new UsageLog({ logPath, maxBytes: 1024 * 1024, maxFiles: 5, redactQueries: false, toolPrefix: 'godot_' });
  const results = Array.from({ length: 30 }, (_, index) => ({ rank: index + 1, doc_id: `d${index}`, chunk_id: `c${index}` }));
  log.recordToolCall(execOf('godot_search_docs', { query: 'q' }), resultOf({ results }), 5);
  log.recordToolCall(execOf('godot_search_docs', { query: 'q' }), resultOf(null, true), 5);
  await log.close();
  const [ok, failed] = await readLines(logPath);
  assert.equal(ok.results.length, 20);
  assert.equal(failed.status, 'error');
  assert.equal(failed.error_code, 'INDEX_NOT_READY');
});

test('redactQueries 只记录哈希与长度', async () => {
  const logPath = tempLogPath();
  const log = new UsageLog({ logPath, maxBytes: 1024 * 1024, maxFiles: 5, redactQueries: true, toolPrefix: 'godot_' });
  log.recordToolCall(execOf('godot_search_docs', { query: 'secret query' }), resultOf({ results: [] }), 1);
  await log.close();
  const [event] = await readLines(logPath);
  assert.equal(event.args.query, undefined);
  assert.equal(event.args.query_sha256.length, 64);
  assert.equal(event.args.query_chars, 12);
});

test('超过 maxBytes 触发轮转且只保留 maxFiles 份', async () => {
  const logPath = tempLogPath();
  const log = new UsageLog({ logPath, maxBytes: 600, maxFiles: 2, redactQueries: false, toolPrefix: 'godot_' });
  for (let index = 0; index < 20; index += 1) {
    log.recordToolCall(execOf('godot_search_docs', { query: `q${index}` }), resultOf({ results: [] }), 1);
  }
  await log.close();
  const dir = path.dirname(logPath);
  const files = (await fs.readdir(dir)).filter((name) => name.startsWith('rag_usage'));
  assert.ok(files.length <= 2, `files=${files.join(',')}`);
});

test('写盘失败不抛异常，只增加失败计数', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'godot-rag-bad-'));
  const notADir = path.join(dir, 'blocked');
  await fs.writeFile(notADir, 'x');
  const log = new UsageLog({ logPath: path.join(notADir, 'x.jsonl'), maxBytes: 1024, maxFiles: 2, redactQueries: false, toolPrefix: 'godot_' });
  log.recordToolCall(execOf('godot_search_docs', { query: 'q' }), resultOf({ results: [] }), 1);
  await log.close();
  assert.equal(log.failures, 1);
});
