"""Small, redacted failure reports. No invoice contents, captcha images or environment dump."""
import os
import platform
import re
import traceback


def redact(value):
    text = str(value)
    text = re.sub(r"data:image/[^\s]+", "<image>", text)
    text = re.sub(r"\b\d{16,}\b", "<invoice>", text)
    text = re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]+", r"\1?<redacted>", text)
    text = re.sub(r"(?i)((?:token|authorization|cookie|secret|password|api[_-]?key)\s*[:=]\s*)[^\s,;]+", r"\1<redacted>", text)
    for name in ("USERPROFILE", "HOME"):
        path = os.environ.get(name)
        if path:
            text = text.replace(path, "<user>")
    return text[:2500]


def resources():
    result = {"os": platform.system(), "os_version": platform.release(), "architecture": platform.machine(), "python": platform.python_version()}
    try:
        import psutil
        memory = psutil.virtual_memory()
        result.update(memory_total_mb=round(memory.total / 1024 ** 2), memory_available_mb=round(memory.available / 1024 ** 2),
                      process_rss_mb=round(psutil.Process().memory_info().rss / 1024 ** 2))
    except Exception:
        pass
    return result


def failure_report(exc, stage):
    # Exception-only and function names: never collect locals or rendered page contents.
    frames = traceback.extract_tb(exc.__traceback__)
    return {"stage": stage, "error_type": type(exc).__name__, "message": redact(str(exc).split("\nCall log:", 1)[0]),
            "trace": [f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}" for frame in frames[-6:]], "system": resources()}


def human_reason(exc, default):
    message = str(exc).lower()
    code = getattr(exc, "winerror", None)
    if isinstance(exc, MemoryError) or code in (8, 14, 1455) or any(word in message for word in ("bad_alloc", "not enough memory", "paging file", "out of memory")):
        return "系统可用内存或虚拟内存不足"
    if isinstance(exc, PermissionError) or code == 5 or "access is denied" in message:
        return "系统拒绝访问查验组件，请检查文件权限或安全软件拦截"
    if "dll load failed" in message or "specified module could not be found" in message:
        return "OCR 运行库加载失败，可复制诊断信息定位缺失组件"
    return default
