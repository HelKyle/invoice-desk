const { launchEngine } = require('./engine-client.cjs');

async function startupProbe(config, closeStdin = false) {
  let ready;
  let failure;
  let stage;
  const client = launchEngine(config, ['startup-test'], event => {
    if (event.type === 'ready') ready = event;
    if (event.type === 'error') failure = event;
    if (event.type === 'stage') stage = event.message;
  });
  if (closeStdin) client.child.stdin.end();
  const timer = setTimeout(() => client.kill(), 120000);
  try {
    const result = await client.completion;
    const expectsIsolated = config.packaged && process.platform === 'win32';
    return {
      ok: result.code === 0 && ready?.transport === 'stdio' && ready?.browser === true && ready?.models === 2 && (!expectsIsolated || (ready?.isolated === true && ready?.worker_isolated === true)),
      parent: 'electron', packaged: config.packaged, platform: process.platform,
      stdin_closed_at_launch: closeStdin, expects_isolated: expectsIsolated,
      ready: ready || null, failure: failure || null, last_stage: stage,
      exit_code: result.code, signal: result.signal, stderr: result.diagnostic,
    };
  } finally {
    clearTimeout(timer);
    client.kill();
  }
}

module.exports = { startupProbe };
