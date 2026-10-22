// scripts/probe_bridge.mjs
// M0 冒烟：验证能否用管道 spawn python 并完成一次 UTF-8 JSON 往返。
// 在受限沙箱内会失败（EPERM/WinError 5），属预期；请在普通终端运行。
import { spawn } from 'node:child_process';

const python = process.env.GODOT_RAG_PYTHON || 'python';
const pyCode = `
import json, sys
line = sys.stdin.buffer.readline().decode('utf-8')
req = json.loads(line)
resp = {"id": req.get("id"), "ok": True,
        "result": {"pong": req["params"]["echo"], "py": sys.executable}}
sys.stdout.buffer.write((json.dumps(resp, ensure_ascii=False) + "\\n").encode("utf-8"))
sys.stdout.buffer.flush()
`;

let child;
try {
  child = spawn(python, ['-c', pyCode], { stdio: ['pipe', 'pipe', 'pipe'] });
} catch (err) {
  // 受限沙箱会在此同步抛出 EPERM：属于环境限制，不是脚本缺陷
  console.error(`[probe] FAIL spawn: ${err.code || err.message}（沙箱限制；请在普通终端运行）`);
  process.exit(2);
}
let out = '';
child.stdout.on('data', (d) => { out += d.toString('utf8'); });
child.on('error', (err) => { console.error(`[probe] FAIL spawn: ${err.code || err.message}`); process.exit(2); });
child.on('exit', (code) => {
  if (code !== 0) { console.error(`[probe] FAIL exit=${code}`); process.exit(2); }
  const line = out.trim().split('\n').pop() || '';
  try {
    const resp = JSON.parse(line);
    if (resp.ok && resp.result.pong === '中文探针 ✓') {
      console.log('[probe] PASS', JSON.stringify(resp.result));
      process.exit(0);
    }
    console.error('[probe] FAIL bad response', line);
    process.exit(2);
  } catch (err) {
    console.error('[probe] FAIL bad json', line, err.message);
    process.exit(2);
  }
});
child.stdin.end(JSON.stringify({ id: 1, method: 'ping', params: { echo: '中文探针 ✓' } }) + '\n');
