"""App-local MSVC runtime from Microsoft's official redistributable. No admin install needed."""
import hashlib
import shutil
import struct
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

destination, sevenzip = Path(sys.argv[1]), sys.argv[2]
cache = Path("build/vcredist")
cache.mkdir(parents=True, exist_ok=True)
archive = Path("build/vc_redist.x64.exe")
if not archive.exists():
    archive.write_bytes(urllib.request.urlopen("https://aka.ms/vs/17/release/vc_redist.x64.exe", timeout=120).read())
data = archive.read_bytes()
cabs = []
offset = 0
while True:
    offset = data.find(b"MSCF", offset)
    if offset < 0:
        break
    size = struct.unpack_from("<I", data, offset + 8)[0]
    if 36 < size < len(data) - offset and data[offset + 24:offset + 26] == b"\x03\x01":
        cabinet = cache / f"{offset}.cab"
        cabinet.write_bytes(data[offset:offset + size])
        cabs.append(cabinet)
    offset += 4
if len(cabs) != 2:
    raise RuntimeError("Microsoft 运行库资源格式变化，请检查构建脚本")


def extract(source, output):
    subprocess.run([sevenzip, "x", "-y", str(source), f"-o{output}"], stdout=subprocess.DEVNULL, check=True)


manifest_dir, payload_dir, runtime_dir = cache / "manifest", cache / "payloads", cache / "runtime"
extract(cabs[0], manifest_dir)
extract(cabs[1], payload_dir)
manifest = ET.parse(manifest_dir / "0")
payload = next(e for e in manifest.iter() if e.tag.endswith("Payload") and e.get("FilePath") == "packages\\vcRuntimeMinimum_amd64\\cab1.cab")
cabinet = payload_dir / payload.attrib["SourcePath"]
if hashlib.sha1(cabinet.read_bytes()).hexdigest().upper() != payload.attrib["Hash"]:
    raise RuntimeError("Microsoft 运行库资源校验失败")
extract(cabinet, runtime_dir)
files = list(runtime_dir.glob("*.dll_amd64"))
if not {"msvcp140.dll_amd64", "msvcp140_1.dll_amd64", "vcruntime140.dll_amd64"} <= {p.name for p in files}:
    raise RuntimeError("Microsoft C++ 运行库缺少必需 DLL")
for file in files:
    shutil.copyfile(file, destination / file.name.removesuffix("_amd64"))
shutil.copyfile(manifest_dir / "u4", destination / "LICENSE-vcredist.rtf")
print(f"已随应用包含 {len(files)} 个 Microsoft C++ 运行库 DLL，无需额外安装。")
