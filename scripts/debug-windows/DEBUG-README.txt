Windows x64 启动诊断包

1. 将整个 ZIP 解压到本地文件夹，再双击 InvoiceDesk-Debug.exe。
   请勿直接在 ZIP 内运行脚本，也不要单独将 check-startup.cmd 移到其他目录；
   check-startup.cmd、InvoiceDesk-Debug.exe、resources 文件夹必须在同一层。
2. 重新导入 Excel 并开始查验；卡住后等待至少 60 秒。
3. 点击底部“打开 debug 日志”，将当前打开的整个日志文件夹打包给开发者。
   界面打不开时，在资源管理器输入 %APPDATA%\发票查验 Debug\debug-logs。
4. 推荐双击 check-startup-electron.cmd 检查实际启动条件。它由 Electron
   直接启动服务，使用 pipe stdin/stdout/stderr、windowsHide 和包内 isolated
   Python；再通过显式 JSON 管道启动 worker，检查 ready/result、OCR 和离线
   Chromium。会分别测试保持 stdin 和立即关闭 stdin，两项均应退出码 0。
   每次会话的 startup-probe.json 应为 ok: true、transport: stdio、
   isolated: true、worker_isolated: true。请提供脚本显示的整个日志目录，
   位于 %LOCALAPPDATA%\InvoiceDesk-Debug-Electron-Probe 下。
5. check-startup.cmd 仍可在 cmd 下单独检查直接 worker 初始化和离线浏览器；
   它不能替代 Electron 父进程检查。日志在 %LOCALAPPDATA%\InvoiceDesk-Debug-Probe。

1.0.2 改为直接启动 worker，不再使用 multiprocessing spawn。
首次初始化失败会停止任务，不扣除发票尝试次数、不写入任务账本。
旧账本中有明确初始化阶段诊断的失败预算，在恢复时会重置；已成功发票保留。

desktop.jsonl：Electron 启动、引擎阶段、标准错误、退出码。
python-进程号.jsonl：父子进程启动、ddddocr / Playwright 导入起止、初始化阶段。
python-进程号-stacks.log：每 30 秒抓取所有 Python 线程的调用栈及原生崩溃记录。
console.log：cmd 检查脚本的输出（仅 check-startup.cmd 生成）。
startup-probe.json：真实 Electron 父进程的初始化回归结果。

Debug 版使用单独的用户数据目录，需要重新导入文件。
日志不主动采集发票内容、金额、验证码、完整命令参数或环境变量；
JSON 日志会隐藏用户目录、长发票号及常见凭证。Python 原始调用栈可能
包含组件安装路径。请只提供日志文件夹，无需提供 workspace.json 或 tasks。

此包用于采集故障现场；Windows Server 2022 上的实际卡点需根据日志确认。
