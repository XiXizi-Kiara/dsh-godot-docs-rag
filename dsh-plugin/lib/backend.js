// 子进程生命周期 + JSONL 协议客户端。只用 Node 标准库，便于单测注入假 spawn。
import { spawn } from 'node:child_process';
import path from 'node:path';

const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 30000;
const MAX_RESTARTS = 5;
const START_TIMEOUT_MS = 30000;
const STDERR_TAIL_BYTES = 8192;

export class BackendError extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.name = 'BackendError';
    this.code = code;
    this.hint = options.hint || '';
    this.stderr = options.stderr || '';
  }
}

export class RagBackend {
  constructor(cfg, options = {}) {
    this.cfg = cfg;
    this.spawnImpl = options.spawnImpl || spawn;
    this.sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.backoff = options.backoff
      || ((attempt) => Math.min(BACKOFF_BASE_MS * 2 ** (attempt - 1), BACKOFF_MAX_MS));
    this._state = 'idle';
    this._child = null;
    this._seq = 0;
    this._pending = new Map();
    this._buffer = '';
    this._stderr = '';
    this._meta = null;
    this._attempts = 0;
    this._starting = null;
    this._closed = false;
  }

  get meta() { return this._meta; }

  async request(method, params = {}, options = {}) {
    if (this._closed) throw new BackendError('DISPOSED', '插件已卸载');
    await this._ensureStarted();
    return this._send(method, params, options.timeoutMs || this.cfg.toolTimeoutMs);
  }

  async warmupInBackground() {
    try {
      await this.request('warmup', {}, { timeoutMs: Math.max(this.cfg.toolTimeoutMs, 180000) });
    } catch (err) {
      console.error(`[godot-docs-rag] warmup 失败：${err.message}`);
    }
  }

  async dispose() {
    this._closed = true;
    if (!this._child) return;
    const child = this._child;
    this._child = null;
    this._state = 'idle';
    this._failAll(new BackendError('DISPOSED', '插件已卸载'));
    try { child.stdin.end(); } catch { /* 已关闭 */ }
    const exited = new Promise((resolve) => child.once('exit', resolve));
    await Promise.race([exited, this.sleep(2000)]);
    if (child.exitCode === null && child.signalCode === null) {
      try { child.kill(); } catch { /* 已退出 */ }
      await Promise.race([exited, this.sleep(2000)]);
    }
    if (child.exitCode === null && child.signalCode === null) {
      try { child.kill('SIGKILL'); } catch { /* 已退出 */ }
    }
  }

  _ensureStarted() {
    if (this._state === 'ready') return Promise.resolve();
    if (this._starting) return this._starting;
    if (this._attempts >= MAX_RESTARTS) {
      return Promise.reject(new BackendError('BACKEND_FAILED', 'Python 桥连续启动失败，已停止重试', {
        hint: '检查 pythonExe / projectDir / 索引是否存在',
        stderr: this._stderrTail(),
      }));
    }
    this._starting = (async () => {
      if (this._attempts > 0) await this.sleep(this.backoff(this._attempts));
      this._attempts += 1;
      try { await this._spawn(); } finally { this._starting = null; }
    })();
    return this._starting;
  }

  async _spawn() {
    this._state = 'starting';
    const child = this.spawnImpl(
      this.cfg.pythonExe,
      ['-m', 'godot_rag.dsh_bridge', '--index-dir', this.cfg.indexDir, '--device', this.cfg.device, '--docs-base', this.cfg.docsBaseUrl],
      { cwd: this.cfg.projectDir, env: this._env(), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true },
    );
    this._child = child;
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => this._onData(chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => { this._stderr = (this._stderr + chunk).slice(-STDERR_TAIL_BYTES); });
    child.on('error', (err) => {
      this._failAll(new BackendError('SPAWN_FAILED', `无法启动 Python 进程：${err.message}`, {
        hint: '检查 pythonExe 路径与权限', stderr: this._stderrTail(),
      }));
    });
    child.on('exit', (code, signal) => this._onExit(code, signal));
    try {
      await this._send('ping', {}, START_TIMEOUT_MS);
      this._state = 'ready';
      this._attempts = 0;
    } catch (err) {
      this._kill();
      this._state = 'crashed';
      throw err instanceof BackendError
        ? err
        : new BackendError('START_FAILED', `Python 桥启动失败：${err.message}`, { stderr: this._stderrTail() });
    }
  }

  _send(method, params, timeoutMs) {
    const child = this._child;
    if (!child || !child.stdin || child.stdin.writable === false) {
      return Promise.reject(new BackendError('NOT_RUNNING', 'Python 桥未运行'));
    }
    const id = ++this._seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        this._kill();
        this._state = 'crashed';
        reject(new BackendError('TIMEOUT', `调用 ${method} 超时（${timeoutMs}ms）`, {
          hint: '首次加载可能较慢；进程已重启，可重试一次', stderr: this._stderrTail(),
        }));
      }, timeoutMs);
      this._pending.set(id, { resolve, reject, timer, method });
      try {
        child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
      } catch (err) {
        clearTimeout(timer);
        this._pending.delete(id);
        reject(new BackendError('WRITE_FAILED', err.message));
      }
    });
  }

  _onData(chunk) {
    this._buffer += chunk;
    let index = this._buffer.indexOf('\n');
    while (index >= 0) {
      const line = this._buffer.slice(0, index).trim();
      this._buffer = this._buffer.slice(index + 1);
      if (line) this._onLine(line);
      index = this._buffer.indexOf('\n');
    }
  }

  _onLine(line) {
    let response;
    try { response = JSON.parse(line); } catch { return; }
    if (response.meta) this._meta = response.meta;
    const pending = this._pending.get(response.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this._pending.delete(response.id);
    if (response.ok) {
      pending.resolve({ result: response.result, meta: response.meta ?? this._meta });
    } else {
      pending.reject(new BackendError(
        response.error?.code || 'BACKEND_ERROR',
        response.error?.message || 'Python 桥返回错误',
        { hint: response.error?.hint || '', stderr: this._stderrTail() },
      ));
    }
  }

  _onExit(code, signal) {
    if (this._closed) return;
    this._child = null;
    this._state = code === 0 ? 'idle' : 'crashed';
    if (code !== 0) this._attempts = Math.max(this._attempts, 1);
    this._failAll(new BackendError('BACKEND_EXITED', `Python 桥退出（code=${code} signal=${signal}）`, {
      stderr: this._stderrTail(),
    }));
  }

  _failAll(error) {
    for (const pending of this._pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this._pending.clear();
  }

  _kill() {
    const child = this._child;
    this._child = null;
    if (child) { try { child.kill(); } catch { /* 已退出 */ } }
    this._failAll(new BackendError('KILLED', 'Python 桥被终止'));
  }

  _stderrTail() { return this._stderr.slice(-1500); }

  _env() {
    const env = {};
    for (const key of ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE']) {
      if (process.env[key] !== undefined) env[key] = process.env[key];
    }
    env.PYTHONPATH = path.join(this.cfg.projectDir, 'src');
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONUTF8 = '1';
    env.PYTHONUNBUFFERED = '1';
    return env;
  }
}
