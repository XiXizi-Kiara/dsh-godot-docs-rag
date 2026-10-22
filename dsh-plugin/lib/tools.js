// 4 个 ToolDefinition：描述、参数 schema、结果渲染。
import { diagnosticMessage } from './config.js';

function textBlock(text) { return [{ type: 'text', text }]; }

function fail(message) {
  const error = new Error(message);
  error.code = 'CONFIG_INVALID';
  throw error;
}

const SEARCH = {
  name: 'godot_search_docs',
  method: 'search',
  description:
    'Search the official Godot 4.x documentation (hybrid retrieval over the full docs site). '
    + 'Use when you do not know the exact API symbol or page. Returns titles, breadcrumbs, '
    + 'API symbols, official URLs and excerpts. Follow up with godot_get_doc when you need the full page.',
  parameters: {
    type: 'object',
    additionalProperties: false,
    required: ['query'],
    properties: {
      query: { type: 'string', description: 'Search query in English or Chinese; English usually retrieves better.' },
      k: { type: 'integer', minimum: 1, maximum: 20, description: 'Number of results (default from plugin config).' },
      mode: { type: 'string', enum: ['hybrid', 'dense', 'bm25', 'hybrid+rerank', 'dense+rerank'],
        description: 'Retrieval mode; leave empty to use the configured default.' },
    },
  },
  parseArgs(args, cfg) {
    return {
      query: String(args.query ?? ''),
      k: Math.min(Math.max(Number(args.k) || cfg.defaultK, 1), 20),
      mode: String(args.mode || cfg.defaultMode),
    };
  },
  render(args, value) {
    const lines = [`Godot docs search: ${value.query} (mode=${value.mode})`];
    (value.results || []).forEach((r, index) => {
      lines.push(`\n[${index + 1}] ${r.title}${r.symbol ? ` — ${r.symbol}` : ''}`);
      if (r.breadcrumbs?.length) lines.push(`   path: ${r.breadcrumbs.join(' > ')}`);
      lines.push(`   url: ${r.url}`);
      if (r.text) lines.push(`   ${r.text}`);
    });
    lines.push('\nUse godot_get_doc with a doc_id when the excerpt is not enough.');
    return textBlock(lines.join('\n'));
  },
};

const FIND = {
  name: 'godot_find_symbol',
  method: 'find_symbol',
  description:
    'Look up an exact Godot API symbol (method/property/signal/constant/enum) by name, '
    + 'e.g. "Node.add_child" or "move_and_slide". Use this instead of godot_search_docs '
    + 'when you already know the symbol name. Returns signature-level documentation blocks.',
  parameters: {
    type: 'object',
    additionalProperties: false,
    required: ['name'],
    properties: {
      name: { type: 'string', description: 'Symbol name, bare ("add_child") or qualified ("Node.add_child").' },
      class_name: { type: 'string', description: 'Optional class filter, e.g. "CharacterBody2D".' },
      kind: { type: 'string', enum: ['method', 'property', 'signal', 'constant', 'enum', 'constructor', 'operator', 'annotation', 'theme_item'] },
      limit: { type: 'integer', minimum: 1, maximum: 20 },
    },
  },
  parseArgs(args) {
    return {
      name: String(args.name ?? ''),
      class_name: args.class_name ? String(args.class_name) : undefined,
      kind: args.kind ? String(args.kind) : undefined,
      limit: Math.min(Math.max(Number(args.limit) || 5, 1), 20),
    };
  },
  render(args, value) {
    const lines = [`Godot symbol lookup: ${value.name}${value.approximate ? ' (approximate)' : ''}`];
    (value.matches || []).forEach((m, index) => {
      lines.push(`\n[${index + 1}] ${m.symbol}${m.kind ? ` — ${m.kind}` : ''}`);
      lines.push(`   url: ${m.url}`);
      if (m.text) lines.push(`   ${m.text}`);
    });
    if (!value.matches?.length) lines.push('No symbol matched; try godot_search_docs with a natural-language query.');
    return textBlock(lines.join('\n'));
  },
};

