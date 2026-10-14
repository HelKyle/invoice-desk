const { spawnSync } = require('node:child_process');
const path = require('node:path');
const root = path.join(__dirname, '..');
function run(script, args = []) {
  const result = spawnSync(process.execPath, [path.join(root, script), ...args], { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const windows = process.platform === 'win32';
run(windows ? 'scripts/build-windows.cjs' : 'scripts/build-engine.cjs');
run('node_modules/electron-builder/cli.js', [...(windows ? ['--win', '--x64'] : []), ...(process.argv.includes('--dir') ? ['--dir'] : []), '--publish', 'never']);
