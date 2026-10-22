// JSONL 使用日志：字段裁剪、异步写入、轮转；写失败不影响主流程。
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

function isoNow() { return new Date().toISOString(); }

function truncate(value, limit = 2000) {
  const text = String(value ?? '');
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

function queryView(query, redact) {
  if (!redact) return { query: truncate(query) };
  const text = String(query ?? '');
  return {
    query_sha256: createHash('sha256').update(text).digest('hex'),
    query_chars: text.length,
  };
}

function trimArgs(tool, args) {
  const safe = args && typeof args === 'object' ? args : {};
  if (tool === 'godot_get_doc') {
    return {
      doc_id: truncate(safe.doc_id, 200),
      offset: safe.offset ?? 0,
      limit: safe.limit ?? null,
    };
  }
  return safe;
}

function summarizeResults(tool, value) {
  if (!value || typeof value !== 'object') return [];
  if (Array.isArray(value.results)) {
    return value.results.slice(0, 20).map((item) => ({
      rank: item.rank, doc_id: item.doc_id, chunk_id: item.chunk_id,
      symbol: item.symbol || '', score: item.score,
    }));
  }
  if (Array.isArray(value.matches)) {
    return value.matches.slice(0, 20).map((item) => ({
      rank: item.rank, doc_id: item.doc_id || '', chunk_id: item.chunk_id || '',
      symbol: item.symbol || '', kind: item.kind || '',
    }));
  }
  if (tool === 'godot_get_doc') {
    return [{ rank: 1, doc_id: value.doc_id, chunk_id: '', chars: value.total_chars }];
  }
  return [];
}

export class UsageLog {
  constructor(cfg, options = {}) {
    this.cfg = cfg;
    this.now = options.now || isoNow;
    this.failures = 0;
    this._queue = Promise.resolve();
    this._closed = false;
    this._bytes = 0;
    this._checked = false;
  }

  recordToolCall(exec, result, latencyMs) {
    const failed = Boolean(result?.isError);
    const value = failed ? null : result?.value;
    const rawArgs = { ...trimArgs(exec?.name, exec?.arguments) };
    if (exec?.name === 'godot_search_docs' && this.cfg.redactQueries) delete rawArgs.query;
    const event = {
      v: 1,
      kind: 'tool_call',
      ts: this.now(),
      session_id: exec?.agent?.id ?? null,
      call_id: exec?.callId ?? null,
      tool: exec?.name ?? '',
      args: {
        ...rawArgs,
        ...(exec?.name === 'godot_search_docs'
          ? queryView(exec?.arguments?.query, this.cfg.redactQueries)
          : {}),
      },
      status: failed ? 'error' : 'ok',
      error_code: failed ? (result?.error?.info?.code || 'TOOL_ERROR') : null,
      latency_ms: latencyMs,
      result_count: summarizeResults(exec?.name, value).length,
      results: summarizeResults(exec?.name, value),
      backend: value?.backend ?? null,
    };
    if (failed) delete event.args.query; // 失败调用也不需要原文；保持结构一致
    this._enqueue(event);
  }

  recordFeedback({ sessionId, rating, note, command, refs }) {
    this._enqueue({
      v: 1,
      kind: 'feedback',
      ts: this.now(),
      session_id: sessionId ?? null,
      rating,
      note: truncate(note, 2000),
      command,
      refs: Array.isArray(refs) ? refs.slice(0, 3) : [],
    });
  }

  async close() {
    this._closed = true;
    await this._queue;
  }

  _enqueue(event) {
    const line = `${JSON.stringify(event)}\n`;
    this._queue = this._queue
      .then(() => this._write(line))
      .catch(() => { /* 已在 _write 内计数 */ });
  }

  async _write(line) {
    try {
      await fs.mkdir(path.dirname(this.cfg.logPath), { recursive: true });
      if (!this._checked) {
        this._checked = true;
        this._bytes = await this._size();
      }
      if (this._bytes > 0 && this._bytes + line.length > this.cfg.maxBytes) {
        await this._rotate();
      }
      await fs.appendFile(this.cfg.logPath, line, 'utf8');
      this._bytes += line.length;
    } catch (err) {
      this.failures += 1;
      if (this.failures === 1) {
        console.error(`[godot-docs-rag/usage-log] 写入失败（后续同类错误不再提示）：${err.message}`);
      }
    }
  }

  async _size() {
    try { return (await fs.stat(this.cfg.logPath)).size; } catch { return 0; }
  }

  async _rotate() {
    const dir = path.dirname(this.cfg.logPath);
    const base = path.basename(this.cfg.logPath, '.jsonl');
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '');
    await fs.rename(this.cfg.logPath, path.join(dir, `${base}.${stamp}.jsonl`)).catch(() => {});
    const all = (await fs.readdir(dir)).filter((name) => name.startsWith(base)).sort();
    const excess = all.length - this.cfg.maxFiles;
    for (let index = 0; index < excess; index += 1) {
      await fs.unlink(path.join(dir, all[index])).catch(() => {});
    }
    this._bytes = 0;
  }
}
