import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from './bridge.mjs';
import { createTransport, createSpeech } from './photon.mjs';
import { cursorStore } from './state.mjs';

const mvpDir = join(dirname(fileURLToPath(import.meta.url)), '..');
if (existsSync(join(mvpDir, '.env'))) process.loadEnvFile(join(mvpDir, '.env'));
const token = process.env.TELEGRAM_BOT_TOKEN;
const ownerId = process.env.TELEGRAM_OWNER_ID || '';
if (!token) throw new Error('Set TELEGRAM_BOT_TOKEN in windows-mvp/.env. See TELEGRAM.md.');
if (ownerId && !/^[1-9]\d*$/.test(ownerId)) throw new Error('TELEGRAM_OWNER_ID must be your numeric Telegram user ID.');
if (ownerId && process.platform !== 'win32') throw new Error('Run the configured bridge on the Windows PC you want to control.');

const transport = createTransport(token);
const abort = new AbortController();
let worker;
let stopping = false;
let outbound = Promise.resolve();
function stop() {
  if (stopping) return;
  stopping = true;
  abort.abort();
  worker?.kill();
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);

async function main() {
  const me = await transport.info();
  if ((await transport.webhook()).url) {
    throw new Error('This bot has a webhook configured. Use a dedicated bot with no webhook for the long-polling bridge.');
  }
  console.log(`Connected to @${me.username} using Photon's Telegram SDK.`);
  if (!ownerId) console.log('Discovery mode: send /start to the bot, then copy the user ID printed here into .env. No PC actions are enabled.');
  const bridge = new Bridge({ ownerId, sendText: text => transport.text(ownerId, text),
    sendAudio: bytes => transport.audio(ownerId, bytes), synthesize: createSpeech(process.env),
    writeCommand(message) {
      if (!worker || !worker.stdin.writable) throw new Error('Windows worker is unavailable. Restart jarvis-telegram.bat.');
      worker.stdin.write(JSON.stringify(message) + '\n');
    },
  });
  if (ownerId) {
    const python = process.env.JARVIS_PYTHON || join(mvpDir, '.venv', 'Scripts', 'python.exe');
    worker = spawn(python, ['-u', '-m', 'jarvis.telegram_worker'], {
      cwd: mvpDir, env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true,
    });
    worker.on('error', () => {
      console.error('Could not start Python. Run setup.bat first or set JARVIS_PYTHON.');
      process.exitCode = 1;
      stop();
    });
    worker.on('exit', code => {
      if (!stopping) {
        console.error(`Windows worker exited (${code}). Restart the bridge.`);
        process.exitCode = 1;
        stop();
      }
    });
    worker.stdin.on('error', () => stop());
    createInterface({ input: worker.stdout }).on('line', line => {
      let message;
      try { message = JSON.parse(line); }
      catch { console.error('Invalid worker output.'); return; }
      // Completion frees queue capacity immediately, even while audio uploads.
      if (message.type === 'done') { bridge.pending.delete(message.id); return; }
      outbound = outbound.then(() => bridge.receiveWorker(message)).catch(() => {
        console.error('Could not deliver a Telegram reply. Check connectivity and bot access.');
      });
    });
  }
  const cursor = cursorStore(join(mvpDir, '.telegram-state.json'), me.id, ownerId);
  let offset = cursor.read();
  while (!stopping) {
    try {
      for (const update of await transport.poll(offset, abort.signal)) {
        if (offset !== undefined && update.update_id < offset) continue;
        offset = update.update_id + 1;
        // Never execute commands if recording their IDs failed.
        try { cursor.write(offset); }
        catch { throw new Error('Could not record the Telegram cursor.'); }
        try { await bridge.receive(update); }
        catch { console.error('Could not handle a message. Check the Windows worker and Telegram connection.'); }
      }
    } catch (error) {
      if (stopping) break;
      if (error.errorCode === 401 || error.errorCode === 409) throw new Error('Telegram authentication failed or another bot instance is polling. Check your token and run only one bridge.');
      console.error('Telegram connection interrupted. Retrying...');
      const delay = Math.min(30, Math.max(2, error.parameters?.retry_after || 2));
      await new Promise(resolve => {
        const finish = () => { clearTimeout(timer); abort.signal.removeEventListener('abort', finish); resolve(); };
        const timer = setTimeout(finish, delay * 1000);
        abort.signal.addEventListener('abort', finish, { once: true });
      });
    }
  }
}

main().catch(() => {
  // Avoid dumping errors that can embed the bot token in request URLs.
  console.error('Bridge could not start. Check your .env, bot token, webhook settings, and Python setup.');
  process.exitCode = 1;
  stop();
});
