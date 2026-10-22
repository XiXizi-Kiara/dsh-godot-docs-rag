// 工具行入口：解析配置 → 注册 4 个工具 → 管理子进程。
import { diagnosticMessage, resolveConfig } from './lib/config.js';
import { RagBackend } from './lib/backend.js';
import { makeTool, TOOL_DEFS } from './lib/tools.js';

export const inject = ['tools'];

export function apply(ctx, config = {}, options = {}) {
  const resolved = options.resolved || resolveConfig(config || {}, options.env || process.env);
  if (!resolved.ok) {
    console.error(`[godot-docs-rag] ${diagnosticMessage(resolved.diagnostics, 'godot-docs-rag')}`);
  }
  const backend = options.backend || (resolved.ok ? new RagBackend(resolved.config) : null);
  const context = {
    cfg: resolved.config,
    ok: resolved.ok,
    diagnostics: resolved.diagnostics,
    backend,
  };
  for (const def of TOOL_DEFS) {
    ctx.effect(() => ctx.tools.register(makeTool(def, context)));
  }
  if (resolved.ok && resolved.config.prewarm && backend) {
    backend.warmupInBackground();
  }
  ctx.effect(() => () => backend && backend.dispose());
  return TOOL_DEFS.map((def) => def.name);
}
