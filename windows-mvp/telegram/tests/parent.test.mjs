import test from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { Bridge, parentLink, STOPPED } from '../bridge.mjs';

// Parent mode: the Alfred app (jarvis/service/bridge.py) is on the other end of these two streams.
function setup({ ownerId = '42' } = {}) {
  const input = new PassThrough(), output = new PassThrough();
  const sent = [], texts = [], logs = [];
  let closed = false;
  let buffer = '';
  output.on('data', chunk => {
    buffer += chunk;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) { sent.push(JSON.parse(buffer.slice(0, i))); buffer = buffer.slice(i + 1); }
  });
  const link = parentLink({ input, output, onClose: () => { closed = true; }, log: l => logs.push(l) });
  const bridge = new Bridge({ ownerId, sendText: async t => texts.push(t), writeCommand: link.writeCommand,
    hardStop: link.hardStop, onDiscover: link.discovered, log: l => logs.push(l), now: () => 1_000_000 });
  link.attach(bridge);
  const fromApp = async message => {
    input.write(JSON.stringify(message) + '\n');
    await new Promise(resolve => setImmediate(resolve));
    await link.flushed();
  };
  return { link, bridge, input, sent, texts, logs, fromApp, isClosed: () => closed };
}
function update(text, id = 1, user = 42) {
  return { update_id: id, message: { message_id: id, date: 1000,
    chat: { id: user, type: 'private' }, from: { id: user, is_bot: false }, text } };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('owner commands and approvals go to the Alfred app as JSON lines', async () => {
  const { bridge, sent } = setup();
  await bridge.receive(update('open notepad', 1));
  await bridge.receive(update('/approve abcdef12', 2));
  await bridge.receive(update('/reset', 3));
  await tick();
  assert.equal(sent[0].type, 'command');
  assert.equal(sent[0].text, 'open notepad');
  assert.ok(sent[0].id);
  assert.deepEqual(sent[1], { type: 'approval', token: 'abcdef12', approved: true });
  assert.equal(sent[2].text, 'reset');
  assert.equal(bridge.pending.size, 2);
});

test('replies, approval requests and done from the app reach Telegram', async () => {
  const { bridge, sent, texts, fromApp } = setup();
  await bridge.receive(update('open notepad', 1));
  await tick();
  const id = sent[0].id;
  await fromApp({ type: 'approval', token: 'abcdef12', reason: 'click Send', timeout: 60 });
  assert.match(texts.at(-1), /Allow this action\?\nclick Send/);
  assert.match(texts.at(-1), /\/approve abcdef12/);
  await fromApp({ type: 'reply', id, text: 'Opened Notepad.' });
  assert.equal(texts.at(-1), 'Opened Notepad.');
  await fromApp({ type: 'done', id });
  assert.equal(bridge.pending.size, 0);
});

test('/stop asks the app for a hard stop; the app reports when it is done', async () => {
  const { bridge, sent, texts, fromApp } = setup();
  await bridge.receive(update('open paint', 1));
  await bridge.receive(update('/stop', 2));
  await tick();
  assert.deepEqual(sent.at(-1), { type: 'stop' });
  assert.equal(sent.filter(m => m.type === 'command').length, 1); // /stop is never a command
  assert.equal(bridge.pending.size, 0);
  assert.match(texts.at(-1), /Stopping now/);
  await fromApp({ type: 'ready', after_stop: true });
  assert.equal(texts.at(-1), STOPPED);
});

test('discovery mode reports the user ID to the app and sends no commands', async () => {
  const { bridge, sent } = setup({ ownerId: '' });
  await bridge.receive(update('/start', 1, 777));
  await tick();
  assert.deepEqual(sent, [{ type: 'telegram_user', id: '777' }]);
});

test('bad lines from the app are logged and skipped; closing the input ends the bridge', async () => {
  const { input, logs, texts, fromApp, isClosed } = setup();
  input.write('not json\n');
  await fromApp({ type: 'notice', text: 'Approval received.' });
  assert.match(logs[0], /Invalid message/);
  assert.deepEqual(texts, ['Approval received.']);
  input.end();
  await tick();
  assert.ok(isClosed());
});
