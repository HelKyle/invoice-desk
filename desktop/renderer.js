/* Renderer receives only a narrow preload API. No filesystem or network access. */
const api = window.invoiceDesk;
const $ = id => document.getElementById(id);
let state;
let actionPending = false;
let notice = '';

function text(id, value) { $(id).textContent = value; }
function show(id, visible) { $(id).hidden = !visible; }

function render(next) {
  state = next;
  const imported = next.import;
  const progress = next.progress || { total: 0, completed: 0, success: 0, failed: 0, rejected: 0 };
  const running = ['running', 'paused', 'stopping'].includes(next.phase);
  const finished = next.phase === 'finished';
  const percentage = progress.total ? Math.round(progress.completed / progress.total * 100) : 0;
  show('notice', Boolean(notice)); text('notice', notice);
  text('app-version', `Invoice Desk · ${next.version || '1.0.1'}`);
  show('copy-diagnostics', Boolean(next.diagnostic || next.engineStderr || next.phase === 'stopped' || running));
  text('file-title', imported?.filename || '导入你的发票 Excel');
  text('file-description', imported ? `已识别 ${imported.total} 张可查验发票${imported.duplicates ? ` · 已合并 ${imported.duplicates} 条重复记录` : ''}` : '拖入文件，或点击右侧选择。支持 .xlsx、.xlsm、.xls');
  $('import-button').disabled = next.busy || running || actionPending;
  text('import-button', imported ? '更换 Excel ↗' : '选择 Excel ↗');
  show('import-details', Boolean(imported));
  if (imported) {
    text('mapping-description', imported.mappings.map(mapping => `“${mapping.sheet}”已识别：${mapping.labels.invoice_number} / ${mapping.labels.issue_date} / ${mapping.labels.amount}`).join('；'));
    const issues = imported.issues || [];
    const warnings = imported.warnings || [];
    show('import-issues', issues.length + warnings.length > 0);
    text('issues-summary', issues.length ? `${issues.length} 行需修正，已跳过 · 点击查看${warnings.length ? '及金额口径' : ''}` : '已自动选择金额列 · 查看金额口径');
    $('issues-content').replaceChildren(...[...warnings, ...issues.map(issue => `${issue.location}：${issue.message}`)].map(message => {
      const p = document.createElement('p'); p.textContent = message; return p;
    }));
  }
  const labels = { empty: '等待导入', invalid: '请检查 Excel', ready: '已就绪', running: '自动查验中', paused: '已暂停', stopping: '正在停止', stopped: '可继续查验', finished: '本次查验完成' };
  text('phase-badge', labels[next.phase] || '等待导入');
  $('phase-badge').className = `phase-badge ${running ? 'running' : finished ? 'finished' : ''}`;
  show('empty-panel', !imported?.total);
  show('progress-panel', Boolean(imported?.total));
  for (const key of ['total', 'completed', 'success', 'rejected', 'failed']) text(key, progress[key] || 0);
  text('percentage', `${percentage}%`);
  $('progress-bar').value = percentage;
  text('progress-caption', finished ? '本次已处理' : '已处理发票');
  let title = '发票列表已就绪';
  let description = '点击开始，自动完成查验。';
  if (next.current) {
    title = next.message || '正在查验发票';
    description = `发票 ${next.current.number}`;
    if (next.waiting) description += ` · ${next.waiting} 秒后继续`;
  } else if (running) {
    title = next.phase === 'stopping' ? '正在保存进度' : next.phase === 'paused' ? '任务已暂停' : next.message || '正在准备查验';
    description = next.waiting ? `${next.waiting} 秒后查验下一张` : '自动处理过程中，你可以暂停或停止。';
  } else if (next.phase === 'stopped') {
    title = '进度已保存，可以继续';
    description = next.message || '继续时会跳过已保存的发票，接着处理未完成的部分。';
  } else if (finished) {
    title = progress.success === progress.total ? '全部发票已收好' : `已保存 ${progress.success} 张发票`;
    description = progress.rejected || progress.failed ? `${progress.rejected} 张返回不一致或查无此票，${progress.failed} 张因异常未完成。可查看原因或再次查验未完成部分。` : '打开文件夹即可查看，保存的都是最终发票详情图片。';
  }
  if (next.phase === 'paused') { title = '任务已暂停'; description = '当前发票处理完后暂停，点击继续即可恢复。'; }
  text('status-title', title); text('status-description', description);
  $('activity-dot').className = `activity-dot ${next.phase === 'running' ? 'active' : ''}`;
  show('retry-badge', Boolean(next.current?.retry));
  text('retry-badge', `重试 ${next.current?.retry || 0}/10`);
  const failures = next.failures || [];
  show('failure-details', failures.length > 0);
  text('failure-summary', `查看 ${failures.length} 张未取得发票的原因`);
  $('failure-list').replaceChildren(...failures.map(failure => {
    const li = document.createElement('li'); li.textContent = `${failure.number} · ${failure.location} · ${failure.reason}`; return li;
  }));
  show('stop-button', running); $('stop-button').disabled = next.phase === 'stopping' || actionPending;
  show('pause-button', running && next.phase !== 'stopping');
  text('pause-button', next.phase === 'paused' ? '继续' : '暂停'); $('pause-button').disabled = actionPending;
  show('start-button', !running);
  const incomplete = finished && (progress.failed || progress.rejected);
  text('start-button', incomplete ? '再次查验未完成部分 →' : next.phase === 'stopped' ? '继续查验 →' : finished ? '全部已完成 ✓' : '开始查验 →');
  $('start-button').disabled = !imported?.total || next.busy || actionPending || (finished && !incomplete);
  $('output-button').disabled = !next.task?.output || !progress.success;
  show('choose-output', next.phase === 'ready'); $('choose-output').disabled = actionPending || next.busy;
  text('output-description', next.task?.output ? `最终发票保存至：${next.task.output}` : '完成后仅保存发票详情图片。验证码、失败截图和中间表均不导出。');
}

