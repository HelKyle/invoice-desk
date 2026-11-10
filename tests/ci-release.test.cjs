const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { releaseNotes, windowsAssets } = require('../scripts/ci-release.cjs');
const version = require('../package.json').version;

test('selects one Windows installer for a Windows-only release', t => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-release-'));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const installer = `InvoiceDesk-${version}-win-x64.exe`;
  fs.writeFileSync(path.join(cwd, installer), 'tested installer');
  assert.deepEqual(windowsAssets(cwd), [installer]);
  const notes = releaseNotes('HelKyle/invoice-desk', '0'.repeat(40), '12', '123456');
  assert.match(notes, /Windows 10\/11 x64/);
  assert.doesNotMatch(notes, /macOS/);
});
