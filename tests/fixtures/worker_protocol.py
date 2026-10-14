"""Faulty real subprocesses for transport/timeout/budget regression tests."""
import json
import os
import subprocess
import sys
import time

mode = sys.argv[1]
if mode == "silent":
    time.sleep(60)
if mode == "startup-error":
    print(json.dumps({"type": "error", "message": "DLL load failed", "diagnostic": {"stage": "正在加载 OCR 运行库", "error_type": "ImportError", "message": "DLL load failed"}}), flush=True)
    sys.exit(1)
print(json.dumps({"type": "ready"}), flush=True)
request = json.loads(sys.stdin.readline())
if mode == "crash":
    os._exit(7)
if mode == "hang":
    time.sleep(60)
if mode in ("grandchild", "orphan-pipe"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)
    print(json.dumps({"type": "stage", "message": f"grandchild={child.pid}"}), flush=True)
    if mode == "orphan-pipe":
        os._exit(7)
    time.sleep(60)
if mode == "success":
    from pathlib import Path
    output = Path(request['output']) / (request['invoice']['invoice_number'] + '.png')
    output.write_bytes(b'final-invoice' * 20)
    print(json.dumps({"type": "result", "result": {"kind": "success"}}), flush=True)
    sys.stdin.readline()