async function action(fn) {
  if (actionPending) return;
  actionPending = true; notice = '';
  if (state) render(state);
  try {
    const result = await fn();
    if (result?.error) notice = result.error;
  } catch { notice = '操作未完成，请重试。'; }
  finally {
    actionPending = false;
    const current = await api.getState();
    if (current?.error) { notice = current.error; render(state); }
    else render(current);
  }
}

$('import-button').addEventListener('click', () => action(() => api.chooseExcel()));
$('start-button').addEventListener('click', () => action(() => api.start(state.phase === 'finished')));
$('pause-button').addEventListener('click', () => action(() => state.phase === 'paused' ? api.resume() : api.pause()));
$('stop-button').addEventListener('click', () => action(() => api.stop()));
$('output-button').addEventListener('click', () => action(() => api.openOutput()));
$('choose-output').addEventListener('click', () => action(() => api.chooseOutput()));
$('copy-diagnostics').addEventListener('click', () => action(async () => {
  const result = await api.copyDiagnostics();
  if (result?.copied) notice = '诊断信息已复制，可粘贴给开发者；不含发票内容和验证码。';
  return result;
}));
document.addEventListener('dragover', event => event.preventDefault());
document.addEventListener('drop', event => event.preventDefault());
$('drop-zone').addEventListener('dragover', event => { event.preventDefault(); if (!state?.busy) $('drop-zone').classList.add('dragover'); });
$('drop-zone').addEventListener('dragleave', () => $('drop-zone').classList.remove('dragover'));
$('drop-zone').addEventListener('drop', event => {
  event.preventDefault(); $('drop-zone').classList.remove('dragover');
  if (state?.busy || actionPending) return;
  const files = [...event.dataTransfer.files];
  if (files.length !== 1) { notice = '请每次导入一个 Excel 文件。'; render(state); return; }
  action(() => api.importDroppedFile(files[0]));
});
api.onState(render);
api.getState().then(render);
