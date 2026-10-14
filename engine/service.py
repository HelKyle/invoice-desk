"""JSON-lines desktop protocol, persistent progress and hard per-attempt watchdog."""
import argparse
import json
import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from pathlib import Path

from engine.excel import read_workbook
from engine.diagnostics import failure_report, human_reason, resources

MAX_RETRIES = 10
ATTEMPT_TIMEOUT = 150


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
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in sys.stdin:
            try:
                self.commands.put(json.loads(line).get("command"))
            except (ValueError, AttributeError):
                continue
        self.commands.put("cancel")  # Parent disappeared: do not leave browsers running.

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
        if time.monotonic() - self.last_heartbeat > 5:
            emit("heartbeat")
            self.last_heartbeat = time.monotonic()
        return not self.cancelled

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
    process.join(timeout=3)


class WorkerClient:
    def __init__(self, controls, timeout=ATTEMPT_TIMEOUT, worker_target=None, startup_timeout=90):
        self.controls = controls
        self.timeout = timeout
        self.process = None
        self.connection = None
        self.worker_target = worker_target
        self.startup_timeout = startup_timeout
        self.last_stage = "正在启动查验子进程"
        self.last_diagnostic = None

    def receive(self, expected, timeout):
        deadline = time.monotonic() + timeout
        while self.controls.pump() and time.monotonic() < deadline:
            if self.connection.poll(0.1):
                try:
                    event = self.connection.recv()
                except EOFError:
                    self.process.join(timeout=0.5)
                    raise RuntimeError(f"查验子进程异常退出（退出码 {self.process.exitcode}）") from None
                if event["type"] == "stage":
                    self.last_stage = event["message"]
                    emit("stage", message=event["message"])
                elif event["type"] == expected:
                    return event
                elif event["type"] == "error":
                    self.last_diagnostic = event.get("diagnostic")
                    raise RuntimeError(event["message"])
            if not self.process.is_alive():
                raise RuntimeError(f"查验子进程异常退出（退出码 {self.process.exitcode}）")
        if self.controls.cancelled:
            raise InterruptedError("已停止")
        raise TimeoutError(f"{self.last_stage}超时，正在重启查验引擎")

    def request(self, invoice, output):
        from engine.browser import worker
        self.last_diagnostic = None
        try:
            if self.process is None or not self.process.is_alive():
                self.close()
                context = mp.get_context("spawn")
                self.connection, child_connection = context.Pipe()
                self.process = context.Process(target=self.worker_target or worker, args=(child_connection,), daemon=True)
                self.last_stage = "正在启动查验子进程"
                emit("stage", message=self.last_stage)
                self.process.start()
                child_connection.close()
                self.receive("ready", self.startup_timeout)
            self.last_stage = "正在等待查验子进程返回"
            self.connection.send({"command": "verify", "invoice": invoice, "output": str(output)})
            return self.receive("result", self.timeout)["result"]
        except InterruptedError:
            self.close()
            raise
        except (OSError, EOFError, RuntimeError, TimeoutError) as exc:
            diagnostic = self.last_diagnostic or failure_report(exc, self.last_stage)
            diagnostic["exit_code"] = self.process.exitcode if self.process else None
            if diagnostic["exit_code"] is not None:
                diagnostic["exit_status"] = f"0x{diagnostic['exit_code'] & 0xffffffff:08X}"
            diagnostic.setdefault("system", resources())
            reason = human_reason(exc, str(exc) or "查验子进程连接中断")
            self.close()
            return {"kind": "retry", "reason": reason, "diagnostic": diagnostic}

    def close(self):
        if self.process:
            kill_worker(self.process)
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None


def retry_delay(result, retries):
    if result.get("rate_limited"):
        return min(120, 30 * retries)
    return min(20, 2 ** min(retries, 4))


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
    try:
        for index, invoice in enumerate(parsed["invoices"]):
            if not controls.wait(0):
                break
            record = items.setdefault(invoice["id"], {"attempts": 0})
            final = output / f"{invoice['invoice_number']}.png"
            if record.get("kind") == "success" and (not final.is_file() or final.stat().st_size < 100):
                record.clear()
                record["attempts"] = 0
            if record.get("kind") in ("success", "rejected", "failed"):
                if record["kind"] != "success":
                    failures.append({"number": invoice["invoice_number"], "location": f"{invoice['sheet']} 第{invoice['excel_row']}行", "reason": record.get("reason", "未完成")})
                emit("progress", **counts())
                continue
            while record["attempts"] < MAX_RETRIES + 1:
                if not controls.wait(0):
                    break
                record["attempts"] += 1
                save()  # Persist before starting, so an app crash cannot reset the retry budget.
                emit("invoice", number=invoice["invoice_number"], index=index + 1, retry=record["attempts"] - 1, **counts())
                result = client.request(invoice, output)
                record.update(result)
                save()
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
                record.setdefault("reason", "已达到十次重试上限")
            if record["kind"] != "success":
                failures.append({"number": invoice["invoice_number"], "location": f"{invoice['sheet']} 第{invoice['excel_row']}行", "reason": record["reason"]})
            save()
            emit("progress", **counts())
            if index + 1 < parsed["total"] and not controls.wait(delay):
                break
    except InterruptedError:
        controls.cancelled = True
    finally:
        client.close()
        for temporary in output.glob("*.partial.png"):
            temporary.unlink(missing_ok=True)
        save()
    emit("finished", cancelled=controls.cancelled, failures=failures, **counts())


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "run", "selftest"))
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--state")
    parser.add_argument("--reset-failed", action="store_true")
    args = parser.parse_args()
    try:
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
            emit("imported", **{k: v for k, v in parsed.items() if k != "invoices"})
            return
        if not parsed["total"]:
            raise ValueError("没有可查验的发票，请修正 Excel 数据后重新导入")
        if not args.output or not args.state:
            raise ValueError("未指定结果目录或任务存储目录")
        controls = Controls()
        run_batch(parsed, args.output, args.state, controls, WorkerClient(controls), args.reset_failed)
    except Exception as exc:
        emit("error", message=str(exc) if isinstance(exc, (ValueError, PermissionError, FileNotFoundError)) else "无法处理该文件或任务，请确认文件未加密、未损坏，且结果目录可写")
        raise SystemExit(1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
