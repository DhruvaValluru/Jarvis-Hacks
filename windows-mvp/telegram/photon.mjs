export function createSpeech(env, fetchImpl = fetch) {
  if (!env.OPENAI_API_KEY) return undefined;
  return async (text) => {
    const response = await fetchImpl(
      'https://api.openai.com/v1/audio/speech',
      { method: 'POST', headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: String(text).slice(0, 1500),
          model: env.OPENAI_TTS_MODEL || 'gpt-4o-mini-tts',
          voice: env.OPENAI_TTS_VOICE || 'cedar', response_format: 'mp3' }),
        signal: AbortSignal.timeout(30_000) });
    if (!response.ok) throw new Error(`OpenAI speech returned HTTP ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  };
}

export function createTranscription(env, fetchImpl = fetch) {
  if (!env.OPENAI_API_KEY) return undefined;
  return async (content) => {
    if (content.size > 5 * 1024 * 1024) throw new Error('Voice note is too large');
    const bytes = await content.read();
    if (!bytes.length || bytes.length > 5 * 1024 * 1024) throw new Error('Invalid voice note size');
    const mimeType = content.mimeType.split(';')[0].trim().toLowerCase();
    const extensions = { 'audio/ogg': 'ogg', 'audio/opus': 'ogg', 'audio/mpeg': 'mp3',
      'audio/mp3': 'mp3', 'audio/mp4': 'm4a', 'audio/x-m4a': 'm4a',
      'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/webm': 'webm',
      'audio/flac': 'flac', 'audio/x-flac': 'flac' };
    // OpenAI needs a recognizable extension, including unnamed Telegram voice notes.
    const filename = /\.(flac|mp3|mp4|mpeg|mpga|m4a|ogg|wav|webm)$/i.test(content.name || '')
      ? content.name : extensions[mimeType] ? `voice.${extensions[mimeType]}` : undefined;
    if (!filename) throw new Error('Unsupported audio format');
    const form = new FormData();
    form.append('file', new Blob([bytes], { type: mimeType }), filename);
    form.append('model', env.OPENAI_TRANSCRIBE_MODEL || 'gpt-4o-mini-transcribe');
    form.append('response_format', 'json');
    const response = await fetchImpl('https://api.openai.com/v1/audio/transcriptions', {
      method: 'POST', headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}` },
      body: form, signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new Error(`OpenAI transcription returned HTTP ${response.status}`);
    const result = await response.json();
    if (typeof result.text !== 'string' || !result.text.trim()) throw new Error('OpenAI returned no transcript');
    return result.text.trim();
  };
}
