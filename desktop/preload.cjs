const { contextBridge, ipcRenderer, webUtils } = require('electron');
contextBridge.exposeInMainWorld('invoiceDesk', {
  getState: () => ipcRenderer.invoke('workspace:get'),
  chooseExcel: () => ipcRenderer.invoke('excel:choose'),
  importDroppedFile: file => ipcRenderer.invoke('excel:drop', webUtils.getPathForFile(file)),
  start: resetFailed => ipcRenderer.invoke('task:start', resetFailed === true),
  pause: () => ipcRenderer.invoke('task:pause'),
  resume: () => ipcRenderer.invoke('task:resume'),
  stop: () => ipcRenderer.invoke('task:stop'),
  chooseOutput: () => ipcRenderer.invoke('output:choose'),
  openOutput: () => ipcRenderer.invoke('output:open'),
  copyDiagnostics: () => ipcRenderer.invoke('diagnostics:copy'),
  openDiagnostics: () => ipcRenderer.invoke('diagnostics:open'),
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('workspace:changed', listener);
    return () => ipcRenderer.removeListener('workspace:changed', listener);
  },
});
