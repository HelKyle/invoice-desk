"""Check native architecture and app-local C++ dependencies without executing Windows code."""
import struct
import sys
from pathlib import Path

root = Path(sys.argv[1])
local = {p.name.lower() for p in root.rglob("*") if p.is_file()}
checked = 0
for file in root.rglob("*"):
    if file.suffix.lower() not in (".dll", ".pyd", ".exe") or "browsers" in file.parts:
        continue
    data = file.read_bytes()
    if data[:2] != b"MZ":
        raise RuntimeError(f"不是有效的 Windows 二进制：{file}")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    machine, section_count = struct.unpack_from("<HH", data, pe + 4)
    if machine != 0x8664:
        raise RuntimeError(f"Windows x64 包混入其他架构：{file}")
    optional = pe + 24
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    table = optional + optional_size
    sections = []
    for i in range(section_count):
        start = table + i * 40
        virtual_size, virtual_address, raw_size, raw_address = struct.unpack_from("<IIII", data, start + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_address))
    def offset(rva):
        for address, size, raw in sections:
            if address <= rva < address + size:
                return raw + rva - address
        raise RuntimeError(f"无法读取 PE 数据：{file}")
    import_rva = struct.unpack_from("<I", data, optional + 112 + 8)[0]
    if import_rva:
        descriptor = offset(import_rva)
        while any(data[descriptor:descriptor + 20]):
            name_rva = struct.unpack_from("<I", data, descriptor + 12)[0]
            location = offset(name_rva)
            name = data[location:data.index(b"\0", location)].decode("ascii").lower()
            if name.startswith(("msvcp", "vcruntime", "concrt", "python3")) and name not in local:
                raise RuntimeError(f"{file.name} 缺少本地依赖 {name}")
            descriptor += 20
    checked += 1
print(f"Windows 原生依赖检查通过：{checked} 个 x64 文件，C++ 与 Python 运行库已包含。")
