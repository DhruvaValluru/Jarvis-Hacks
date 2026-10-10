import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';

export const HELP = 'Alfred controls your Windows PC from this private chat.\n'
  + 'Try: open notepad, volume 30, what is running, or timer for 10 seconds.\n'
  + '/stop hard-stops everything Alfred is doing, right away, and lets go of the mouse and keyboard.\n'
  + '/reset starts a fresh conversation. /audio on or /audio off toggles spoken replies.\n'
  + 'Spoken replies use an AI-generated voice.\n'
  + 'Actions needing permission ask for /approve CODE or /deny CODE.';

export const PRIVACY = 'Privacy: your messages and Alfred\'s replies pass through Telegram and Photon Spectrum. '
  + 'Voice notes and spoken replies use OpenAI. What you ask, text Alfred reads on your PC and screenshots of your '
  + 'screen go to Anthropic (Claude). Full notice: windows-mvp/PRIVACY.md.';

export const STOPPED = 'Stopped. Everything Alfred was doing was cancelled, it let go of the mouse and keyboard, '
  + 'and the conversation was reset. Ready for a new command.';

export class Bridge {
  constructor({ ownerId, sendText, sendAudio, synthesize, writeCommand, log = console.log,
    now = () => Date.now(), hardStop = async () => {}, restarting = () => false, onDiscover = () => {} }) {
    Object.assign(this, { ownerId: String(ownerId || ''), sendText, sendAudio,
      synthesize, writeCommand, log, now, hardStop, restarting, onDiscover });
    this.audio = Boolean(synthesize);
    this.seen = new Set();
    this.pending = new Set();
  }

  async receive(update) {
    const message = update.message;
    if (!message || message.chat?.type !== 'private' || message.from?.is_bot) return;
    if (this.seen.has(update.update_id)) return;
    this.seen.add(update.update_id);
    if (this.seen.size > 1000) this.seen.delete(this.seen.values().next().value);
    // Discovery never starts the Windows worker or grants access automatically.
    if (!this.ownerId) {
      this.log(`Telegram user ID: ${message.from?.id}. Set TELEGRAM_OWNER_ID locally and restart.`);
      this.onDiscover(String(message.from?.id ?? ''));
      return;
    }
    if (String(message.from?.id) !== this.ownerId || String(message.chat.id) !== this.ownerId) return;
    if (message.date * 1000 < this.now() - 120_000) {
      await this.sendText('Skipped an old message. Please send your command again.');
      return;
    }
    const text = (message.text || '').trim();
    if (!text) {
      await this.sendText('Send a typed command for now. Voice-note input is not enabled yet.');
      return;
    }
    if (text === '/stop') {
      // Hard stop: the PC side kills the worker process and starts a fresh one.
      this.pending.clear();
      await this.sendText('Stopping now.');
      return this.hardStop();
    }
    if (text === '/start') return this.sendText(`${HELP}\n\n${PRIVACY}`);
    if (text === '/help') return this.sendText(HELP);
    if (text === '/privacy') return this.sendText(PRIVACY);
    if (text === '/audio on' || text === '/audio off') {
      this.audio = text.endsWith('on') && Boolean(this.synthesize);
      return this.sendText(this.audio ? 'Audio replies on (AI-generated voice).'
        : text.endsWith('off') ? 'Audio replies off.'
        : 'Set OPENAI_API_KEY on the PC and restart to enable audio replies.');
    }
    const approval = /^\/(approve|deny) ([a-f0-9]{8})$/i.exec(text);
    if (approval) {
      this.writeCommand({ type: 'approval', token: approval[2].toLowerCase(),
        approved: approval[1].toLowerCase() === 'approve' });
      return;
    }
    if (text.startsWith('/') && text !== '/reset') return this.sendText(HELP);
    if (this.restarting()) return this.sendText('Alfred is restarting after /stop. Send that again in a few seconds.');
    if (text.length > 6000) return this.sendText('That command is too long. Keep it under 6,000 characters.');
    if (this.pending.size >= 10) return this.sendText('The command queue is full. Wait for a reply before sending more.');
    const id = randomUUID();
    this.pending.add(id);
    try {
      this.writeCommand({ type: 'command', id, text: text === '/reset' ? 'reset' : text });
    } catch (error) {
      this.pending.delete(id);
      throw error;
    }
  }

  async receiveWorker(message) {
    if (!this.ownerId) return;
    if (message.type === 'done') { this.pending.delete(message.id); return; }
    if (message.type === 'ready') {
      return this.sendText(message.after_stop ? STOPPED : 'Alfred is online on your Windows PC. Send /help for commands.');
    }
    if (message.type === 'approval') {
      return this.sendText(`Allow this action?\n${message.reason}\n\n`
        + `/approve ${message.token}\n/deny ${message.token}\nExpires in ${message.timeout} seconds.`);
    }
    if (message.type !== 'reply' && message.type !== 'notice') return;
    await this.sendText(message.text);
    if (message.type === 'reply' && this.audio && this.synthesize) {
      try {
        const audio = await this.synthesize(message.text);
        await this.sendAudio(audio);
      } catch {
        this.log('Audio generation/delivery failed; the text reply was delivered.');
      }
    }
  }
}

// Parent mode (ALFRED_BRIDGE_MODE=parent): the Alfred app owns the one engine. Commands, approvals,
// /stop and discovered user IDs go to the app as JSON lines on `output`; the app answers on `input`
// in the worker's format (reply, notice, approval, done, ready), which the bridge handles as before.
export function parentLink({ input, output, onClose = () => {}, log = console.error }) {
  let bridge;
  let outbound = Promise.resolve();
  const send = message => { output.write(JSON.stringify(message) + '\n'); };
  createInterface({ input }).on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { log('Invalid message from the Alfred app.'); return; }
    if (!bridge || !message || typeof message !== 'object') return;
    if (message.type === 'done') { bridge.pending.delete(message.id); return; }
    outbound = outbound.then(() => bridge.receiveWorker(message)).catch(() => {
      log('Could not deliver a Telegram reply. Check connectivity and bot access.');
    });
  }).on('close', onClose);
  return {
    send,
    attach(target) { bridge = target; },
    flushed: () => outbound,
    writeCommand: message => send(message),
    hardStop: async () => send({ type: 'stop' }),
    discovered: id => send({ type: 'telegram_user', id }),
  };
}
