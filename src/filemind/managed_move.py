"""Windows handle-based, non-replacing renames inside the managed workspace."""

import ctypes
import hashlib
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path

from filemind.documents import MAX_FILE_BYTES
from filemind.library import FileAccessError, _fingerprint, _inspect, _require_file

if sys.platform == "win32":
    import ntsecuritycon
    import pywintypes
    import win32con
    import win32file


class _RenameInfo(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.DWORD),
        ("directory", wintypes.HANDLE),
        ("name_bytes", wintypes.DWORD),
        ("name", wintypes.WCHAR * 1),
    ]


def _rename(handle, directory, name: str) -> None:
    # FileRenameInformation with ReplaceIfExists=False and a pinned destination directory.
    name = name.encode("utf-16-le")
    payload = ctypes.create_string_buffer(ctypes.sizeof(_RenameInfo) + len(name))
    info = _RenameInfo.from_buffer(payload)
    info.directory = int(directory)
    info.name_bytes = len(name)
    ctypes.memmove(ctypes.addressof(payload) + _RenameInfo.name.offset, name, len(name))
    ntdll = ctypes.WinDLL("ntdll")
    rename = ntdll.NtSetInformationFile
    rename.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_int,
    )
    rename.restype = wintypes.LONG
    io_status = (ctypes.c_size_t * 2)()  # IO_STATUS_BLOCK
    status = rename(int(handle), io_status, payload, len(payload), 10)
    if status:
        to_dos_error = ntdll.RtlNtStatusToDosError
        to_dos_error.argtypes = (wintypes.LONG,)
        to_dos_error.restype = wintypes.ULONG
        raise ctypes.WinError(to_dos_error(status))


def _open(path: Path, *, directory: bool):
    access = (
        ntsecuritycon.FILE_LIST_DIRECTORY | ntsecuritycon.FILE_TRAVERSE
        if directory
        else win32con.GENERIC_READ | win32con.DELETE
    )
    share = win32con.FILE_SHARE_READ
    if directory:
        share |= win32con.FILE_SHARE_WRITE
    flags = win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32file.FILE_FLAG_BACKUP_SEMANTICS
    handle = win32file.CreateFile(
        str(path), access, share, None, win32con.OPEN_EXISTING, flags, None
    )
    try:
        attributes = win32file.GetFileInformationByHandle(handle)[0]
        if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise FileAccessError("路径包含符号链接或重解析点。")
        if bool(attributes & win32con.FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise FileAccessError("文件或目录类型已变更。")
    except BaseException:
        handle.Close()
        raise
    return handle


def _check_digest(handle, expected_size: int, expected_hash: str) -> None:
    if expected_size > MAX_FILE_BYTES:
        raise FileAccessError("文件超出受管副本大小限制。")
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        status, data = win32file.ReadFile(handle, min(remaining, 1024 * 1024))
        if status or not data:
            raise FileAccessError("文件在读取时发生变化。")
        remaining -= len(data)
        digest.update(data)
    if digest.hexdigest() != expected_hash:
        raise FileAccessError("文件内容已变更，请刷新索引后重新预览。")


@contextmanager
def locked_rename(
    source: Path,
    destination: Path,
    workspace: Path,
    fingerprint: tuple[int, int, int, str],
    sha256: str,
) -> Iterator[Callable[[], None]]:
    if sys.platform != "win32":
        raise FileAccessError("移动和撤销需要 Windows。")
    try:
        with ExitStack() as stack:
            pinned = {}
            for path in (source, destination):
                for parent in reversed(path.parents):
                    if parent in pinned:
                        continue
                    if (
                        path == destination
                        and parent.is_relative_to(workspace)
                        and parent != workspace
                    ):
                        parent.mkdir(exist_ok=True)
                    handle = _open(parent, directory=True)
                    stack.callback(handle.Close)
                    pinned[parent] = handle
            handle = _open(source, directory=False)
            stack.callback(handle.Close)
            info = _inspect(source)
            _require_file(info)
            if _fingerprint(info) != fingerprint:
                raise FileAccessError("文件状态已变更，请刷新索引后重新预览。")
            _check_digest(handle, info.st_size, sha256)
            yield lambda: _rename(handle, pinned[destination.parent], destination.name)
    except pywintypes.error as error:
        raise ctypes.WinError(error.winerror) from error
