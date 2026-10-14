const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
run(process.platform === 'win32' ? 'python' : 'python3', ['-m', 'venv', '.venv']);
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
run(python, ['-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '-r', 'requirements-desktop.txt']);
run(python, ['-m', 'playwright', 'install', 'chromium', '--only-shell']);
fs.mkdirSync(path.join(root, 'test-artifacts'), { recursive: true });
if (process.env.GITHUB_ENV) fs.appendFileSync(process.env.GITHUB_ENV, `INVOICE_PYTHON=${python}\n`);
