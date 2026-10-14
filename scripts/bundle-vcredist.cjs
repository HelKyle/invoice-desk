const { getPath7za } = require('app-builder-lib/out/toolsets/7zip');
const { spawnSync } = require('node:child_process');
(async () => {
  const sevenzip = await getPath7za();
  const result = spawnSync(process.argv[3], ['scripts/extract-vcredist.py', process.argv[2], sevenzip], { stdio: 'inherit' });
  if (result.status !== 0) throw new Error('Windows C++ 运行库提取失败');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
