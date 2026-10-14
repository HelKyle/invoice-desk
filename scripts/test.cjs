const { spawnSync } = require('node:child_process');
const path = require('node:path');
const python = process.env.INVOICE_PYTHON || path.join(__dirname, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const result = spawnSync(python, ['-m', 'unittest', 'discover', '-s', 'tests', '-v'], { cwd: path.join(__dirname, '..'), stdio: 'inherit' });
if (result.status !== 0) process.exit(result.status ?? 1);
const javascript = spawnSync(process.execPath, ['--test', 'tests/desktop-diagnostics.test.cjs'], { cwd: path.join(__dirname, '..'), stdio: 'inherit' });
process.exit(javascript.status ?? 1);
