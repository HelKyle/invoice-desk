const os = require('node:os');

function redact(value, limit = 4000) {
  let text = String(value);
  for (const name of ['HOME', 'USERPROFILE']) {
    if (process.env[name]) text = text.split(process.env[name]).join('<user>');
  }
  text = text.replace(/\b\d{16,}\b/g, '<invoice>')
    .replace(/(https?:\/\/[^\s?'"\\]+)\?[^\s'"\\]+/g, '$1?<redacted>')
    .replace(/((?:token|authorization|cookie|secret|password|api[_-]?key)\s*[:=]\s*)[^\s,;]+/gi, '$1<redacted>');
  return limit ? text.slice(-limit) : text;
}

function report(state, version) {
  return JSON.stringify({
    app_version: version, platform: process.platform, architecture: process.arch, os_release: os.release(),
    cpu_count: os.cpus().length, cpu_model: os.cpus()[0]?.model, memory_total_mb: Math.round(os.totalmem() / 1024 ** 2), memory_free_mb: Math.round(os.freemem() / 1024 ** 2),
    phase: state.phase, stage: state.message, last_failure: state.diagnostic || null, engine_stderr: state.engineStderr || '',
  }, (_key, value) => typeof value === 'string' ? redact(value, 0) : value, 2);
}
module.exports = { redact, report };
