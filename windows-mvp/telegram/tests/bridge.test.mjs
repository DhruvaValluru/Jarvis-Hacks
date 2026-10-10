import test from 'node:test';
import assert from 'node:assert/strict';
import { Bridge } from '../bridge.mjs';
import { createSpeech } from '../photon.mjs';

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
