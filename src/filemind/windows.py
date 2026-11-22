"""Windows integration for the desktop app."""

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from filemind.config import AppPaths

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "FileMind"

CAPTION_BUTTON_WIDTH = 45
CAPTION_HEIGHT = 32


_WM_NCMOUSELEAVE = 0x02A2
_TME_LEAVE_NONCLIENT = 0x12


class _MouseTracking(ctypes.Structure):
    _fields_ = (
        ("size", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("hover_time", ctypes.c_uint),
    )


class IntegratedCaption:
    """Extend the client top while keeping the Win32 window styles and commands."""

    def __init__(self, scale: Callable[[], float], hover: Callable[[int | None], None]) -> None:
        from ctypes import wintypes

        import win32con
        import win32gui

        self.enabled = False
        self._scale = scale
        self._hover = hover
        self._hot = None
        self._pressed = None
        self._types = wintypes
        self._win = win32con
        self._gui = win32gui
        self._buttons = (win32con.HTMINBUTTON, win32con.HTMAXBUTTON, win32con.HTCLOSE)
        user = ctypes.WinDLL("user32.dll", winmode=0x800)
        self._dpi = user.GetDpiForWindow
        self._dpi.argtypes = (wintypes.HWND,)
        self._dpi.restype = wintypes.UINT
        self._metrics = user.GetSystemMetricsForDpi
        self._metrics.argtypes = (ctypes.c_int, wintypes.UINT)
        self._metrics.restype = ctypes.c_int
        self._maximized = user.IsZoomed
        self._maximized.argtypes = (wintypes.HWND,)
        self._maximized.restype = wintypes.BOOL
        self._default_proc = user.DefWindowProcW
        self._default_proc.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._default_proc.restype = ctypes.c_ssize_t
        self._track_mouse = user.TrackMouseEvent
        self._track_mouse.argtypes = (ctypes.POINTER(_MouseTracking),)
        self._track_mouse.restype = wintypes.BOOL

    def set_enabled(self, hwnd: int, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.enabled = enabled
        if not enabled:
            self._pressed = None
            self._set_hover(hwnd, None)
        win = self._win
        self._gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            win.SWP_NOMOVE
            | win.SWP_NOSIZE
            | win.SWP_NOZORDER
            | win.SWP_NOACTIVATE
            | win.SWP_FRAMECHANGED,
        )

    def handle_message(self, address: int) -> int | None:
        if not self.enabled:
            return None
        win = self._win
        event = self._types.MSG.from_address(address)
        if event.message == win.WM_NCCALCSIZE and event.wParam:
            area = self._types.RECT.from_address(event.lParam)
            top = area.top
            self._default_proc(event.hWnd, event.message, event.wParam, event.lParam)
            # Preserve the side/bottom frame and the invisible maximized resize inset.
            area.top = top + (self._top_border(event.hWnd) if self._maximized(event.hWnd) else 0)
            return 0
        if event.message == win.WM_NCHITTEST:
            return self._hit_test(event.hWnd, event.lParam)
        button = event.wParam
        if event.message == win.WM_NCMOUSEMOVE:
            hot = self._buttons.index(button) if button in self._buttons else None
            self._set_hover(event.hWnd, hot)
        elif event.message == _WM_NCMOUSELEAVE:
            self._pressed = None
            self._set_hover(event.hWnd, None)
        elif event.message in (win.WM_NCLBUTTONDOWN, win.WM_NCLBUTTONDBLCLK):
            if button in self._buttons:
                self._pressed = button
                return 0
        elif event.message == win.WM_NCLBUTTONUP:
            pressed, self._pressed = self._pressed, None
            if button in self._buttons:
                if pressed == button:
                    self._post_button_command(event.hWnd, button)
                return 0
        elif event.message in (win.WM_LBUTTONUP, win.WM_CANCELMODE):
            self._pressed = None
        return None

    def _post_button_command(self, hwnd: int, button: int) -> None:
        win = self._win
        maximize = win.SC_RESTORE if self._maximized(hwnd) else win.SC_MAXIMIZE
        command = {
            win.HTMINBUTTON: win.SC_MINIMIZE,
            win.HTMAXBUTTON: maximize,
            win.HTCLOSE: win.SC_CLOSE,
        }
        self._gui.PostMessage(hwnd, win.WM_SYSCOMMAND, command[button], 0)

    def _set_hover(self, hwnd: int, hot: int | None) -> None:
        if hot == self._hot:
            return
        self._hot = hot
        if hot is not None:
            tracking = _MouseTracking(ctypes.sizeof(_MouseTracking), _TME_LEAVE_NONCLIENT, hwnd, 0)
            self._track_mouse(ctypes.byref(tracking))
        self._hover(hot)

    def _top_border(self, hwnd: int) -> int:
        dpi = self._dpi(hwnd)
        return self._metrics(33, dpi) + self._metrics(92, dpi)  # Vertical frame and padded border.

    def _hit_test(self, hwnd: int, coordinates: int) -> int | None:
        left, top = self._gui.ClientToScreen(hwnd, (0, 0))
        width = self._gui.GetClientRect(hwnd)[2]
        scale = self._scale()
        x = ctypes.c_short(coordinates & 0xFFFF).value - left
        y = ctypes.c_short((coordinates >> 16) & 0xFFFF).value - top
        if not (0 <= x < width and 0 <= y < round(CAPTION_HEIGHT * scale)):
            return None

        win = self._win
        if not self._maximized(hwnd) and y < self._top_border(hwnd):
            return win.HTTOP
        from_right = width - 1 - x
        for index, hit in enumerate((win.HTCLOSE, win.HTMAXBUTTON, win.HTMINBUTTON), 1):
            if from_right < round(index * CAPTION_BUTTON_WIDTH * scale):
                return hit
        return win.HTCAPTION


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
