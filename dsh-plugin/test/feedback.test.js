import test from 'node:test';
import assert from 'node:assert/strict';
import { createFeedback } from '../lib/feedback.js';

const NOW = 1_700_000_000_000;

test('记住最近调用并按会话返回最多 3 条', () => {
  const feedback = createFeedback({ now: () => NOW });
  for (let index = 0; index < 5; index += 1) {
    feedback.remember({ agent: { id: 's1' }, callId: `c${index}`, name: 'godot_search_docs', arguments: { query: `q${index}` } });
  }
  const refs = feedback.refsFor('s1');
  assert.equal(refs.length, 3);
  assert.equal(refs[0].call_id, 'c4');
  assert.equal(refs[0].query, 'q4');
  assert.deepEqual(feedback.refsFor('other'), []);
});

test('超过 10 分钟的记录不进入 refs', () => {
  let now = NOW;
  const feedback = createFeedback({ now: () => now });
  feedback.remember({ agent: { id: 's1' }, callId: 'old', name: 'godot_search_docs', arguments: { query: 'old' } });
  now += 11 * 60 * 1000;
  assert.deepEqual(feedback.refsFor('s1'), []);
});

test('build 生成 feedback 事件体', () => {
  const feedback = createFeedback({ now: () => NOW });
  feedback.remember({ agent: { id: 's1' }, callId: 'c1', name: 'godot_search_docs', arguments: { query: 'queue_free' } });
  const event = feedback.build({ sessionId: 's1', rating: 'bad', note: '没命中', command: 'godot-rag-bad' });
  assert.equal(event.rating, 'bad');
  assert.equal(event.note, '没命中');
  assert.equal(event.command, 'godot-rag-bad');
  assert.equal(event.refs.length, 1);
  assert.equal(event.refs[0].query, 'queue_free');
});
