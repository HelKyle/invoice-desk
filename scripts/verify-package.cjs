const fs = require('node:fs');
const path = require('node:path');
module.exports = async context => {
  const resources = context.electronPlatformName === 'darwin'
    ? path.join(context.appOutDir, '发票查验.app', 'Contents', 'Resources') : path.join(context.appOutDir, 'resources');
  const engine = path.join(resources, 'engine');
  const windows = context.electronPlatformName === 'win32';
  const executable = path.join(engine, windows ? 'python.exe' : 'invoice-engine');
  if (!fs.existsSync(executable)) throw new Error('安装包中缺少查验引擎');
  if (!fs.existsSync(path.join(engine, 'browsers'))) throw new Error('安装包中缺少离线浏览器');
  if (windows) {
    if (!fs.existsSync(path.join(engine, 'app', 'engine', 'worker_pipe.py'))) throw new Error('安装包缺少直接 worker 管道模块');
    for (const name of ['msvcp140.dll', 'msvcp140_1.dll', 'vcruntime140.dll']) {
      if (!fs.existsSync(path.join(engine, name))) throw new Error(`安装包缺少 Windows 运行库 ${name}`);
    }
    for (const name of ['common.onnx', 'common_old.onnx']) {
      if (!fs.existsSync(path.join(engine, 'Lib', 'site-packages', 'ddddocr', name))) throw new Error(`安装包缺少模型 ${name}`);
    }
  }
};
