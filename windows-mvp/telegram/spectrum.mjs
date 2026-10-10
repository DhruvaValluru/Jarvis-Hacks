import { Spectrum, voice } from 'spectrum-ts';
import { telegram } from 'spectrum-ts/providers/telegram';

export function spectrumOptions(env) {
  for (const key of ['SPECTRUM_PROJECT_ID', 'SPECTRUM_PROJECT_SECRET', 'TELEGRAM_BOT_TOKEN']) {
    if (!env[key]) throw new Error(`Set ${key} in windows-mvp/.env.`);
  }
  return { projectId: env.SPECTRUM_PROJECT_ID, projectSecret: env.SPECTRUM_PROJECT_SECRET,
    providers: [telegram.config({ botToken: env.TELEGRAM_BOT_TOKEN })],
    telemetry: false, options: { logLevel: 'error' } };
}
export async function connectSpectrum(env) {
  return Spectrum(spectrumOptions(env));
}

// A positive Telegram chat id equal to the sender identifies a private chat.
export function normalizedUpdate(space, message) {
  if (message.platform !== 'telegram' || message.direction !== 'inbound'
    || message.sender?.kind === 'agent' || !/^[1-9]\d*$/.test(String(space.id))
    || String(space.id) !== String(message.sender?.id)
    || !Number.isFinite(message.timestamp?.getTime())) return undefined;
  return { update_id: `${space.id}:${message.id}`, message: {
    date: message.timestamp.getTime() / 1000,
    chat: { id: String(space.id), type: 'private' },
    from: { id: String(message.sender.id), is_bot: false },
    text: message.content.type === 'text' ? message.content.text : undefined,
  } };
}

export function spectrumTransport(getSpace) {
  return {
    async text(text) {
      const space = getSpace();
      if (!space) throw new Error('Owner has not opened the Telegram chat yet.');
      const chars = Array.from(String(text));
      for (let i = 0; i < chars.length; i += 2000) await space.send(chars.slice(i, i + 2000).join(''));
    },
    async audio(bytes) {
      const space = getSpace();
      if (!space) throw new Error('Owner has not opened the Telegram chat yet.');
      await space.send(voice(Buffer.from(bytes), { mimeType: 'audio/mpeg', name: 'jarvis.mp3' }));
    },
  };
}

// Text and approval messages can continue while a voice note is being transcribed.
export class SpectrumInbox {
  constructor({ bridge, remember, transcribe, onOwnerSpace, log = console.log }) {
    Object.assign(this, { bridge, remember, transcribe, onOwnerSpace, log });
    this.voicePending = new Set();
  }
  async receive(space, message) {
    const update = normalizedUpdate(space, message);
    if (!update) return;
    if (!this.bridge.ownerId) return this.bridge.receive(update);
    if (update.message.from.id !== this.bridge.ownerId) return;
    this.onOwnerSpace(space);
    if (!this.remember(update.update_id)) return;
    const content = message.content;
    if (content.type !== 'voice' && !(content.type === 'attachment' && content.mimeType.startsWith('audio/'))) {
      return this.bridge.receive(update);
    }
    if (update.message.date * 1000 < this.bridge.now() - 120_000) return this.bridge.receive(update);
    if (!this.transcribe) return this.bridge.sendText('Voice input will be available when VOICE_SERVICE_URL is configured. Send a typed command for now.');
    if (this.voicePending.size >= 2) return this.bridge.sendText('Voice transcription is busy. Wait for a reply or send a typed command.');
    if (content.size > 5 * 1024 * 1024 || content.duration > 120) {
      return this.bridge.sendText('Keep voice notes under 5 MB and 2 minutes.');
    }
    const task = this.handleVoice(content, update).catch(() => this.log('Could not process a voice note.'));
    this.voicePending.add(task);
    task.finally(() => this.voicePending.delete(task));
  }
  async handleVoice(content, update) {
    try {
      const text = (await this.transcribe(content)).trim();
      // Permission codes must be typed explicitly, never inferred from speech.
      if (/^\/(approve|deny)\b/i.test(text)) return this.bridge.sendText('Type approval or denial codes in Telegram.');
      if (!text || text.length > 6000) throw new Error('Invalid transcript');
      await this.bridge.sendText(`Heard: ${text}`);
      await this.bridge.receive({ ...update, message: { ...update.message, text } });
    } catch {
      await this.bridge.sendText('Could not transcribe that voice note. Please send a typed command.');
    }
  }
}
