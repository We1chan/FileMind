"""桌面工作区的配色、字体层级和应用图标。"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

WINDOW_STYLE = """
QWidget#Shell { background: #f3f6f9; color: #132638; }
QFrame#Sidebar { background: #102438; }
QLabel#Brand { color: #ffffff; font-size: 20px; font-weight: 700; }
QLabel#Version { color: #a9bacb; font-size: 12px; }
QListWidget#Navigation {
    background: #102438;
    color: #cfdae5;
    border: none;
}
QListWidget#Navigation::item {
    padding-left: 13px;
    border: 1px solid transparent;
    border-radius: 8px;
}
QListWidget#Navigation::item:hover { background: #1b344b; }
QListWidget#Navigation::item:selected {
    background: #203c53;
    color: #ffffff;
    border-color: #365773;
    font-weight: 600;
}
QListWidget#Navigation::item:selected:focus { border-color: #74c8b9; }
QLabel#PageTitle { color: #102438; font-size: 28px; font-weight: 700; }
QLabel#Hint { color: #4e6377; }
QFrame#Card {
    background: #ffffff;
    border: 1px solid #dce4ed;
    border-radius: 12px;
}
QLabel#SectionTitle { color: #132638; font-size: 16px; font-weight: 700; }
QLabel#Value { color: #182f43; }
QPushButton[action="true"] {
    color: #183c4d;
    background: #ffffff;
    border: 1px solid #c9d7e2;
    border-radius: 7px;
    padding: 7px 11px;
}
QPushButton[action="true"]:hover { border-color: #318578; }
QPushButton[primary="true"]:enabled {
    background: #12665d;
    color: #ffffff;
    border: 1px solid #12665d;
    border-radius: 7px;
    padding: 7px 13px;
}
QPushButton[primary="true"]:enabled:hover { background: #0b544d; }
QPushButton[action="true"]:disabled {
    color: #657987;
    background: #eaf0f4;
    border-color: #dce4ed;
}
QLineEdit, QComboBox, QPlainTextEdit, QListWidget[results="true"] {
    background: #ffffff;
    color: #132638;
    border: 1px solid #c9d7e2;
    border-radius: 7px;
    padding: 6px 8px;
    selection-background-color: #c7e9e1;
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
QListWidget[results="true"]::item:selected { background: #c7e9e1; color: #123c36; }
"""


def application_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#13685e"))
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
