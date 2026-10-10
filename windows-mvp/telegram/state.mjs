import { readFileSync, writeFileSync, renameSync } from 'node:fs';

// Spectrum delivers messages at least once; retain the last 1,000 accepted ids.
export function messageStore(path, identity) {
  let ids = new Set();
  try {
    const data = JSON.parse(readFileSync(path, 'utf8'));
    if (data.identity === identity) {
      if (!Array.isArray(data.ids) || data.ids.some(id => typeof id !== 'string')) throw new Error('Invalid message ids');
      ids = new Set(data.ids);
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw new Error('Telegram message state could not be read. Check it before restarting.');
  }
  return id => {
    if (ids.has(id)) return false;
    const next = new Set(ids); next.add(id);
    if (next.size > 1000) next.delete(next.values().next().value);
    writeFileSync(`${path}.tmp`, JSON.stringify({ identity, ids: [...next] }), { mode: 0o600 });
    renameSync(`${path}.tmp`, path);
    ids = next;
    return true;
  };
}
