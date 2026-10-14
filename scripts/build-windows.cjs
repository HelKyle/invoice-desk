/* Build a complete Windows engine on any host with official embeddable Python and Windows wheels. */
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const suffix = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python';
const isolated = path.join(root, '.venv-engine', suffix);
const python = process.env.INVOICE_PYTHON || (fs.existsSync(isolated) ? isolated : path.join(root, '.venv', suffix));
const destination = path.join(root, 'build', 'engine-win');
function run(args, env = {}) {
  const result = spawnSync(python, args, { cwd: root, stdio: 'inherit', env: { ...process.env, ...env } });
  if (result.status !== 0) throw new Error(`Windows 引擎打包失败：${result.error?.message || result.status}`);
}
fs.mkdirSync(destination, { recursive: true });
run(['scripts/windows-runtime.py', destination]);
run(['-m', 'pip', 'install', '--upgrade', '--target', path.join(destination, 'Lib', 'site-packages'),
  '--platform', 'win_amd64', '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312',
  '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '-r', 'requirements-desktop.txt']);
run(['-m', 'playwright', 'install', 'chromium', '--only-shell'], {
  PLAYWRIGHT_HOST_PLATFORM_OVERRIDE: 'win64', PLAYWRIGHT_BROWSERS_PATH: path.join(destination, 'browsers'),
});
fs.mkdirSync(path.join(destination, 'app'), { recursive: true });
for (const filename of ['desktop_engine.py', 'invoice_batch.py']) fs.copyFileSync(path.join(root, filename), path.join(destination, 'app', filename));
fs.cpSync(path.join(root, 'engine'), path.join(destination, 'app', 'engine'), { recursive: true, filter: source => !source.includes('__pycache__') });
const crt = spawnSync(process.execPath, ['scripts/bundle-vcredist.cjs', destination, python], { cwd: root, stdio: 'inherit' });
if (crt.status !== 0) throw new Error('Windows C++ 运行库打包失败');
run(['scripts/make-icon.py']);
const required = ['python.exe', 'python312.dll', 'Lib/site-packages/ddddocr/common.onnx', 'Lib/site-packages/ddddocr/common_old.onnx',
  'Lib/site-packages/playwright/driver/node.exe', 'app/desktop_engine.py', 'msvcp140.dll', 'msvcp140_1.dll'];
for (const filename of required) if (!fs.existsSync(path.join(destination, filename))) throw new Error(`缺少 Windows 资源：${filename}`);
run(['scripts/check-windows.py', destination]);
if (process.platform === 'win32') {
  const result = spawnSync(path.join(destination, 'python.exe'), ['-u', path.join(destination, 'app', 'desktop_engine.py'), 'selftest'], {
    cwd: destination, stdio: 'inherit', env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: path.join(destination, 'browsers') },
  });
  if (result.status !== 0) throw new Error('Windows 引擎自检失败');
}
console.log('Windows x64 引擎、OCR 模型和浏览器已准备完成。');
