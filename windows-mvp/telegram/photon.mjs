import { createTelegramClient, getMe, getUpdates, getWebhookInfo, sendMessage,
  sendAudio } from '@photon-ai/telegram-ts';

// All Telegram calls use Photon's published SDK. No cloud gateway is required.
export function createTransport(token, fetchImpl = fetch) {
  const client = createTelegramClient({ token, fetch: fetchImpl });
  return {
    async info() { return (await getMe({ client })).result; },
    async webhook() { return (await getWebhookInfo({ client })).result; },
    async poll(offset, signal) {
      return (await getUpdates({ client, signal,
        body: { offset, timeout: 25, allowed_updates: ['message'] } })).result;
    },
    async text(chatId, text) {
      // Stay below Telegram's limit, including characters represented by surrogate pairs.
      const chars = Array.from(String(text));
      for (let i = 0; i < chars.length; i += 2000) {
        await sendMessage({ client, body: { chat_id: chatId, text: chars.slice(i, i + 2000).join('') } });
      }
    },
    async audio(chatId, bytes) {
      // The generated SDK types audio as a string. attach:// plus a custom
      // multipart serializer uploads bytes while retaining its validators.
      await sendAudio({ client,
        body: { chat_id: chatId, audio: 'attach://speech', title: 'Jarvis', performer: 'Jarvis' },
        headers: { 'Content-Type': null },
        bodySerializer(body) {
          const form = new FormData();
          for (const [key, value] of Object.entries(body)) form.append(key, String(value));
          form.append('speech', new Blob([bytes], { type: 'audio/mpeg' }), 'jarvis.mp3');
          return form;
        },
      });
    },
  };
}

export function createSpeech(env, fetchImpl = fetch) {
  if (!env.ELEVENLABS_API_KEY) return undefined;
  const voice = env.ELEVENLABS_VOICE_ID || 'JBFqnCBsd6RMkjVDRZzb';
  return async (text) => {
    const response = await fetchImpl(
      `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voice)}?output_format=mp3_44100_128`,
      { method: 'POST', headers: { 'xi-api-key': env.ELEVENLABS_API_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: String(text).slice(0, 1500),
          model_id: env.ELEVENLABS_MODEL_ID || 'eleven_flash_v2_5' }),
        signal: AbortSignal.timeout(30_000) });
    if (!response.ok) throw new Error(`ElevenLabs returned HTTP ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  };
}
