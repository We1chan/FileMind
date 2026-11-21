"""Windows integration for the desktop app."""

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from filemind.config import AppPaths

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "FileMind"

_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def set_title_bar_colors(hwnd: int, colors: tuple[str, str] | None) -> None:
    """Set native caption and text colors from #RRGGBB, or restore system colors."""
    if os.name != "nt" or sys.getwindowsversion().build < 22000:
        return

    from ctypes import wintypes

    set_attribute = ctypes.WinDLL("dwmapi.dll", winmode=0x800).DwmSetWindowAttribute
    set_attribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    )
    set_attribute.restype = ctypes.c_long

    def set_color(attribute: int, value: int) -> bool:
        color = wintypes.DWORD(value)
        return set_attribute(hwnd, attribute, ctypes.byref(color), ctypes.sizeof(color)) >= 0

    if colors is None:
        set_color(_DWMWA_CAPTION_COLOR, _DWMWA_COLOR_DEFAULT)
        set_color(_DWMWA_TEXT_COLOR, _DWMWA_COLOR_DEFAULT)
        return

    caption, text = (int.from_bytes(bytes.fromhex(color[1:]), "little") for color in colors)
    if set_color(_DWMWA_CAPTION_COLOR, caption) and not set_color(_DWMWA_TEXT_COLOR, text):
        set_color(_DWMWA_CAPTION_COLOR, _DWMWA_COLOR_DEFAULT)


def _known_folder(identifier: str) -> Path | None:
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32.dll", winmode=0x800)
    ole = ctypes.WinDLL("ole32.dll", winmode=0x800)
    function = shell.SHGetKnownFolderPath
    function.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    )
    function.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = None
    guid = ctypes.create_string_buffer(UUID(identifier).bytes_le)
    result = ctypes.c_void_p()
    try:
        if function(guid, 0, None, ctypes.byref(result)) == 0 and result.value:
            return Path(ctypes.wstring_at(result))
        return None
    finally:
        if result.value:
            free(result)


def common_locations() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    documents = _known_folder("fdd39ad0-238f-46af-adb4-6c85480369c7") or Path.home() / "Documents"
    downloads = _known_folder("374de290-123f-4565-9164-39c4925e467b") or Path.home() / "Downloads"
    return tuple(
        path for path in dict.fromkeys((documents, downloads, Path("D:/"))) if path.is_dir()
    )


def startup_command() -> str:
    interpreter = Path(sys.executable).with_name("pythonw.exe")
    if not interpreter.is_file():
        raise OSError("找不到 FileMind 的后台 Python 入口。")
    paths = AppPaths.resolve()
    return subprocess.list2cmdline(
        [
            str(interpreter),
            "-X",
            f"pycache_prefix={paths.bytecode}",
            "-m",
            "filemind",
            "--tray",
            "--data-dir",
            str(paths.root),
        ]
    )


def starts_with_windows() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE)
        return value == startup_command()
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("此设置只适用于 Windows。")
    import winreg

    if enabled:
        command = startup_command()
        if len(command) > 260:
            raise OSError("安装路径过长，无法登记到 Windows 登录启动项。")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN, access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN, access=winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _VALUE)
        except FileNotFoundError:
            pass


def archive_console_encoding() -> str:
    if os.name != "nt":
        return "utf-8"
    system = ctypes.WinDLL("kernel32.dll", winmode=0x800)
    system.GetACP.restype = ctypes.c_uint
    return f"cp{system.GetACP()}"
