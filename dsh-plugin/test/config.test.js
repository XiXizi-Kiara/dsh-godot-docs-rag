import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  deriveProjectDir, looksLikeProject, resolveConfig, resolveLogConfig, diagnosticMessage,
} from '../lib/config.js';

function envDir() {
  return mkdtempSync(path.join(tmpdir(), 'godot-rag-env-'));
}

function sandboxPython() {
  const dir = mkdtempSync(path.join(tmpdir(), 'godot-rag-cfg-'));
  const exe = path.join(dir, 'python.exe');
  writeFileSync(exe, '');
  return { dir, exe };
}

test('显式配置优先于环境变量与推导值', () => {
  const { dir, exe } = sandboxPython();
  const env = { GODOT_RAG_PYTHON: 'env-python', GODOT_RAG_PROJECT: envDir() };
  const result = resolveConfig({ pythonExe: exe, projectDir: dir }, env);
  assert.equal(result.ok, true);
  assert.equal(result.config.pythonExe, exe);
  assert.equal(result.config.projectDir, dir);
  assert.equal(result.config.configSource.pythonExe, 'row-config');
});

test('环境变量兜底并夹取数值', () => {
  const { dir, exe } = sandboxPython();
  const result = resolveConfig(
    { defaultK: 999, toolTimeoutMs: 1 },
    { GODOT_RAG_PYTHON: exe, GODOT_RAG_PROJECT: dir },
  );
  assert.equal(result.ok, true);
  assert.equal(result.config.defaultK, 20);
  assert.equal(result.config.toolTimeoutMs, 5000);
  assert.equal(result.config.configSource.pythonExe, 'env');
});

test('缺少 pythonExe 时给出可操作诊断与 profile patch 片段', () => {
  const { dir } = sandboxPython();
  const result = resolveConfig(
    { projectDir: dir },
    { GODOT_RAG_PYTHON: '', GODOT_RAG_PROJECT: dir },
    { defaults: false },
  );
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].key, 'pythonExe');
  assert.match(diagnosticMessage(result.diagnostics, 'godot-docs-rag'), /pythonExe/);
  assert.match(diagnosticMessage(result.diagnostics, 'godot-docs-rag'), /<PYTHON_EXE>/);
});

test('非法 mode 与不存在的路径给出诊断', () => {
  const { dir, exe } = sandboxPython();
  const result = resolveConfig({ pythonExe: exe, projectDir: dir, defaultMode: 'nope' });
  assert.equal(result.ok, false);
  assert.ok(result.diagnostics.some((d) => d.key === 'defaultMode'));
  const missing = resolveConfig({ pythonExe: path.join(dir, 'nope.exe'), projectDir: dir });
  assert.equal(missing.ok, false);
  assert.ok(missing.diagnostics.some((d) => d.key === 'pythonExe'));
});

test('日志行配置默认跟随项目目录', () => {
  const { dir } = sandboxPython();
  const result = resolveLogConfig({ projectDir: dir }, {});
  assert.equal(result.ok, true);
  assert.equal(result.config.logPath, path.join(dir, 'logs', 'rag_usage.jsonl'));
  assert.equal(result.config.maxFiles, 5);
  assert.equal(result.config.redactQueries, false);
});

test('looksLikeProject 只认 GodotRag 仓库特征', () => {
  assert.equal(looksLikeProject(deriveProjectDir()), true);
  const empty = mkdtempSync(path.join(tmpdir(), 'godot-rag-not-project-'));
  assert.equal(looksLikeProject(empty), false);
  assert.equal(looksLikeProject(''), false);
});

test('从 node_modules 安装时推导失败，要求显式 projectDir', () => {
  const notProject = mkdtempSync(path.join(tmpdir(), 'godot-rag-node-modules-'));
  const result = resolveConfig({}, {}, { deriveProjectDir: () => notProject });
  assert.equal(result.ok, false);
  const diagnostic = result.diagnostics.find((d) => d.key === 'projectDir');
  assert.ok(diagnostic, '应给出 projectDir 诊断');
  assert.match(diagnostic.hint, /GODOT_RAG_PROJECT|projectDir/);
});

test('推导命中仓库时使用默认 projectDir', () => {
  const result = resolveConfig({ pythonExe: process.execPath }, {}, { defaults: true });  assert.equal(result.ok, true);
  assert.equal(result.config.projectDir, deriveProjectDir());
  assert.equal(result.config.configSource.projectDir, 'default');
});

