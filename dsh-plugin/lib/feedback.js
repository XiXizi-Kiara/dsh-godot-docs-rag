// 反馈命令的纯逻辑：会话最近调用环形缓冲 + feedback 事件体。
const MAX_RECENT = 10;
const MAX_REFS = 3;
const WINDOW_MS = 10 * 60 * 1000;

export function createFeedback({ now = () => Date.now() } = {}) {
  const recent = new Map();

  function remember(exec) {
    const sessionId = exec?.agent?.id;
    if (!sessionId) return;
    const list = recent.get(sessionId) || [];
    list.push({
      ts: now(),
      tool: exec.name,
      query: exec?.arguments?.query ?? '',
      call_id: exec.callId,
    });
    while (list.length > MAX_RECENT) list.shift();
    recent.set(sessionId, list);
  }

  function refsFor(sessionId) {
    const cutoff = now() - WINDOW_MS;
    return (recent.get(sessionId) || [])
      .filter((item) => item.ts >= cutoff)
      .slice(-MAX_REFS)
      .reverse()
      .map((item) => ({
        ts: new Date(item.ts).toISOString(),
        tool: item.tool,
        query: item.query,
        call_id: item.call_id,
      }));
  }

  return {
    remember,
    refsFor,
    build({ sessionId, rating, note, command }) {
      return {
        sessionId,
        rating,
        note: String(note ?? ''),
        command,
        refs: refsFor(sessionId),
      };
    },
  };
}
