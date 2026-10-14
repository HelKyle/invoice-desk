"""Download and checksum the official embedded Python; no system Python needed on Windows."""
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.12.10"  # ddddocr 1.5.6 requires Python < 3.13.
destination = Path(sys.argv[1])
for stale in ("python313.dll", "python313.zip", "python313._pth"):
    (destination / stale).unlink(missing_ok=True)
url = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"
data = urllib.request.urlopen(url, timeout=120).read()
if hashlib.md5(data).hexdigest() != "fe8ef205f2e9c3ba44d0cf9954e1abd3":
    raise RuntimeError("Windows Python 校验和与 Python.org 发布信息不符")
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    if not {"python.exe", "python312.dll", "python312.zip"} <= set(archive.namelist()):
        raise RuntimeError("Windows Python 下载内容不完整")
    archive.extractall(destination)
(destination / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\napp\nimport site\n", encoding="utf-8")
(destination / "runtime.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  python-{VERSION}-embed-amd64.zip\n", encoding="utf-8")
