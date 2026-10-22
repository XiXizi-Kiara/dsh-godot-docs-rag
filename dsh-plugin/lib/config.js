// 配置解析：行 config → 环境变量 → 可移植默认值 → CONFIG_INVALID 诊断。
import { existsSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const MODES = ['hybrid', 'dense', 'bm25', 'hybrid+rerank', 'dense+rerank'];

const HERE = path.dirname(fileURLToPath(import.meta.url)); // dsh-plugin/lib

export function deriveProjectDir() {
  return path.resolve(HERE, '..', '..'); // 仓库根（目录安装时）
}

// 发布版从 node_modules 安装时，上面的推导会指向错误目录；
// 只有推导结果确实像 GodotRag 仓库时才采用，否则要求显式配置。
export function looksLikeProject(dir) {
  if (!dir) return false;
  try {
    return (
      existsSync(path.join(dir, 'src', 'godot_rag'))
      || existsSync(path.join(dir, 'data', 'index', 'meta.json'))
    );
  } catch {
    return false;
  }
}

function defaultPythonExe(platform = process.platform) {
  return platform === 'win32'
    ? path.join(homedir(), '.conda', 'envs', 'godotrag', 'python.exe')
    : path.join(homedir(), '.conda', 'envs', 'godotrag', 'bin', 'python');
}

function clamp(value, min, max, fallback) {
  const num = Number(value);
  return Number.isFinite(num) ? Math.min(Math.max(num, min), max) : fallback;
}

function pathLike(value) {
  return typeof value === 'string' && value.trim() !== '' ? path.resolve(value) : '';
}

function pick(rawValue, env, envKey, fallbackFn, diagnostics, key) {
  if (rawValue !== undefined && rawValue !== null && rawValue !== '') {
    return { value: rawValue, source: 'row-config' };
  }
  const fromEnv = env[envKey];
  if (typeof fromEnv === 'string' && fromEnv.trim() !== '') {
    return { value: fromEnv, source: 'env' };
  }
  if (fallbackFn) {
    return { value: fallbackFn(), source: 'default' };
  }
  diagnostics.push({
    key,
    message: `缺少配置 ${key}（行 config 与环境变量 ${envKey} 都未提供）`,
    hint: `在 profile 的 cordis.patch.yml 追加 ${key}`,
  });
  return { value: '', source: 'missing' };
}

export function resolveConfig(raw = {}, env = process.env, options = {}) {
  const useDefaults = options.defaults !== false;
  const derive = options.deriveProjectDir || deriveProjectDir;
  const diagnostics = [];
  const python = pick(raw.pythonExe, env, 'GODOT_RAG_PYTHON',
    useDefaults ? () => defaultPythonExe() : null, diagnostics, 'pythonExe');

  const explicitProject = typeof raw.projectDir === 'string' && raw.projectDir.trim() !== ''
    || typeof env.GODOT_RAG_PROJECT === 'string' && env.GODOT_RAG_PROJECT.trim() !== '';
  let project;
  if (!explicitProject && useDefaults) {
    const derived = derive();
    if (looksLikeProject(derived)) {
      project = { value: derived, source: 'default' };
    } else {
      project = { value: '', source: 'missing' };
      diagnostics.push({
        key: 'projectDir',
        message: `无法自动推导 GodotRag 项目目录（${derived} 不像本项目）`,
        hint: '发布版请显式配置 projectDir，或设置环境变量 GODOT_RAG_PROJECT 指向仓库根目录',
      });
    }
  } else {
    project = pick(raw.projectDir, env, 'GODOT_RAG_PROJECT', null, diagnostics, 'projectDir');
  }

  let index = pick(raw.indexDir, env, 'GODOT_RAG_INDEX_DIR', null, diagnostics, 'indexDir');
  // indexDir 可选：没有时用 <projectDir>/data/index，不应报缺失
  if (index.source === 'missing') {
    index = {
      value: project.value ? path.join(project.value, 'data', 'index') : '',
      source: 'default',
    };
    diagnostics.pop();
  }

  const pythonExe = pathLike(python.value);
  if (pythonExe && !existsSync(pythonExe)) {
    diagnostics.push({ key: 'pythonExe', message: `pythonExe 不存在：${pythonExe}`, hint: '确认 conda 环境 godotrag 的 python 路径' });
  }
  const projectDir = pathLike(project.value);
  if (projectDir && !(existsSync(projectDir) && statSync(projectDir).isDirectory())) {
    diagnostics.push({ key: 'projectDir', message: `projectDir 不是目录：${projectDir}`, hint: '填写仓库根目录' });
  }
  const mode = String(raw.defaultMode || 'hybrid');
  if (!MODES.includes(mode)) {
    diagnostics.push({ key: 'defaultMode', message: `defaultMode 非法：${mode}`, hint: `可选：${MODES.join(' / ')}` });
  }
  const config = {
    pythonExe,
    projectDir,
    indexDir: index.value ? path.resolve(index.value) : '',
    defaultK: clamp(raw.defaultK, 1, 20, 5),
    defaultMode: MODES.includes(mode) ? mode : 'hybrid',
    device: ['auto', 'cuda', 'cpu'].includes(String(raw.device)) ? String(raw.device) : 'auto',
    prewarm: raw.prewarm !== false,
    toolTimeoutMs: clamp(raw.toolTimeoutMs, 5000, 600000, 120000),
    docsBaseUrl: String(raw.docsBaseUrl || 'https://docs.godotengine.org/en/stable/'),
    configSource: {
      pythonExe: python.source,
      projectDir: project.source,
      indexDir: index.source,
    },
  };
  return { ok: diagnostics.length === 0, config, diagnostics };
}

export function resolveLogConfig(raw = {}, env = process.env) {
  const diagnostics = [];
  const explicit = raw.projectDir || env.GODOT_RAG_PROJECT;
  let projectDir = explicit ? path.resolve(explicit) : '';
  if (!projectDir && !raw.logPath) {
    const derived = deriveProjectDir();
    if (looksLikeProject(derived)) projectDir = derived;
  }
  const logPath = raw.logPath
    ? path.resolve(String(raw.logPath))
    : projectDir
      ? path.join(projectDir, 'logs', 'rag_usage.jsonl')
      : '';
  if (!logPath) {
    diagnostics.push({
      key: 'logPath',
      message: '无法确定日志路径（未配置 projectDir，且无法自动推导项目目录）',
      hint: '配置 projectDir、环境变量 GODOT_RAG_PROJECT，或直接配置 logPath',
    });
  }
  const config = {
    projectDir,
    logPath,
    toolPrefix: String(raw.toolPrefix || 'godot_'),
    maxBytes: clamp(raw.maxBytes, 1024, 1024 * 1024 * 1024, 10 * 1024 * 1024),
    maxFiles: clamp(raw.maxFiles, 1, 50, 5),
    redactQueries: raw.redactQueries === true,
  };
  return { ok: diagnostics.length === 0, config, diagnostics };
}

export function diagnosticMessage(diagnostics, entryId = 'godot-docs-rag') {
  const lines = diagnostics.map((d) => `- ${d.key}: ${d.message}${d.hint ? `（${d.hint}）` : ''}`);
  return [
    'Godot RAG 插件配置无效：',
    ...lines,
    '',
    '修复方式：在 profile 的 cordis.patch.yml 覆盖该行：',
    `- id: ${entryId}`,
    "  name: '@xixizi-kiara/dsh-godot-docs-rag'",
    '  config:',
    "    pythonExe: '<PYTHON_EXE>'",
    "    projectDir: '<PROJECT_DIR>'",
  ].join('\n');
}
