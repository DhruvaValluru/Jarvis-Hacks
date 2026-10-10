import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from './bridge.mjs';
import { connectSpectrum, spectrumTransport, SpectrumInbox, spectrumOptions } from './spectrum.mjs';
import { createVoice } from './voice.mjs';
import { messageStore } from './state.mjs';

const mvpDir = join(dirname(fileURLToPath(import.meta.url)), '..');
if (existsSync(join(mvpDir, '.env'))) process.loadEnvFile(join(mvpDir, '.env'));
const token = process.env.TELEGRAM_BOT_TOKEN;
const ownerId = process.env.TELEGRAM_OWNER_ID || '';
if (!token) throw new Error('Set TELEGRAM_BOT_TOKEN in windows-mvp/.env. See TELEGRAM.md.');
if (ownerId && !/^[1-9]\d*$/.test(ownerId)) throw new Error('TELEGRAM_OWNER_ID must be your numeric Telegram user ID.');
if (ownerId && process.platform !== 'win32') throw new Error('Run the configured bridge on the Windows PC you want to control.');

spectrumOptions(process.env);
let app;
let ownerSpace;
const transport = spectrumTransport(() => ownerSpace);
const voice = createVoice(process.env);
let worker;
let stopping = false;
let outbound = Promise.resolve();
function stop() {
  if (stopping) return;
  stopping = true;
  worker?.kill();
  void app?.stop().catch(() => {});
}
process.on('SIGINT', stop);
process.on('SIGTERM', stop);

async function main() {
  app = await connectSpectrum(process.env);
  console.log('Connected to Telegram using Photon Spectrum.');
  if (!ownerId) console.log('Discovery mode: send /start to the bot, then copy the user ID printed here into .env. No PC actions are enabled.');
  const bridge = new Bridge({ ownerId, sendText: text => transport.text(text),
    sendAudio: bytes => transport.audio(bytes), synthesize: voice.synthesize,
    writeCommand(message) {
      if (!worker || !worker.stdin.writable) throw new Error('Windows worker is unavailable. Restart jarvis-telegram.bat.');
      worker.stdin.write(JSON.stringify(message) + '\n');
    },
    hardStop, restarting: () => restarting,
  });
  let workerStarted = false;
  let restarting = false;
  // /stop: kill the worker and everything it started (PowerShell, a Claude call in flight,
  // a computer-use drag), then start a fresh worker, which releases any held keys or buttons.
  async function hardStop() {
    if (!worker || restarting || stopping) return;
    restarting = true;
    const old = worker;
    const exited = new Promise(resolve => old.once('exit', resolve));
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(old.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    } else {
      old.kill('SIGKILL');
    }
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000))]);
    bridge.pending.clear();
    console.log('Hard stop from Telegram: worker killed, starting a fresh one.');
    workerStarted = false;
    startWorker({ afterStop: true });
    // Commands sent from now on wait in the new worker's input until it is up; if it fails
    // to start, the normal exit handling below reports it instead of staying "restarting".
    restarting = false;
  }
  function startWorker({ afterStop = false } = {}) {
    if (workerStarted || !ownerId || stopping) return;
    workerStarted = true;
    const python = process.env.JARVIS_PYTHON || join(mvpDir, '.venv', 'Scripts', 'python.exe');
    const child = spawn(python, ['-u', '-m', 'jarvis.telegram_worker'], {
      cwd: mvpDir, env: { ...process.env, PYTHONIOENCODING: 'utf-8', JARVIS_RESTART_REASON: afterStop ? 'stop' : '' },
      stdio: ['pipe', 'pipe', 'inherit'], windowsHide: true,
    });
    worker = child;
    child.on('error', () => {
      console.error('Could not start Python. Run setup.bat first or set JARVIS_PYTHON.');
      process.exitCode = 1;
      stop();
    });
    child.on('exit', code => {
      if (child !== worker) return; // an old worker replaced by /stop
      if (!stopping && !restarting) {
        if (code === 3) console.error('Accept the data notice on this PC first: jarvis.bat --terms');
        console.error(`Windows worker exited (${code}). Restart the bridge.`);
        process.exitCode = 1;
        stop();
      }
    });
    child.stdin.on('error', () => { if (child === worker && !restarting) stop(); });
    createInterface({ input: child.stdout }).on('line', line => {
      if (child !== worker) return;
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
  const remember = messageStore(join(mvpDir, '.telegram-state.spectrum.json'),
    `${process.env.SPECTRUM_PROJECT_ID}:${token.split(':')[0]}:${ownerId}`);
  const inbox = new SpectrumInbox({ bridge, remember, transcribe: voice.transcribe,
    onOwnerSpace(space) { ownerSpace = space; startWorker(); } });
  for await (const [space, message] of app.messages) {
    if (stopping) break;
    try { await inbox.receive(space, message); }
    catch { console.error('Could not handle a message. Check local message state, the Windows worker and connection.'); }
  }
  stop();

}

main().catch(() => {
  // Avoid dumping errors that can embed the bot token in request URLs.
  console.error('Bridge could not start. Check your .env, bot token, Spectrum credentials, and Python setup.');
  process.exitCode = 1;
  stop();
});
