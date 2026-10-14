const test = require('node:test');
const assert = require('node:assert/strict');
const { report, redact } = require('../desktop/diagnostics.cjs');

test('support report omits imported workbook and invoice contents and stays valid JSON', () => {
  const state = {
    phase: 'running', message: '正在启动查验子进程',
    import: { filename: 'private.xlsx' }, current: { number: '00000000000000000001' },
    task: { source: '/private/payments/private.xlsx' },
    diagnostic: { stage: '正在加载 OCR 运行库', error_type: 'ImportError', message: 'DLL load failed', exit_code: 1 },
    engineStderr: 'x'.repeat(4000),
  };
  const copied = report(state, '1.0.1');
  const parsed = JSON.parse(copied);
  assert.equal(parsed.app_version, '1.0.1');
  assert.equal(parsed.last_failure.exit_code, 1);
  assert.doesNotMatch(copied, /private.xlsx|00000000000000000001/);
});

test('engine error output masks invoice identifiers and URL credentials', () => {
  const redacted = redact('00000000000000000001 token=secret123 https://tax.test/fail?session=abc');
  assert.doesNotMatch(redacted, /00000000000000000001|secret123|session=abc/);
  assert.match(redacted, /tax.test/);
});

test('redacting a credential at the end of a field preserves the report JSON', () => {
  const copied = report({ phase: 'stopped', message: 'failure', engineStderr: 'token=secret123' }, '1.0.1');
  const parsed = JSON.parse(copied);
  assert.equal(parsed.engine_stderr, 'token=<redacted>');
});
