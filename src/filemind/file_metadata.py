"""Read archive member names and Windows version resources."""

import ctypes
import os
import subprocess
from pathlib import Path
from threading import Timer
from zipfile import BadZipFile, ZipFile

from filemind.documents import ARCHIVE_EXTENSIONS, DocumentError, Section
from filemind.windows import archive_console_encoding

MAX_LIST_BYTES = 128 * 1024
MAX_MEMBERS = 1_000


def _zip_names(path: Path) -> tuple[list[str], bool]:
    # Read ZIP's UTF-8 filename metadata in Python for normal-sized directories.
    # Larger archives use the bounded streaming listing below.
    if path.stat().st_size > 64 * 1024 * 1024:
        raise BadZipFile
    names = []
    used = 0
    with ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            used += len(member.filename.encode("utf-8")) + 1
            if len(names) >= MAX_MEMBERS or used > MAX_LIST_BYTES:
                return names, True
            names.append(member.filename)
    return names, False


def _archive(path: Path) -> list[Section]:
    if path.suffix.lower() == ".zip":
        try:
            names, truncated = _zip_names(path)
            return _archive_sections(names, truncated)
        except (BadZipFile, NotImplementedError):
            pass
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    executable = str(tar) if os.name == "nt" else "tar"
    with subprocess.Popen(
        [executable, "-tf", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as process:
        assert process.stdout is not None
        deadline = Timer(8, process.kill)
        deadline.start()
        try:
            raw = process.stdout.read(MAX_LIST_BYTES + 1)
            truncated = len(raw) > MAX_LIST_BYTES
            if truncated:
                process.kill()
            code = process.wait()
        finally:
            deadline.cancel()
            deadline.join()
    if code and not truncated:
        raise DocumentError("无法读取压缩包目录，可能已加密、格式损坏或读取超时。")
    names = raw.decode(archive_console_encoding(), errors="replace").splitlines()
    if truncated:
        names = names[:-1]
    names = [name for name in names if name and not name.endswith("/")]
    return _archive_sections(names, truncated)


def _archive_sections(names: list[str], truncated: bool) -> list[Section]:
    sections = [
        Section(
            f"压缩包条目 {start + 1}–{min(start + 32, len(names), MAX_MEMBERS)}",
            "\n".join(names[start : min(start + 32, MAX_MEMBERS)]),
        )
        for start in range(0, min(len(names), MAX_MEMBERS), 32)
    ]
    if truncated or len(names) > MAX_MEMBERS:
        sections.append(Section("压缩包目录", "目录超过预览上限，仅索引前 1000 项或 128 KiB。"))
    return sections


def _version_strings(path: Path) -> list[str]:
    if os.name != "nt":
        return []
    from ctypes import wintypes

    version = ctypes.WinDLL("version.dll", winmode=0x800)
    size_of = version.GetFileVersionInfoSizeW
    size_of.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    size_of.restype = wintypes.DWORD
    load = version.GetFileVersionInfoW
    load.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    load.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    query.restype = wintypes.BOOL

    size = size_of(str(path), None)
    if not 0 < size <= 1024 * 1024:
        return []
    buffer = ctypes.create_string_buffer(size)
    if not load(str(path), 0, size, buffer):
        return []

    def lookup(key: str) -> tuple[int, int]:
        pointer, count = ctypes.c_void_p(), wintypes.UINT()
        if query(buffer, key, ctypes.byref(pointer), ctypes.byref(count)) and pointer.value:
            return pointer.value, count.value
        return 0, 0

    pointer, count = lookup(r"\VarFileInfo\Translation")
    languages = []
    if pointer and 4 <= count <= 256:
        values = (ctypes.c_ushort * (count // 2)).from_address(pointer)
        languages = [f"{lang:04x}{code:04x}" for lang, code in zip(values[::2], values[1::2])]
    keys = {
        "FileDescription": "说明",
        "ProductName": "产品",
        "CompanyName": "公司",
        "FileVersion": "版本",
        "OriginalFilename": "原始文件名",
    }
    result = []
    for field, label in keys.items():
        for language in dict.fromkeys([*languages, "040904b0", "040904e4"]):
            pointer, count = lookup(f"\\StringFileInfo\\{language}\\{field}")
            if pointer and 1 < count <= 4096:
                value = ctypes.wstring_at(pointer, count - 1).strip().replace("\0", "")
                if value:
                    result.append(f"{label}  {value}")
                    break
    return result


def file_metadata(path: Path, extension: str) -> list[Section]:
    if extension in ARCHIVE_EXTENSIONS:
        return _archive(path)
    if extension == ".exe":
        return [Section("EXE 版本信息", "\n".join(_version_strings(path)))]
    return []
