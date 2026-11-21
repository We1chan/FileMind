"""桌面工作区的配色、字体层级和应用图标。"""

from math import cos, pi, sin

from PySide6.QtCore import QLineF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter

WINDOW_STYLE = """
QWidget#Shell { background: #f7f5f1; color: #1c3026; }
QFrame#Sidebar { background: #153728; }
QLabel#Brand { color: #fbfbf2; font-family: Georgia; font-size: 26px; font-weight: 600; }
QListWidget#Navigation {
    background: #153728;
    color: #f5f7ef;
    border: none;
}
QListWidget#Navigation::item {
    padding-left: 18px;
    border: 1px solid transparent;
    border-radius: 8px;
}
QListWidget#Navigation::item:hover { background: #294f3c; }
QListWidget#Navigation::item:selected {
    background: #3d694b;
    color: #ffffff;
    border-color: #437051;
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
QListWidget#Navigation { font-family: SimSun; font-size: 18px; }
QWidget#SearchWorkspace, QFrame#SearchHeader, QFrame#SearchDetail {
    background: #f7f5f1;
}
QFrame#SearchHeader QLabel#PageTitle {
    font-family: SimSun; font-weight: 700; font-size: 39px; color: #123627;
}
QFrame#SearchHeader { border-bottom: 1px solid #d2d3ca; }
QFrame#SearchField {
    background: #faf9f6; border: 1px solid #214737; border-radius: 7px; min-height: 44px;
}
QLineEdit#SearchQuery, QLineEdit#SearchQuery:focus {
    font-family: SimSun; font-size: 24px; color: #151815;
    background: transparent; border: none; padding: 0; min-height: 36px;
}
QToolButton#ClearSearch {
    background: transparent; border: 1px solid transparent; border-radius: 5px;
}
QToolButton#ClearSearch:hover, QToolButton#ClearSearch:focus { border-color: #a4b3a4; }
QPushButton#FindContent {
    font-family: SimSun; font-size: 21px; font-weight: 700;
    min-width: 94px; min-height: 45px; padding: 0;
    background: #153728; border: 1px solid #153728; border-radius: 7px;
}
QPushButton#FindContent:hover:enabled { background: #28523b; }
QPushButton#FindContent:disabled { background: #dce0d6; }
QPushButton#SearchModeMenu {
    padding: 0; background: transparent; border: 1px solid transparent;
    font-family: SimSun; font-size: 18px; color: #151815;
}
QPushButton#SearchModeMenu::menu-indicator { image: none; width: 0; }
QPushButton#SearchModeMenu:hover, QPushButton#SearchModeMenu:focus { border-color: #9ca899; }
QPushButton#ModeChoice {
    min-width: 79px; min-height: 29px; padding: 0; border-radius: 8px;
    background: transparent; border-color: #8d9a88;
    font-family: SimSun; font-size: 16px; color: #151815;
}
QPushButton#ModeChoice:checked {
    background: #2d5b3d; border-color: #2d5b3d; color: #ffffff;
}
QPushButton#ModeChoice:focus { border-color: #6c9f83; }
QSplitter#SearchSplit::handle { background: #ced2c9; }
QWidget#ResultPanel { background: #f8f7f3; }
QFrame#ResultHeader, QWidget#ReaderToolbar { border-bottom: 1px solid #dcddd6; }
QLabel#ResultCount, QComboBox#ResultSort {
    font-family: SimSun; color: #171d17; font-size: 16px;
}
QComboBox#ResultSort {
    background: transparent; border: 1px solid transparent; padding: 4px 21px 4px 4px;
}
QComboBox#ResultSort:hover, QComboBox#ResultSort:focus { border-color: #9ca899; }
QComboBox#ResultSort::down-arrow, QComboBox#ReaderPage::down-arrow { image: none; width: 0; }
QComboBox#ReaderPage {
    font-family: SimSun; font-size: 15px; border: 1px solid #8d9a88;
    background: #f8f7f2; padding: 4px 20px 4px 12px; min-width: 52px;
}
QPushButton#ReaderTool {
    padding: 0; min-width: 33px; min-height: 31px; border: 1px solid transparent;
    background: transparent; border-radius: 5px;
}
QPushButton#ReaderTool:hover:enabled, QPushButton#ReaderTool:focus {
    background: #e7ece2; border-color: #a4b3a4;
}
QScrollArea#PaperView { background: #f7f5f1; border: none; }
QWidget#PaperSurface { background: #f7f5f1; }
QLabel#RenderedPaper { background: #ffffff; }
QFrame#AskFooter { border-top: 1px solid #d4d7ce; }
QPushButton#AskFile {
    min-width: 230px; min-height: 36px; padding: 0;
    font-family: SimSun; font-size: 18px; font-weight: 700;
    color: #194331; background: #f7f7ee; border: 1px solid #30684d;
}
QPushButton#AskFile:hover:enabled { background: #e6edde; }
QPushButton#AskFile:disabled { color: #647369; border-color: #acb9ac; }
QPlainTextEdit#SourcePreview {
    font-family: "SimSun"; font-size: 17px; padding: 14px;
    selection-background-color: #f2d998; selection-color: #23392c;
}
QListWidget#SearchResults { background: #f8f7f3; border: none; padding: 0; }
QLabel#SectionTitle { font-family: "SimSun"; font-size: 21px; }
"""