const DOC = {
  name: 'godot_get_doc',
  method: 'get_doc',
  description:
    'Read a full official Godot documentation page by doc_id (e.g. "classes/class_node"). '
    + 'Use after search or symbol lookup when you need complete context. Supports offset/limit for long pages.',
  parameters: {
    type: 'object',
    additionalProperties: false,
    required: ['doc_id'],
    properties: {
      doc_id: { type: 'string', description: 'Document id such as classes/class_node or tutorials/2d/2d_movement.' },
      offset: { type: 'integer', minimum: 0, description: 'Character offset to start from (default 0).' },
      limit: { type: 'integer', minimum: 1, maximum: 20000, description: 'Max characters to return (default 8000).' },
    },
  },
  parseArgs(args) {
    return {
      doc_id: String(args.doc_id ?? ''),
      offset: Math.max(Number(args.offset) || 0, 0),
      limit: Math.min(Math.max(Number(args.limit) || 8000, 1), 20000),
    };
  },
  render(args, value) {
    const lines = [`# ${value.title}`, `doc_id: ${value.doc_id}`, `url: ${value.url}`,
      `chars: ${value.offset}..${value.offset + value.text.length} / ${value.total_chars}`];
    if (value.truncated) lines.push('(truncated; call again with a larger offset to continue)');
    lines.push('', value.text);
    return textBlock(lines.join('\n'));
  },
};

const STATS = {
  name: 'godot_rag_stats',
  method: 'stats',
  description:
    'Report the local Godot docs index status: chunk count, device, embedding model, index mtime, '
    + 'and resolved plugin configuration. Use it to check whether the index is ready before relying on search.',
  parameters: { type: 'object', additionalProperties: false, properties: {} },
  parseArgs() { return {}; },
  render(args, value) {
    const lines = [
      `ready: ${value.ready}`,
      `chunks: ${value.n_chunks ?? 'n/a'} / docs: ${value.n_docs ?? 'n/a'}`,
      `device: ${value.device ?? 'n/a'}`,
      `index: ${value.index_dir}`,
      `index mtime: ${value.index_mtime ?? 'n/a'}`,
      `embed model: ${value.embed_model ?? 'n/a'} / rerank available: ${value.rerank_available}`,
      `config: python=${value.backend?.python_exe ?? 'n/a'} project=${value.backend?.project_dir ?? 'n/a'}`,
    ];
    if (!value.ready) lines.push('Index is not built. Run: python scripts/run.py build');
    return textBlock(lines.join('\n'));
  },
};

export const TOOL_DEFS = [SEARCH, FIND, DOC, STATS];

export function makeTool(def, context) {
  return {
    name: def.name,
    description: def.description,
    parameters: def.parameters,
    timeoutMs: context.cfg.toolTimeoutMs,
    isConcurrencySafe: () => true,
    async execute(args) {
      if (!context.ok) fail(diagnosticMessage(context.diagnostics, 'godot-docs-rag'));
      const params = def.parseArgs(args || {}, context.cfg);
      try {
        const { result, meta } = await context.backend.request(def.method, params, {
          timeoutMs: context.cfg.toolTimeoutMs,
        });
        return {
          ...result,
          backend: {
            ...(meta || {}),
            python_exe: context.cfg?.pythonExe,
            project_dir: context.cfg?.projectDir,
            config_source: context.cfg?.configSource,
          },
        };
      } catch (err) {
        const parts = [`[${err.code || 'BACKEND_ERROR'}] ${err.message}`];
        if (err.hint) parts.push(`hint: ${err.hint}`);
        if (err.stderr) parts.push(`stderr: ${String(err.stderr).slice(-800)}`);
        throw new Error(parts.join(' · '));
      }
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (args, value) => def.render(args, value),
    },
  };
}
