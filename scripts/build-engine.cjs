const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const venv = path.join(root, '.venv-engine');
const python = path.join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
function run(command, args, env = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', env: { ...process.env, ...env } });
  if (result.status !== 0) throw new Error(`${command} 执行失败：${result.error?.message || result.status}`);
}
if (!fs.existsSync(python)) {
  const bootstrap = process.env.INVOICE_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  run(bootstrap, ['-m', 'venv', venv]);
}
run(python, ['-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '-r', 'requirements-desktop.txt', 'pyinstaller==6.22.3']);
const browsers = path.join(root, 'build', 'browsers');
run(python, ['-m', 'playwright', 'install', 'chromium', '--only-shell'], { PLAYWRIGHT_BROWSERS_PATH: browsers });
run(python, ['scripts/make-icon.py']);
run(python, ['-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'invoice-engine',
  '--distpath', 'build/engine', '--workpath', 'build/pyinstaller', '--specpath', 'build',
  '--collect-all', 'ddddocr', '--collect-all', 'onnxruntime', '--collect-all', 'playwright', '--collect-all', 'cv2',
  'desktop_engine.py']);
const destination = path.join(root, 'build', 'engine', 'invoice-engine', 'browsers');
fs.cpSync(browsers, destination, { recursive: true });
run(path.join(root, 'build', 'engine', 'invoice-engine', process.platform === 'win32' ? 'invoice-engine.exe' : 'invoice-engine'), ['selftest'], { PLAYWRIGHT_BROWSERS_PATH: destination });
console.log('查验引擎及离线依赖已打包并通过自检。');
