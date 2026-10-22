import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TEXT = new Set(['.js', '.mjs', '.json', '.yml', '.yaml', '.md', '.svg']);
const SKIP = new Set(['node_modules', '.git']);
const PATTERNS = [
  /[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+/,
  /\/Users\/[A-Za-z0-9_.-]+\//,
  /\/home\/[A-Za-z0-9_.-]+\//,
];

function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue;
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) files.push(...walk(full));
    else if (TEXT.has(path.extname(name))) files.push(full);
  }
  return files;
}

test('bundle 内没有机器路径', () => {
  const hits = [];
  for (const file of walk(ROOT)) {
    const text = readFileSync(file, 'utf8');
    for (const pattern of PATTERNS) {
      if (pattern.test(text)) hits.push(`${path.relative(ROOT, file)} ~ ${pattern}`);
    }
  }
  assert.deepEqual(hits, []);
});

test('bundle patch 不携带 config', () => {
  const patch = readFileSync(path.join(ROOT, 'cordis.patch.yml'), 'utf8');
  assert.ok(!/^\s*config:/m.test(patch), 'patch 不应包含 config 块');
  assert.match(patch, /godot-docs-rag-usage-log/);
  assert.match(patch, /disabled:\s*true/);
});
