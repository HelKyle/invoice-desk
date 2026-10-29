"""JSON-lines desktop protocol, persistent progress and hard per-attempt watchdog."""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from engine.excel import read_workbook
from engine.invoice import image_name
from engine.diagnostics import failure_report, human_reason, resources
from engine.startup_debug import trace
from engine.worker_pipe import ParentConnection, WorkerConnection, launch_worker

MAX_RETRIES = 2
ATTEMPT_TIMEOUT = 150
STARTUP_STAGES = {"正在启动查验子进程", "查验子进程已启动，正在等待初始化", "正在加载 OCR 运行库",
                  "正在加载本地识别模型（1/2）", "正在加载本地识别模型（2/2）", "正在启动浏览器驱动"}


def emit(event_type, **fields):
    print(json.dumps({"type": event_type, **fields}, ensure_ascii=False), flush=True)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class Controls:
    def __init__(self):
        self.commands = queue.Queue()
        self.paused = False
        self.cancelled = False
        self.last_heartbeat = 0
        self.input_closed = False
        self.active = False
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in sys.stdin:
            try:
                self.commands.put(json.loads(line).get("command"))
            except (ValueError, AttributeError):
                continue
        self.commands.put("stdin-eof")

    def pump(self):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "pause":
                self.paused = True
                emit("control", paused=True)
            elif command == "resume":
                self.paused = False
                emit("control", paused=False)
            elif command == "cancel":
                self.cancelled = True
            elif command == "stdin-eof":
                self.input_closed = True
        # Bootstrap/probes may run with closed stdin. Only an active task uses EOF as parent disconnect.
        if self.active and self.input_closed:
            self.cancelled = True
        if time.monotonic() - self.last_heartbeat > 5:
            emit("heartbeat")
            self.last_heartbeat = time.monotonic()
        return not self.cancelled

    def activate(self):
        self.active = True
        return self.pump()

    def wait(self, seconds):
        remaining = seconds
        while self.pump():
            if self.paused:
                time.sleep(0.1)
                continue
            if remaining <= 0:
                return True
            emit("waiting", seconds=max(1, int(remaining + 0.999)))
            interval = min(1, remaining)
            time.sleep(interval)
            remaining -= interval
        return False


def kill_worker(process):
    if process is None or process.pid is None:
        return
    # Playwright has grandchildren; killing only the Python process leaks Chromium on Windows.
    import psutil
    descendants = []
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.Error:
                pass
        try:
            parent.kill()
        except psutil.Error:
            pass
        psutil.wait_procs(descendants, timeout=3)
    except psutil.Error:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


class WorkerStartupError(RuntimeError):
    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


