const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const root = path.join(__dirname, '..');
let extracted;
try {
  let executable;
  if (process.platform === 'win32') {
    executable = path.join(root, 'release', 'win-unpacked', '发票查验.exe');
  } else {
    // Test the delivered ZIP, including signatures after extraction, rather than only the build directory.
    const version = require('../package.json').version;
    const archive = path.join(root, 'release', `InvoiceDesk-${version}-mac-arm64.zip`);
    extracted = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-packaged-'));
    const unzip = spawnSync('ditto', ['-x', '-k', archive, extracted], { stdio: 'inherit' });
    if (unzip.status !== 0) throw new Error('最终 macOS ZIP 解压失败');
    const app = path.join(extracted, '发票查验.app');
    const signature = spawnSync(process.execPath, ['scripts/verify-mac-signature.cjs', app], { cwd: root, stdio: 'inherit' });
    if (signature.status !== 0) throw new Error('最终 macOS ZIP 内的应用签名无效');
    executable = path.join(app, 'Contents', 'MacOS', '发票查验');
  }
  if (!fs.existsSync(executable)) throw new Error('CI 安装包中找不到应用入口');
  for (const script of ['scripts/startup-smoke.cjs', 'scripts/ui-smoke.cjs']) {
    const result = spawnSync(process.execPath, [script], {
      cwd: root, stdio: 'inherit', env: { ...process.env, INVOICE_APP_EXE: executable },
    });
    if (result.status !== 0) { process.exitCode = result.status ?? 1; break; }
  }
} finally {
  if (extracted) fs.rmSync(extracted, { recursive: true, force: true });
}
