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
