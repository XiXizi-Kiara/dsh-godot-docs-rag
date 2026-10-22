// 日志行入口（默认 disabled）：被动监听工具事件、写 JSONL、注册反馈命令。
import { diagnosticMessage, resolveLogConfig } from '../lib/config.js';
import { UsageLog } from '../lib/usage-log.js';
import { createFeedback } from '../lib/feedback.js';

const COMMANDS = [
  { name: 'godot-rag-good', rating: 'good', description: '记录一次满意的 Godot 文档检索（可带备注）' },
  { name: 'godot-rag-bad', rating: 'bad', description: '记录一次不满意的 Godot 文档检索（可带备注）' },
];

export function apply(ctx, config = {}) {
  const resolved = resolveLogConfig(config || {});
  if (!resolved.ok) {
    console.error(`[godot-docs-rag/usage-log] ${diagnosticMessage(resolved.diagnostics, 'godot-docs-rag-usage-log')}`);
    return;
  }
  const cfg = resolved.config;
  const log = new UsageLog(cfg);
  const feedback = createFeedback();
  const starts = new Map();

  ctx.effect(() => ctx.on('tools/pre-execute', (exec, next) => {
    if (String(exec.name).startsWith(cfg.toolPrefix)) starts.set(exec.callId, Date.now());
    return next();
  }));

  ctx.effect(() => ctx.on('tools/result', (exec, result) => {
    if (!String(exec.name).startsWith(cfg.toolPrefix)) return;
    const started = starts.get(exec.callId);
    starts.delete(exec.callId);
    feedback.remember(exec);
    log.recordToolCall(exec, result, started === undefined ? null : Date.now() - started);
  }));

  const commands = typeof ctx.get === 'function' ? ctx.get('commands') : undefined;
  if (commands) {
    for (const { name, rating, description } of COMMANDS) {
      ctx.effect(() => commands.register({
        name,
        description,
        input: { hint: '可选备注：哪里好 / 哪里不对' },
        handler: (invocation) => {
          const note = String(invocation.rawInput || '').trim();
          const event = feedback.build({
            sessionId: invocation.agent?.id,
            rating,
            note,
            command: name,
          });
          log.recordFeedback(event);
          return {
            kind: 'success',
            text: `已记录（${rating}）${event.refs.length ? `，关联最近 ${event.refs.length} 次查询` : '，本会话暂无查询记录'}`,
          };
        },
      }));
    }
  }

  ctx.effect(() => () => log.close());
  return COMMANDS.map((item) => item.name);
}