class WorkerClient:
    def __init__(self, controls, timeout=ATTEMPT_TIMEOUT, worker_command=None, startup_timeout=90):
        self.controls = controls
        self.timeout = timeout
        self.process = None
        self.connection = None
        self.worker_command = worker_command
        self.startup_timeout = startup_timeout
        self.last_stage = "正在启动查验子进程"
        self.last_diagnostic = None
        self.ready = False
        self.worker_runtime = None

    def receive(self, expected, timeout):
        deadline = time.monotonic() + timeout
        while self.controls.pump() and time.monotonic() < deadline:
            if self.connection.poll(0.1):
                try:
                    event = self.connection.recv()
                except EOFError:
                    try:
                        self.process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
                    raise RuntimeError(f"查验子进程异常退出（退出码 {self.process.poll()}）") from None
                if event["type"] == "stage":
                    self.last_stage = event["message"]
                    trace("worker-stage", child_pid=self.process.pid, message=self.last_stage)
                    emit("stage", message=event["message"])
                elif event["type"] == expected:
                    return event
                elif event["type"] == "error":
                    self.last_diagnostic = event.get("diagnostic")
                    raise RuntimeError(event["message"])
            if self.process.poll() is not None:
                # Drain final protocol messages before reporting exit; errors/results can already be queued.
                self.connection.read_thread.join(timeout=0.1)
                if self.connection.poll(0):
                    continue
                raise RuntimeError(f"查验子进程异常退出（退出码 {self.process.returncode}）")
        if self.controls.cancelled:
            raise InterruptedError("已停止")
        raise TimeoutError(f"{self.last_stage}超时，正在重启查验引擎")

    def start(self):
        if self.ready and self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.last_diagnostic = None
        self.last_stage = "正在启动查验子进程"
        emit("stage", message=self.last_stage)
        trace("worker-spawn-begin", executable=sys.executable, transport="stdio")
        try:
            self.process = launch_worker(self.worker_command)
            self.connection = ParentConnection(self.process)
            trace("worker-spawn-end", child_pid=self.process.pid, transport="stdio")
            self.last_stage = "查验子进程已启动，正在等待初始化"
            emit("stage", message=self.last_stage)
            ready = self.receive("ready", self.startup_timeout)
            self.worker_runtime = ready
            self.ready = True
            trace("worker-initialized", child_pid=self.process.pid, isolated=ready.get("isolated"), transport="stdio")
        except InterruptedError:
            self.close()
            raise
        except (OSError, EOFError, RuntimeError, TimeoutError) as exc:
            diagnostic = self.diagnostic(exc)
            self.close()
            raise WorkerStartupError(human_reason(exc, str(exc) or "查验子进程初始化失败"), diagnostic) from exc

    def diagnostic(self, exc):
        diagnostic = self.last_diagnostic or failure_report(exc, self.last_stage)
        diagnostic["exit_code"] = self.process.poll() if self.process else None
        if diagnostic["exit_code"] is not None:
            diagnostic["exit_status"] = f"0x{diagnostic['exit_code'] & 0xffffffff:08X}"
        diagnostic.setdefault("system", resources())
        return diagnostic

    def probe(self):
        self.start()
        self.connection.send({"command": "probe"})
        return self.receive("result", self.timeout)["result"]

    def request(self, invoice, output):
        if not self.ready:
            self.start()
        self.last_diagnostic = None
        try:
            self.last_stage = "正在等待查验子进程返回"
            self.connection.send({"command": "verify", "invoice": invoice, "output": str(output)})
            return self.receive("result", self.timeout)["result"]
        except InterruptedError:
            self.close()
            raise
        except (OSError, EOFError, RuntimeError, TimeoutError) as exc:
            trace("worker-request-error", error_type=type(exc).__name__, message=str(exc), stage=self.last_stage,
                  child_pid=self.process.pid if self.process else None, exit_code=self.process.poll() if self.process else None)
            diagnostic = self.diagnostic(exc)
            reason = human_reason(exc, str(exc) or "查验子进程连接中断")
            self.close()
            return {"kind": "retry", "reason": reason, "diagnostic": diagnostic}

    def close(self):
        self.ready = False
        if self.process:
            trace("worker-terminate", child_pid=self.process.pid, exit_code=self.process.poll())
            kill_worker(self.process)
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None


def retry_delay(result, retries):
    if result.get("rate_limited"):
        return min(120, 30 * retries)
    return min(20, 2 ** min(retries, 4))


def item_state(record):
    return {"kind": record.get("kind", "pending"), "attempts": record.get("attempts", 0), "reason": record.get("reason", "")}


def failure_item(invoice, record):
    return {"number": invoice["invoice_number"], "location": f"{invoice['sheet']} 第{invoice['excel_row']}行",
            "reason": record.get("reason", "未完成"), "attempts": record.get("attempts", 0), "kind": record.get("kind", "failed")}


