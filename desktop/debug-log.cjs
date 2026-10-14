const fs = require('node:fs');
const path = require('node:path');
const { redact } = require('./diagnostics.cjs');

function createDebugLog(directory) {
  if (!directory) return { directory: null, write() {} };
  fs.mkdirSync(directory, { recursive: true });
  const file = path.join(directory, 'desktop.jsonl');
  return {
    directory,
    write(event, fields = {}) {
      try {
        const record = { time: new Date().toISOString(), pid: process.pid, event, ...fields };
        const line = JSON.stringify(record, (_key, value) => typeof value === 'string' ? redact(value, 0) : value);
        fs.appendFileSync(file, `${line}\n`, 'utf8');
      } catch { /* Diagnostics must not interrupt a task if the disk is full. */ }
    },
  };
}

module.exports = { createDebugLog };
