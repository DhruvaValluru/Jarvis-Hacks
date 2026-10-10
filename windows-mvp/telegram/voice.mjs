import { createSpeech } from './photon.mjs';

// Stable HTTP contract for a voice implementation hosted from another repo.
export function createVoice(env, fetchImpl = fetch) {
  if (!env.VOICE_SERVICE_URL) return { synthesize: createSpeech(env, fetchImpl) };
  const base = new URL(env.VOICE_SERVICE_URL);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error('VOICE_SERVICE_URL must be an HTTP(S) base URL without credentials or query parameters.');
  }
  base.pathname = base.pathname.replace(/\/$/, '') + '/';
  const auth = env.VOICE_SERVICE_TOKEN ? { Authorization: `Bearer ${env.VOICE_SERVICE_TOKEN}` } : {};
  async function request(route, options) {
    const response = await fetchImpl(new URL(route, base), { ...options,
      headers: { ...auth, ...options.headers }, signal: AbortSignal.timeout(30_000) });
    if (!response.ok) throw new Error(`Voice service returned HTTP ${response.status}`);
    return response;
  }
  return {
    async synthesize(text) {
      const response = await request('speech', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: String(text).slice(0, 1500) }) });
      if (!response.headers.get('content-type')?.startsWith('audio/mpeg')) throw new Error('Voice service must return MP3 audio.');
      return new Uint8Array(await response.arrayBuffer());
    },
    async transcribe(content) {
      const bytes = await content.read();
      if (bytes.length > 5 * 1024 * 1024) throw new Error('Voice note is too large');
      const form = new FormData();
      form.append('audio', new Blob([bytes], { type: content.mimeType }), content.name || 'voice.ogg');
      const result = await (await request('transcribe', { method: 'POST', body: form })).json();
      if (typeof result.text !== 'string') throw new Error('Voice service must return a text field.');
      return result.text;
    },
  };
}
