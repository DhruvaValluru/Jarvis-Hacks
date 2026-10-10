import { readFileSync, writeFileSync, renameSync } from 'node:fs';

// Record before dispatch: restart never repeats an already accepted PC action.
// A command interrupted by a crash must be explicitly sent again by the user.
export function cursorStore(path, botId, ownerId) {
  const identity = `${botId}:${ownerId}`;
  return {
    read() {
      try {
        const data = JSON.parse(readFileSync(path, 'utf8'));
        return data.identity === identity && Number.isSafeInteger(data.offset) ? data.offset : undefined;
      } catch (error) {
        if (error.code === 'ENOENT') return undefined;
        throw new Error('Telegram cursor file could not be read. Check it before restarting.');
      }
    },
    write(offset) {
      writeFileSync(`${path}.tmp`, JSON.stringify({ identity, offset }), { mode: 0o600 });
      renameSync(`${path}.tmp`, path);
    },
  };
}
