import test from 'node:test';
import assert from 'node:assert/strict';
import { Bridge } from '../bridge.mjs';
import { createSpeech, createTransport } from '../photon.mjs';
import { cursorStore } from '../state.mjs';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function setup(extra = {}) {
  const texts = [], commands = [], audio = [], logs = [];
  const bridge = new Bridge({ ownerId: '42', sendText: async t => texts.push(t),
    sendAudio: async a => audio.push(a), writeCommand: c => commands.push(c),
    log: l => logs.push(l), now: () => 1_000_000, ...extra });
  return { bridge, texts, commands, audio, logs };
}
function update(text, id = 1, user = 42) {
  return { update_id: id, message: { message_id: id, date: 1000,
    chat: { id: user, type: 'private' }, from: { id: user, is_bot: false, first_name: 'Test' }, text } };
}

test('only the configured owner can command the PC; discovery grants no access', async () => {
  const { bridge, commands } = setup();
  await bridge.receive(update('open notepad', 1, 99));
  const group = update('open notepad', 2); group.message.chat.type = 'group';
  await bridge.receive(group);
  assert.equal(commands.length, 0);
  const discovery = setup({ ownerId: '' });
  await discovery.bridge.receive(update('open notepad'));
  assert.equal(discovery.commands.length, 0);
  assert.match(discovery.logs[0], /42/);
});

test('commands deduplicate, reject stale events, and keep approval replies flowing', async () => {
  const { bridge, commands, texts } = setup();
  await bridge.receive(update('open notepad'));
  await bridge.receive(update('open notepad'));
  assert.equal(commands.length, 1);
  const stale = update('close notepad', 2); stale.message.date = 1;
  await bridge.receive(stale);
  assert.equal(commands.length, 1);
  assert.match(texts[0], /old message/);
  await bridge.receive(update('/approve abcdef12', 3));
  assert.deepEqual(commands[1], { type: 'approval', token: 'abcdef12', approved: true });
  await bridge.receive(update('/deny abcdef12', 4));
  assert.equal(commands[2].approved, false);
  await bridge.receiveWorker({ type: 'done', id: commands[0].id });
  assert.equal(bridge.pending.size, 0);
});

test('reply text survives OpenAI speech failures and audio can be disabled', async () => {
  const { bridge, texts, audio } = setup({ synthesize: async () => { throw new Error('offline'); } });
  await bridge.receiveWorker({ type: 'reply', text: 'Opened Notepad.' });
  assert.deepEqual(texts, ['Opened Notepad.']);
  assert.equal(audio.length, 0);
  await bridge.receive(update('/audio off'));
  bridge.synthesize = async () => new Uint8Array([1, 2]);
  await bridge.receiveWorker({ type: 'reply', text: 'Done.' });
  assert.equal(audio.length, 0);
  await bridge.receive(update('/audio on', 2));
  await bridge.receiveWorker({ type: 'reply', text: 'Done.' });
  assert.deepEqual(audio[0], new Uint8Array([1, 2]));
});

test('Photons published SDK sends valid text and multipart MP3 requests', async () => {
  const calls = [];
  const transport = createTransport('test-token', async request => {
    calls.push(request);
    return Response.json({ ok: true, result: { message_id: 1, date: 1000,
      chat: { id: 42, type: 'private' } } });
  });
  await transport.text('42', 'Hello');
  assert.equal(new URL(calls[0].url).pathname, '/bottest-token/sendMessage');
  assert.deepEqual(await calls[0].json(), { chat_id: '42', text: 'Hello' });
  await transport.audio('42', new Uint8Array([73, 68, 51]));
  assert.match(calls[1].headers.get('content-type'), /^multipart\/form-data; boundary=/);
  const form = await calls[1].formData();
  assert.equal(form.get('audio'), 'attach://speech');
  assert.equal(form.get('speech').name, 'jarvis.mp3');
  assert.deepEqual(new Uint8Array(await form.get('speech').arrayBuffer()), new Uint8Array([73, 68, 51]));
});

test('OpenAI speech returns MP3 bytes and limits the spoken length', async () => {
  let options;
  const speak = createSpeech({ OPENAI_API_KEY: 'fake-key' }, async (url, opts) => {
    options = opts;
    assert.equal(url, 'https://api.openai.com/v1/audio/speech');
    return new Response(new Uint8Array([73, 68, 51]), { headers: { 'content-type': 'audio/mpeg' } });
  });
  assert.deepEqual(await speak('x'.repeat(2000)), new Uint8Array([73, 68, 51]));
  assert.equal(JSON.parse(options.body).input.length, 1500);
  assert.equal(options.headers.Authorization, 'Bearer fake-key');
  assert.equal(options.method, 'POST');
  assert.deepEqual(JSON.parse(options.body), { input: 'x'.repeat(1500),
    model: 'gpt-4o-mini-tts', voice: 'cedar', response_format: 'mp3' });
  assert.equal(createSpeech({}), undefined);
});

test('Photons published SDK polls message updates with the supplied cursor', async () => {
  const transport = createTransport('test-token', async request => {
    assert.equal(new URL(request.url).pathname, '/bottest-token/getUpdates');
    assert.deepEqual(await request.json(), { offset: 123, timeout: 25, allowed_updates: ['message'] });
    return Response.json({ ok: true, result: [update('hello', 123)] });
  });
  assert.equal((await transport.poll(123))[0].update_id, 123);
});

test('cursor survives restart and is scoped to the bot and owner', () => {
  const dir = mkdtempSync(join(tmpdir(), 'jarvis-test-'));
  try {
    const path = join(dir, 'cursor.json');
    const cursor = cursorStore(path, 123, '42');
    assert.equal(cursor.read(), undefined);
    cursor.write(501);
    assert.equal(cursorStore(path, 123, '42').read(), 501);
    assert.equal(cursorStore(path, 456, '42').read(), undefined);
    assert.equal(cursorStore(path, 123, '99').read(), undefined);
    writeFileSync(path, 'broken');
    assert.throws(() => cursor.read(), /could not be read/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('queue stays bounded without blocking denial or reset', async () => {
  const { bridge, commands, texts } = setup();
  for (let id = 1; id <= 11; id++) await bridge.receive(update('open notepad', id));
  assert.equal(commands.length, 10);
  assert.match(texts[0], /queue is full/);
  await bridge.receive(update('/deny abcdef12', 12));
  assert.equal(commands[10].type, 'approval');
  await bridge.receiveWorker({ type: 'done', id: commands[0].id });
  await bridge.receive(update('/reset', 13));
  assert.equal(commands[11].text, 'reset');
});


test('OpenAI speech honors configuration and rejects HTTP errors without leaking secrets', async () => {
  const speak = createSpeech({ OPENAI_API_KEY: 'secret-key', OPENAI_TTS_MODEL: 'tts-1',
    OPENAI_TTS_VOICE: 'onyx' }, async (_url, opts) => {
    assert.deepEqual(JSON.parse(opts.body), { input: 'Hello', model: 'tts-1',
      voice: 'onyx', response_format: 'mp3' });
    return Response.json({ error: { message: 'secret-key' } }, { status: 401 });
  });
  await assert.rejects(speak('Hello'), { message: 'OpenAI speech returned HTTP 401' });
});

test('audio settings explain a missing key and can turn off without configuring a provider', async () => {
  const { bridge, texts } = setup();
  await bridge.receive(update('/audio on'));
  assert.match(texts[0], /OPENAI_API_KEY/);
  await bridge.receive(update('/audio off', 2));
  assert.equal(texts[1], 'Audio replies off.');
});
