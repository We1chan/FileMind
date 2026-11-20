"""桌面工作区的配色、字体层级和应用图标。"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

WINDOW_STYLE = """
QWidget#Shell { background: #faf8f3; color: #1c3026; }
QFrame#Sidebar { background: #133b2c; }
QLabel#Brand { color: #ffffff; font-family: Georgia; font-size: 24px; font-weight: 700; }
QLabel#Version { color: #a9bacb; font-size: 12px; }
QListWidget#Navigation {
    background: #133b2c;
    color: #ecf1e6;
    border: none;
}
QListWidget#Navigation::item {
    padding-left: 13px;
    border: 1px solid transparent;
    border-radius: 8px;
}
QListWidget#Navigation::item:hover { background: #294f3c; }
QListWidget#Navigation::item:selected {
    background: #426b4b;
    color: #ffffff;
    border-color: #4b7554;
    font-weight: 600;
}
QListWidget#Navigation::item:selected:focus { border-color: #74c8b9; }
QLabel#PageTitle { color: #133b2c; font-family: "SimSun"; font-size: 34px; font-weight: 700; }
QLabel#Hint { color: #566459; }
QFrame#Card {
    background: #ffffff;
    border: 1px solid #d6dbd0;
    border-radius: 12px;
}
QLabel#SectionTitle { color: #1c3026; font-size: 16px; font-weight: 700; }
QLabel#Value { color: #182f43; }
QPushButton[action="true"] {
    color: #183c4d;
    background: #ffffff;
    border: 1px solid #c8cec2;
    border-radius: 7px;
    padding: 7px 11px;
}
QPushButton[action="true"]:hover { border-color: #318578; }
QPushButton[primary="true"]:enabled {
    background: #193f30;
    color: #ffffff;
    border: 1px solid #193f30;
    border-radius: 7px;
    padding: 7px 13px;
}
QPushButton[primary="true"]:enabled:hover { background: #0b544d; }
QPushButton[action="true"]:disabled {
    color: #657987;
    background: #eaf0f4;
    border-color: #d6dbd0;
}
QLineEdit, QComboBox, QPlainTextEdit, QListWidget[results="true"] {
    background: #ffffff;
    color: #1c3026;
    border: 1px solid #c8cec2;
    border-radius: 7px;
    padding: 6px 8px;
    selection-background-color: #dce9d5;
    selection-color: #123c36;
}
QLineEdit:focus, QComboBox:focus, QListWidget[results="true"]:focus { border-color: #318578; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border: none;
}
QListWidget[results="true"]::item { padding: 7px 6px; border-bottom: 1px solid #eef2f6; }
QListWidget[results="true"]::item:hover { background: #eaf4f2; }
QListWidget[results="true"]::item:selected { background: #dce9d5; color: #123c36; }
QPushButton#ModeChoice:checked { background: #386348; color: white; }
QLineEdit#SearchQuery { font-size: 18px; padding: 8px 12px; border-color: #607b69; }
QPlainTextEdit#SourcePreview {
    font-family: "SimSun"; font-size: 17px; padding: 14px;
    selection-background-color: #f2d998; selection-color: #23392c;
}
QListWidget#SearchResults { background: #faf9f5; border: none; }
QListWidget#SearchResults::item { padding: 14px 9px; border-bottom: 1px solid #d6dccf; }
QLabel#SectionTitle { font-family: "SimSun"; font-size: 21px; }
"""


def application_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#376449"))
    painter.drawRoundedRect(QRectF(1, 1, 62, 62), 14, 14)

    document = QPainterPath()
    document.moveTo(20, 15)
    document.lineTo(35, 15)
    document.lineTo(45, 25)
    document.lineTo(45, 49)
    document.lineTo(20, 49)
    document.closeSubpath()
    document.moveTo(35, 15)
    document.lineTo(35, 25)
    document.lineTo(45, 25)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor("#f1fffc"), 3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawPath(document)
    painter.drawLine(26, 33, 39, 33)
    painter.drawLine(26, 40, 36, 40)
    painter.end()
    return QIcon(pixmap)
