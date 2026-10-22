import { EventEmitter } from 'node:events';

export function createFakeChild({ onRequest, onKill } = {}) {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdout.setEncoding = () => {};
  child.stderr.setEncoding = () => {};
  child.stdin = {
    writable: true,
    buffer: '',
    write(chunk) {
      this.buffer += chunk;
      const lines = this.buffer.split('\n');
      this.buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim()) continue;
        const request = JSON.parse(line);
        const respond = (response) => {
          queueMicrotask(() => child.stdout.emit('data', JSON.stringify(response) + '\n'));
        };
        if (onRequest) onRequest(request, respond, child);
      }
      return true;
    },
    end() {
      this.writable = false;
      // 故意不自动退出：让 dispose() 走 kill 兜底路径（真实进程会因 EOF 退出）
    },
  };
  child.kill = (signal = 'SIGTERM') => {
    if (onKill) onKill(signal);
    child.signalCode = signal;
    queueMicrotask(() => child.emit('exit', null, signal));
    return true;
  };
  return child;
}
