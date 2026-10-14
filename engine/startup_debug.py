"""Opt-in startup traces, enabled before service imports and in spawned interpreters."""
import atexit
import faulthandler
import json
import os
import sys
import time
from pathlib import Path

_trace = None
_stacks = None


def trace(event, **fields):
    if _trace is None:
        return
    try:
        # Callers pass lifecycle metadata only, never invoices, CLI arguments or environment dumps.
        from engine.diagnostics import redact
        data = {"time": time.time(), "pid": os.getpid(), "event": event, **fields}
        data = {key: redact(value) if isinstance(value, str) else value for key, value in data.items()}
        _trace.write(json.dumps(data, ensure_ascii=False) + "\n")
        _trace.flush()
    except Exception:
        pass


def configure():
    global _trace, _stacks
    directory = os.environ.get("INVOICE_DEBUG_DIR")
    if not directory or _trace is not None:
        return
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        _trace = (directory / f"python-{os.getpid()}.jsonl").open("a", encoding="utf-8")
        _stacks = (directory / f"python-{os.getpid()}-stacks.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_stacks, all_threads=True)
        faulthandler.dump_traceback_later(30, repeat=True, file=_stacks)
        trace("interpreter-entry", parent_pid=os.getppid(), executable=sys.executable,
              python=sys.version, isolated=bool(sys.flags.isolated), import_paths=";".join(sys.path))
        atexit.register(shutdown)
    except Exception as exc:
        print(f"[DEBUG-startup] Cannot initialize debug logs: {type(exc).__name__}", file=sys.stderr, flush=True)


def shutdown():
    trace("interpreter-exit")
    faulthandler.cancel_dump_traceback_later()
    # Keep the files open for faulthandler until the interpreter has completely shut down.