def application_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    for points, color in (
        (((7, 54), (18, 17), (54, 5)), "#dfead4"),
        (((7, 54), (42, 40), (54, 5)), "#a9c5a8"),
        (((7, 54), (42, 40), (48, 54)), "#f3f1dd"),
    ):
        fold = QPainterPath()
        fold.moveTo(*points[0])
        for point in points[1:]:
            fold.lineTo(*point)
        fold.closeSubpath()
        painter.fillPath(fold, QColor(color))
    painter.end()
    return QIcon(pixmap)


def line_icon(name: str, color: str = "#151b17") -> QIcon:
    pixmap = QPixmap(72, 72)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.scale(3, 3)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(
        QColor(color),
        1.55,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    path = QPainterPath()

    def stroke(*points):
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)

    if name == "search":
        painter.drawEllipse(QRectF(2, 2, 15.5, 15.5))
        stroke((15, 15), (21.5, 21.5))
    elif name == "close":
        stroke((6, 6), (18, 18))
        stroke((18, 6), (6, 18))
    elif name == "folder":
        stroke(
            (2, 19),
            (2, 5),
            (3, 4),
            (9, 4),
            (12, 7),
            (21, 7),
            (22, 8),
            (22, 19),
            (21, 20),
            (3, 20),
            (2, 19),
        )
    elif name == "layers":
        stroke((2, 7.5), (12, 2.3), (22, 7.5), (12, 12.7), (2, 7.5))
        stroke((2, 12), (12, 17.2), (22, 12))
        stroke((2, 16.5), (12, 21.7), (22, 16.5))
    elif name == "settings":
        points = []
        for index in range(32):
            angle = index * pi / 16
            radius = 10.4 if index % 4 in (1, 2) else 8.3
            points.append((12 + radius * cos(angle), 12 + radius * sin(angle)))
        stroke(*points, points[0])
        painter.drawEllipse(QRectF(8.5, 8.5, 7, 7))
    elif name == "book":
        stroke(
            (12, 6),
            (9, 4),
            (4, 4),
            (2, 5),
            (2, 20),
            (5, 19),
            (9, 19),
            (12, 21),
            (15, 19),
            (19, 19),
            (22, 20),
            (22, 5),
            (20, 4),
            (15, 4),
            (12, 6),
            (12, 21),
        )
    elif name == "previous":
        stroke((14.5, 5), (8.5, 12), (14.5, 19))
    elif name == "next":
        stroke((9.5, 5), (15.5, 12), (9.5, 19))
    elif name == "down":
        stroke((4, 9), (12, 16), (20, 9))
    elif name in ("zoom_out", "zoom_in"):
        painter.drawEllipse(QRectF(2, 2, 15.5, 15.5))
        stroke((15.2, 15.2), (21.5, 21.5))
        painter.drawLine(QLineF(6, 9.75, 13.5, 9.75))
        if name == "zoom_in":
            painter.drawLine(QLineF(9.75, 6, 9.75, 13.5))
    elif name == "fullscreen":
        stroke((3, 8), (3, 3), (8, 3))
        stroke((16, 3), (21, 3), (21, 8))
        stroke((21, 16), (21, 21), (16, 21))
        stroke((8, 21), (3, 21), (3, 16))
    elif name == "bubble":
        path.moveTo(4, 17)
        path.cubicTo(-1, 11, 4, 3, 12, 3)
        path.cubicTo(26, 3, 26, 20, 12, 20)
        path.lineTo(8, 19.5)
        path.lineTo(3, 22)
        path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class ChevronComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.chevron = line_icon("down")

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(
            self.rect().adjusted(6, 0, -24, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.currentText(),
        )
        self.chevron.paint(painter, QRect(self.width() - 20, (self.height() - 16) // 2, 16, 16))
        painter.end()
