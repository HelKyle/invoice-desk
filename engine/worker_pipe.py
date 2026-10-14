"""Direct Python worker launch and a JSON-lines pipe; no multiprocessing bootstrap handles."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from engine.diagnostics import redact


def worker_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "worker"]
    entry = Path(__file__).resolve().parent.parent / "desktop_engine.py"
    return [sys.executable, "-u", "-X", "faulthandler", str(entry), "worker"]


def launch_worker(command=None):
    return subprocess.Popen(
        command or worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


class ParentConnection:
    def __init__(self, process):
        self.process = process
        self.events = queue.Queue()
        self.pending = None
        self.read_thread = threading.Thread(target=self.read, daemon=True)
        self.stderr_thread = threading.Thread(target=self.read_stderr, daemon=True)
        self.read_thread.start()
        self.stderr_thread.start()

    def read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # Native libraries may print outside the protocol.
                if isinstance(event, dict) and isinstance(event.get("type"), str):
                    self.events.put(("event", event))
        finally:
            self.process.stdout.close()
            self.events.put(("eof", None))

    def read_stderr(self):
        try:
            for line in self.process.stderr:
                sys.stderr.write(redact(line) + ("" if line.endswith("\n") else "\n"))
                sys.stderr.flush()
        except OSError:
            pass
        finally:
            self.process.stderr.close()

    def poll(self, timeout):
        if self.pending is not None:
            return True
        try:
            self.pending = self.events.get(timeout=timeout)
            return True
        except queue.Empty:
            return False

    def recv(self):
        kind, event = self.pending if self.pending is not None else self.events.get()
        self.pending = None
        if kind == "eof":
            raise EOFError("worker stdout closed")
        return event

    def send(self, request):
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.read_thread.join(timeout=1)
        self.stderr_thread.join(timeout=1)
        # The readers own their streams. Closing from this thread can block forever on a
        # read lock if a crashed worker's descendant still has a copy of the pipe handle.


class WorkerConnection:
    def send(self, event):
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def recv(self):
        line = sys.stdin.readline()
        if not line:
            raise EOFError("worker control pipe closed")
        return json.loads(line)

    def close(self):
        sys.stdout.flush()
