"""FileMind's notification-area icon and native Qt menu."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget, QWidgetAction

from filemind.ui.theme import application_icon

_MENU_STYLE = """
QMenu#FileMindTray {
    background: #fbfaf5;
    border: 1px solid #c8cec2;
    border-radius: 10px;
    padding: 5px;
    color: #1c3026;
}
QMenu#FileMindTray::item {
    padding: 8px 15px 8px 21px;
    border: 1px solid transparent;
    border-radius: 6px;
    min-height: 18px;
}
QMenu#FileMindTray::icon { left: 7px; }
QMenu#FileMindTray::item:selected {
    background: #e3ecde;
    border-color: #9cb698;
    color: #133b2c;
}
QMenu#FileMindTray::item:disabled { color: #68766b; }
QMenu#FileMindTray::separator {
    height: 1px;
    background: #d6dbd0;
    margin: 4px 9px;
}
QWidget#TrayHeading { background: #ecf2e8; border-radius: 6px; }
QLabel#TrayName { color: #133b2c; }
QLabel#TrayStatus { color: #476b4d; }
QLabel#TrayStatus[state="paused"], QLabel#TrayStatus[state="error"] { color: #815222; }
"""


def tray_icon() -> QIcon:
    icon = QIcon()
    source = application_icon().pixmap(64)
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(size / 32, size / 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#133b2c"))
        painter.drawRoundedRect(QRectF(0.5, 0.5, 31, 31), 7, 7)
        painter.drawPixmap(QRectF(3, 3, 29, 29), source, QRectF(source.rect()))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _action_icon(glyph: str, color: QColor) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont("Segoe MDL2 Assets")
        font.setPixelSize(size - 2)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class TrayMenu(QMenu):
    def __init__(self, parent: QWidget, icon: QIcon) -> None:
        super().__init__(parent)
        self.setObjectName("FileMindTray")
        self.setMinimumWidth(224)
        self.setAccessibleName("FileMind 托盘菜单")
        self._state = ""
        self._high_contrast: bool | None = None
        self._toggle_icons: dict[bool, QIcon] = {}

        heading = QWidget()
        heading.setObjectName("TrayHeading")
        heading.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header = QHBoxLayout(heading)
        header.setContentsMargins(9, 8, 10, 9)
        header.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(icon.pixmap(30))
        header.addWidget(logo)
        words = QVBoxLayout()
        words.setSpacing(2)
        name = QLabel("FileMind")
        name.setObjectName("TrayName")
        font = name.font()
        font.setFamily("Georgia")
        font.setBold(True)
        name.setFont(font)
        self.status = QLabel()
        self.status.setObjectName("TrayStatus")
        words.addWidget(name)
        words.addWidget(self.status)
        header.addLayout(words, 1)

        banner = QWidgetAction(self)
        banner.setDefaultWidget(heading)
        banner.setEnabled(False)
        self.addAction(banner)
        # Skip the heading during keyboard navigation while retaining readable colors.
        heading.setEnabled(True)
        self.open_window = self.addAction("打开 FileMind")
        self.toggle_pause = self.addAction("暂停后台索引")
        self.refresh_index = self.addAction("立即刷新索引")
        self.addSeparator()
        self.exit_app = self.addAction("退出 FileMind")
        self.setDefaultAction(self.open_window)
        self.set_high_contrast(False)

    def set_high_contrast(self, enabled: bool) -> None:
        if enabled == self._high_contrast:
            return
        self._high_contrast = enabled
        self.setStyleSheet("" if enabled else _MENU_STYLE)
        ink = self.palette().color(QPalette.ColorRole.WindowText) if enabled else QColor("#284f3d")
        self.open_window.setIcon(_action_icon("\ue8a7", ink))
        self._toggle_icons = {
            False: _action_icon("\ue769", ink),
            True: _action_icon("\ue768", ink),
        }
        self.toggle_pause.setIcon(self._toggle_icons[self._state == "paused"])
        self.refresh_index.setIcon(_action_icon("\ue72c", ink))
        self.exit_app.setIcon(_action_icon("\ue8bb", ink if enabled else QColor("#814d40")))

    def update_status(self, *, paused: bool, error: bool, busy: bool) -> str:
        if paused:
            state, text = "paused", "后台索引已暂停"
        elif error:
            state, text = "error", "后台索引暂时异常"
        else:
            state, text = "active", "后台索引已启用"
        if state != self._state:
            self._state = state
            self.status.setText(text)
            self.status.setProperty("state", state)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            self.toggle_pause.setText("恢复后台索引" if paused else "暂停后台索引")
            self.toggle_pause.setIcon(self._toggle_icons[paused])
        self.refresh_index.setEnabled(not (paused and busy))
        return text
