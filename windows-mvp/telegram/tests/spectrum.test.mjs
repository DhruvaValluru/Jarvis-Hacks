import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spectrumOptions, normalizedUpdate, spectrumTransport, SpectrumInbox } from '../spectrum.mjs';
import { Bridge } from '../bridge.mjs';
import { messageStore } from '../state.mjs';
import { createVoice } from '../voice.mjs';

const message = (content = { type: 'text', text: 'open notepad' }, extra = {}) => ({
  platform: 'telegram', direction: 'inbound', id: '1', sender: { id: '42' },
  timestamp: new Date(1_000_000), content, ...extra,
});
function harness(extra = {}) {
  const texts = [], commands = [], spaces = [], seen = new Set();
  const bridge = new Bridge({ ownerId: '42', sendText: async t => texts.push(t),
    writeCommand: c => commands.push(c), now: () => 1_000_000 });
  const inbox = new SpectrumInbox({ bridge, remember: id => {
    if (seen.has(id)) return false; seen.add(id); return true;
  }, onOwnerSpace: s => spaces.push(s), ...extra });
  return { inbox, texts, commands, spaces };
}
test('Spectrum is configured with the project and Telegram provider; missing secrets fail early', () => {
  const config = spectrumOptions({ SPECTRUM_PROJECT_ID: 'project', SPECTRUM_PROJECT_SECRET: 'secret',
    TELEGRAM_BOT_TOKEN: '123:fake_token' });
  assert.equal(config.projectId, 'project');
  assert.equal(config.providers.length, 1);
  assert.equal(config.telemetry, false);
  assert.throws(() => spectrumOptions({}), /SPECTRUM_PROJECT_ID/);
});
test('only inbound private Telegram messages normalize and map ids and text', () => {
  const update = normalizedUpdate({ id: '42' }, message());
  assert.equal(update.update_id, '42:1');
  assert.equal(update.message.text, 'open notepad');
  for (const invalid of [{ platform: 'slack' }, { direction: 'outbound' },
    { sender: { id: '99' } }, { sender: { id: '42', kind: 'agent' } }, { timestamp: new Date('invalid') }]) {
    assert.equal(normalizedUpdate({ id: '42' }, message(undefined, invalid)), undefined);
  }
  assert.equal(normalizedUpdate({ id: '-42' }, message()), undefined);
});
test('Spectrum sends chunked text and builds a real MP3 voice content', async () => {
  const sent = [];
  const transport = spectrumTransport(() => ({ send: async value => sent.push(value) }));
  await transport.text('x'.repeat(4500));
  assert.deepEqual(sent.map(s => s.length), [2000, 2000, 500]);
  await transport.audio(new Uint8Array([73, 68, 51]));
  const audio = await sent[3].build();
  assert.equal(audio.type, 'voice');
  assert.equal(audio.mimeType, 'audio/mpeg');
  assert.deepEqual(await audio.read(), Buffer.from([73, 68, 51]));
});
test('Spectrum inbox authorizes and deduplicates before sending commands to Windows', async () => {
  const { inbox, commands, spaces } = harness();
  await inbox.receive({ id: '99' }, message(undefined, { sender: { id: '99' } }));
  assert.equal(spaces.length, 0);
  await inbox.receive({ id: '42' }, message());
  await inbox.receive({ id: '42' }, message());
  assert.equal(commands.length, 1);
});
test('voice transcription does not block typed approvals and cannot approve by speech', async () => {
  let finish;
  const h = harness({ transcribe: () => new Promise(resolve => { finish = resolve; }) });
  await h.inbox.receive({ id: '42' }, message({ type: 'voice', mimeType: 'audio/ogg' }));
  await h.inbox.receive({ id: '42' }, message({ type: 'text', text: '/approve abcdef12' }, { id: '2' }));
  assert.equal(h.commands[0].type, 'approval');
  finish('/approve abcdef12');
  await Promise.all(h.inbox.voicePending);
  assert.equal(h.commands.length, 1);
  assert.match(h.texts[0], /Type approval/);
});
test('transcribed voice runs through the same Jarvis command and owner checks', async () => {
  const h = harness({ transcribe: async () => 'open notepad' });
  await h.inbox.receive({ id: '42' }, message({ type: 'voice', mimeType: 'audio/ogg' }));
  await Promise.all(h.inbox.voicePending);
  assert.equal(h.commands[0].text, 'open notepad');
  assert.equal(h.texts[0], 'Heard: open notepad');
});
test('voice remains optional, bounded, and falls back to typed commands on errors', async () => {
  const disabled = harness();
  await disabled.inbox.receive({ id: '42' }, message({ type: 'voice', mimeType: 'audio/ogg' }));
  assert.match(disabled.texts[0], /VOICE_SERVICE_URL/);
  const failed = harness({ transcribe: async () => { throw new Error('private service details'); } });
  await failed.inbox.receive({ id: '42' }, message({ type: 'voice', mimeType: 'audio/ogg' }));
  await Promise.all(failed.inbox.voicePending);
  assert.match(failed.texts[0], /typed command/);
  const large = harness({ transcribe: async () => { throw new Error('must not be called'); } });
  await large.inbox.receive({ id: '42' }, message({ type: 'voice', mimeType: 'audio/ogg', size: 6_000_000 }));
  assert.match(large.texts[0], /under 5 MB/);
});
test('voice service uses the teammate contract for speech and multipart transcription', async () => {
  const calls = [];
  const api = createVoice({ VOICE_SERVICE_URL: 'http://localhost:8000/api', VOICE_SERVICE_TOKEN: 'fake' }, async (url, options) => {
    calls.push({ url: String(url), ...options });
    return String(url).endsWith('speech')
      ? new Response(new Uint8Array([73, 68, 51]), { headers: { 'Content-Type': 'audio/mpeg' } })
      : Response.json({ text: 'open notepad' });
  });
  assert.deepEqual(await api.synthesize('Hello'), new Uint8Array([73, 68, 51]));
  assert.equal(calls[0].url, 'http://localhost:8000/api/speech');
  assert.deepEqual(JSON.parse(calls[0].body), { text: 'Hello' });
  assert.equal(calls[0].headers.Authorization, 'Bearer fake');
  assert.equal(await api.transcribe({ mimeType: 'audio/ogg', read: async () => Buffer.from([1, 2]) }), 'open notepad');
  const file = calls[1].body.get('audio');
  assert.equal(file.type, 'audio/ogg');
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), new Uint8Array([1, 2]));
});
test('voice service checks error status, transcripts, MIME type and upload size', async () => {
  const note = { mimeType: 'audio/ogg', read: async () => Buffer.from([1]) };
  const env = { VOICE_SERVICE_URL: 'http://localhost:8000' };
  const error = createVoice(env, async () => Response.json({ error: 'secret' }, { status: 500 }));
  await assert.rejects(error.synthesize('Hello'), { message: 'Voice service returned HTTP 500' });
  const bad = createVoice(env, async () => Response.json({ text: 42 }));
  await assert.rejects(bad.synthesize('Hello'), /MP3/);
  await assert.rejects(bad.transcribe(note), /text field/);
  await assert.rejects(bad.transcribe({ ...note, read: async () => Buffer.alloc(6_000_000) }), /too large/);
  assert.deepEqual(createVoice({}), { synthesize: undefined });
});
test('Spectrum ids survive restart and are scoped to the project, bot and owner', () => {
  const dir = mkdtempSync(join(tmpdir(), 'jarvis-spectrum-'));
  try {
    const path = join(dir, 'messages.json');
    assert.equal(messageStore(path, 'a')('42:1'), true);
    assert.equal(messageStore(path, 'a')('42:1'), false);
    assert.equal(messageStore(path, 'b')('42:1'), true);
    writeFileSync(path, 'invalid');
    assert.throws(() => messageStore(path, 'b'), /could not be read/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