def run_batch(parsed, output, state_path, controls, client, reset_failed=False, delay=10):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        state = {"fingerprint": parsed["fingerprint"], "items": {}}
    if state.get("fingerprint") != parsed["fingerprint"]:
        raise ValueError("任务数据发生变化，请重新导入 Excel")
    items = state["items"]
    if state.get("attempt_accounting") != "verification-v1":
        # Older releases charged bootstrap failures to invoice attempts. Only identifiable startup records migrate.
        for identifier, record in list(items.items()):
            if record.get("kind") not in ("success", "rejected") and (record.get("diagnostic") or {}).get("stage") in STARTUP_STAGES:
                items[identifier] = {"attempts": 0}
                trace("legacy-startup-budget-reset")
        state["attempt_accounting"] = "verification-v1"
    if reset_failed:
        for invoice in parsed["invoices"]:
            old = items.get(invoice["id"], {})
            if old.get("kind") != "success":
                items.pop(invoice["id"], None)
    def save():
        atomic_json(state_path, state)
    def counts():
        kinds = [items.get(i["id"], {}).get("kind") for i in parsed["invoices"]]
        return {"total": parsed["total"], "completed": sum(k in ("success", "rejected", "failed") for k in kinds),
                "success": kinds.count("success"), "rejected": kinds.count("rejected"), "failed": kinds.count("failed")}
    failures = []
    startup_failed = False
    emit("task-state", items={invoice["id"]: item_state(items.get(invoice["id"], {})) for invoice in parsed["invoices"]},
         retry_limit=MAX_RETRIES, **counts())
    try:
        for index, invoice in enumerate(parsed["invoices"]):
            if not controls.wait(0):
                break
            record = items.get(invoice["id"], {"attempts": 0})
            final = output / image_name(invoice)
            if record.get("kind") == "success" and (not final.is_file() or final.stat().st_size < 100):
                record.clear()
                record["attempts"] = 0
            if record.get("kind") in ("success", "rejected", "failed"):
                if record["kind"] != "success":
                    failures.append(failure_item(invoice, record))
                emit("progress", **counts())
                continue
            while record["attempts"] < MAX_RETRIES + 1:
                if not controls.wait(0):
                    break
                client.start()
                if not controls.activate():
                    break
                if not controls.wait(0):
                    break
                items[invoice["id"]] = record
                record["attempts"] += 1
                save()  # Persist before starting, so an app crash cannot reset the retry budget.
                emit("item-state", id=invoice["id"], **{**item_state(record), "kind": "checking"})
                emit("invoice", id=invoice["id"], number=invoice["invoice_number"], index=index + 1, retry=record["attempts"] - 1, **counts())
                result = client.request(invoice, output)
                record.update(result)
                save()
                emit("item-state", id=invoice["id"], **item_state(record))
                if result["kind"] in ("success", "rejected"):
                    break
                emit("attempt-failed", reason=result["reason"], diagnostic=result.get("diagnostic"))
                if record["attempts"] <= MAX_RETRIES:
                    seconds = retry_delay(result, record["attempts"])
                    emit("retry", number=invoice["invoice_number"], retry=record["attempts"], maximum=MAX_RETRIES, reason=result["reason"], seconds=seconds, diagnostic=result.get("diagnostic"))
                    if not controls.wait(seconds):
                        break
            if controls.cancelled:
                break
            if record.get("kind") not in ("success", "rejected"):
                record["kind"] = "failed"
                record.setdefault("reason", f"已达到本轮 {MAX_RETRIES} 次重试上限（共 {MAX_RETRIES + 1} 次尝试）")
            if record["kind"] != "success":
                    failures.append(failure_item(invoice, record))
            save()
            emit("item-state", id=invoice["id"], **item_state(record))
            emit("progress", **counts())
            if index + 1 < parsed["total"] and not controls.wait(delay):
                break
    except InterruptedError:
        controls.cancelled = True
    except WorkerStartupError as exc:
        startup_failed = True
        emit("error", message=f"{exc}。初始化未完成，未扣除发票重试次数；可点击继续查验。", diagnostic=exc.diagnostic)
    finally:
        client.close()
        for temporary in output.glob("*.partial.png"):
            temporary.unlink(missing_ok=True)
    if not startup_failed:
        emit("finished", cancelled=controls.cancelled, failures=failures, **counts())
    return not startup_failed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "run", "selftest", "startup-test", "worker"))
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--state")
    parser.add_argument("--reset-failed", action="store_true")
    args = parser.parse_args()
    trace("engine-command", command=args.command)
    try:
        if args.command == "worker":
            from engine.browser import worker
            worker(WorkerConnection())
            return
        if args.command == "startup-test":
            # Same direct-worker initialization and pipe as a real task, with an offline browser check.
            controls = Controls()
            client = WorkerClient(controls)
            try:
                result = client.probe()
                emit("ready", subprocess=True, transport="stdio", isolated=bool(sys.flags.isolated),
                     worker_isolated=client.worker_runtime.get("isolated"), **result)
            finally:
                client.close()
            return
        if args.command == "selftest":
            from engine.browser import BrowserEngine
            engine = BrowserEngine(lambda message: emit("stage", message=message))
            try:
                page = engine.prepare({"id": "selftest"})
                page.set_content("<title>invoice engine ready</title>")
                engine.models[0].classification(page.screenshot())
                engine.models[1].classification(page.screenshot())
                emit("ready", browser=True, models=2)
            finally:
                engine.close()
            return
        parsed = read_workbook(args.input)
        if args.command == "inspect":
            emit("imported", retry_limit=MAX_RETRIES, **parsed)
            return
        if not parsed["total"]:
            raise ValueError("没有可查验的发票，请修正 Excel 数据后重新导入")
        if not args.output or not args.state:
            raise ValueError("未指定结果目录或任务存储目录")
        controls = Controls()
        if not run_batch(parsed, args.output, args.state, controls, WorkerClient(controls), args.reset_failed):
            raise SystemExit(1)
    except Exception as exc:
        trace("engine-error", error_type=type(exc).__name__, message=str(exc))
        if args.command == "startup-test":
            emit("error", message=human_reason(exc, str(exc)), diagnostic=getattr(exc, "diagnostic", None) or failure_report(exc, client.last_stage))
            raise SystemExit(1)
        emit("error", message=str(exc) if isinstance(exc, (ValueError, PermissionError, FileNotFoundError)) else "无法处理该文件或任务，请确认文件未加密、未损坏，且结果目录可写")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
