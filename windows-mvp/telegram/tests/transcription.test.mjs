import test from 'node:test';
import assert from 'node:assert/strict';
import { createTranscription } from '../photon.mjs';
import { createVoice } from '../voice.mjs';
import { Bridge } from '../bridge.mjs';
import { SpectrumInbox } from '../spectrum.mjs';

const note = (extra = {}) => ({ type: 'voice', mimeType: 'audio/ogg',
  read: async () => Buffer.from([1, 2, 3]), ...extra });

test('OpenAI receives correctly named OGG multipart audio and returns recognized text', async () => {
  const transcribe = createTranscription({ OPENAI_API_KEY: 'fake-key' }, async (url, options) => {
    assert.equal(url, 'https://api.openai.com/v1/audio/transcriptions');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Authorization, 'Bearer fake-key');
    // fetch supplies the multipart boundary; do not force a Content-Type header.
    assert.equal(options.headers['Content-Type'], undefined);
    assert.equal(options.body.get('model'), 'gpt-4o-mini-transcribe');
    assert.equal(options.body.get('response_format'), 'json');
    const file = options.body.get('file');
    assert.equal(file.name, 'voice.ogg');
    assert.equal(file.type, 'audio/ogg');
    assert.deepEqual(new Uint8Array(await file.arrayBuffer()), new Uint8Array([1, 2, 3]));
    return Response.json({ text: ' open notepad ' });
  });
  assert.equal(await transcribe(note()), 'open notepad');
  assert.equal(createTranscription({}), undefined);
});

test('audio attachments retain supported filenames or infer them from MIME type', async () => {
  const filenames = [];
  const transcribe = createTranscription({ OPENAI_API_KEY: 'fake', OPENAI_TRANSCRIBE_MODEL: 'whisper-1' }, async (_url, options) => {
    filenames.push(options.body.get('file').name);
    assert.equal(options.body.get('model'), 'whisper-1');
    return Response.json({ text: 'Hello' });
  });
  await transcribe(note({ mimeType: 'audio/mpeg', name: 'recording' }));
  await transcribe(note({ mimeType: 'audio/mp4', name: 'recording.m4a' }));
  await transcribe(note({ mimeType: 'audio/ogg; codecs=opus' }));
  assert.deepEqual(filenames, ['voice.mp3', 'recording.m4a', 'voice.ogg']);
});

test('invalid audio is refused before calling OpenAI', async () => {
  const transcribe = createTranscription({ OPENAI_API_KEY: 'fake' }, async () => {
    throw new Error('Network must not be called');
  });
  await assert.rejects(transcribe(note({ size: 6_000_000, read: async () => { throw new Error('Must not download'); } })), /too large/);
  await assert.rejects(transcribe(note({ read: async () => Buffer.alloc(6_000_000) })), /size/);
  await assert.rejects(transcribe(note({ read: async () => Buffer.alloc(0) })), /size/);
  await assert.rejects(transcribe(note({ mimeType: 'audio/unknown' })), /Unsupported/);
});

test('API failures and missing transcripts never expose raw errors as a command', async () => {
  const failure = createTranscription({ OPENAI_API_KEY: 'fake' }, async () =>
    Response.json({ error: { message: 'private provider detail' } }, { status: 401 }));
  await assert.rejects(failure(note()), { message: 'OpenAI transcription returned HTTP 401' });
  for (const result of [{}, { text: 42 }, { text: '  ' }]) {
    const invalid = createTranscription({ OPENAI_API_KEY: 'fake' }, async () => Response.json(result));
    await assert.rejects(invalid(note()), /no transcript/);
  }
});

test('Telegram voice -> direct OpenAI -> command -> text and spoken result', async () => {
  const texts = [], commands = [], audio = [], calls = [];
  const voice = createVoice({ OPENAI_API_KEY: 'fake' }, async (url, options) => {
    calls.push(url);
    return url.endsWith('/transcriptions') ? Response.json({ text: 'open notepad' })
      : new Response(new Uint8Array([73, 68, 51]), { headers: { 'Content-Type': 'audio/mpeg' } });
  });
  const bridge = new Bridge({ ownerId: '42', sendText: async t => texts.push(t),
    sendAudio: async b => audio.push(b), synthesize: voice.synthesize,
    writeCommand: c => commands.push(c), now: () => 1_000_000 });
  const inbox = new SpectrumInbox({ bridge, remember: () => true, transcribe: voice.transcribe,
    onOwnerSpace: () => {} });
  await inbox.receive({ id: '42' }, { platform: 'telegram', direction: 'inbound',
    id: 'voice-1', sender: { id: '42' }, timestamp: new Date(1_000_000), content: note() });
  await Promise.all(inbox.voicePending);
  assert.equal(commands[0].text, 'open notepad');
  assert.equal(texts[0], 'Heard: open notepad');
  await bridge.receiveWorker({ type: 'reply', text: 'Opened Notepad.' });
  await bridge.receiveWorker({ type: 'done', id: commands[0].id });
  assert.equal(texts[1], 'Opened Notepad.');
  assert.deepEqual(audio[0], new Uint8Array([73, 68, 51]));
  assert.equal(bridge.pending.size, 0);
  assert.deepEqual(calls, ['https://api.openai.com/v1/audio/transcriptions', 'https://api.openai.com/v1/audio/speech']);
});
