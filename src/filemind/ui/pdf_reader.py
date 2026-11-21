"""Paged native PDF reader for verified in-memory document snapshots."""

import sys

from PySide6.QtCore import QEvent, QProcess, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from filemind.ui.pages import button, label
from filemind.ui.theme import ChevronComboBox, line_icon


class PdfReader(QWidget):
    render_failed = Signal()
    page_count_found = Signal(int)

    def __init__(self):
        super().__init__()
        self.data = b""
        self.term = ""
        self.process = None
        self.scale = 1.0
        self.image = QPixmap()
        self.view = QScrollArea()
        self.view.setObjectName("PaperView")
        self.view.setViewportMargins(18, 9, 18, 13)
        self.view.viewport().setObjectName("PaperSurface")
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setAccessibleName("PDF 原始页面")
        self.paper = QLabel()
        self.paper.setObjectName("RenderedPaper")
        shadow = QGraphicsDropShadowEffect(self.paper)
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(30, 35, 26, 45))
        self.paper.setGraphicsEffect(shadow)
        self.paper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setWidget(self.paper)
        self.view.viewport().installEventFilter(self)
        self.setMinimumWidth(312)
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        self.layout_box.setSpacing(0)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("ReaderToolbar")
        self.toolbar.setMinimumHeight(48)
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(20, 4, 15, 4)
        row.setSpacing(9)
        self.book = QLabel()
        self.book.setPixmap(line_icon("book").pixmap(QSize(24, 24), self.devicePixelRatioF()))
        self.heading = label("原文预览", "SectionTitle")
        row.addWidget(self.book)
        row.addWidget(self.heading)
        row.addStretch()
        self.previous = self.control(
            row,
            "previous",
            "上一页",
            lambda: self.page.setCurrentIndex(self.page.currentIndex() - 1),
        )
        self.page = ChevronComboBox()
        self.page.setFixedWidth(96)
        self.page.setObjectName("ReaderPage")
        self.page.setMinimumContentsLength(3)
        self.page.setAccessibleName("物理页码")
        row.addWidget(self.page)
        self.next = self.control(
            row, "next", "下一页", lambda: self.page.setCurrentIndex(self.page.currentIndex() + 1)
        )
        row.addStretch()
        self.control(row, "zoom_out", "缩小", lambda: self.zoom(0.8))
        self.control(row, "zoom_in", "放大", lambda: self.zoom(1.25))
        self.control(row, "fullscreen", "全屏阅读", self.fullscreen)
        self.layout_box.addWidget(self.toolbar)
        self.layout_box.addWidget(self.view, 1)
        self.page.currentIndexChanged.connect(self.jump)
        self.dialog = None

    def control(self, layout, symbol, name, callback):
        action = button("", "ReaderTool")
        action.setIcon(line_icon(symbol))
        action.setIconSize(QSize(23, 23))
        action.setAccessibleName(name)
        action.setToolTip(name)
        action.clicked.connect(callback)
        layout.addWidget(action)
        return action

    def reset(self):
        if self.dialog:
            self.dialog.close()
        self.stop_render()
        self.data = b""
        self.image = QPixmap()
        self.paper.clear()
        self.page.clear()
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.hide()

    def stop_render(self):
        if self.process is not None:
            process = self.process
            self.process = None
            process.kill()

    def open_page(self, data, number, term):
        self.reset()
        self.data = data
        self.term = " ".join(term.splitlines())
        self.scale = 1.0
        self.show()
        self.render(number)
        return True

    def jump(self, index):
        if index >= 0 and self.data:
            self.render(index + 1)

    def render(self, number):
        self.stop_render()
        self.image = QPixmap()
        self.paper.setText("正在读取原页…")
        self.paper.adjustSize()
        process = QProcess(self)
        self.process = process
        timer = QTimer(process)
        timer.setSingleShot(True)
        timer.timeout.connect(process.kill)
        timer.start(15000)

        def finished():
            timer.stop()
            process.deleteLater()
            if process is not self.process:
                return
            self.process = None
            output = bytes(process.readAllStandardOutput())
            count = int.from_bytes(output[:4], "big")
            if not self.image.loadFromData(output[4:], "PNG"):
                self.paper.setText("无法渲染此页，请重新选择文件或页码。")
                self.paper.adjustSize()
                self.render_failed.emit()
                return
            self.page.blockSignals(True)
            if self.page.count() != count:
                self.page.clear()
                self.page.addItems([f"第 {i + 1} 页" for i in range(count)])
                self.page_count_found.emit(count)
            self.page.setCurrentIndex(number - 1)
            self.page.blockSignals(False)
            self.previous.setEnabled(number > 1)
            self.next.setEnabled(number < count)
            self.fit()

        process.finished.connect(finished)
        process.errorOccurred.connect(
            lambda err: finished() if err == QProcess.ProcessError.FailedToStart else None
        )
        process.start(sys.executable, ["-m", "filemind.pdf_render", str(number)])
        process.write(self.term.encode("utf-8") + b"\n" + self.data)
        process.closeWriteChannel()

    def fit(self):
        if self.image.isNull():
            return
        area = self.view.maximumViewportSize()
        size = self.image.size().scaled(area, Qt.AspectRatioMode.KeepAspectRatio) * self.scale
        pixmap = self.image.scaled(
            size * self.devicePixelRatioF(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self.paper.setPixmap(pixmap)
        self.paper.resize(size)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            show_heading = event.size().width() >= 410
            self.heading.setVisible(show_heading)
            self.book.setVisible(show_heading)
            self.fit()
        return super().eventFilter(obj, event)

    def zoom(self, factor):
        self.scale = max(0.3, min(4, self.scale * factor))
        self.fit()

    def fullscreen(self):
        if self.dialog:
            self.dialog.close()
            return
        dialog = QDialog(self.window())
        dialog.setWindowTitle("FileMind · 原文预览")
        box = QVBoxLayout(dialog)
        box.addWidget(self.toolbar)
        box.addWidget(self.view, 1)
        self.dialog = dialog

        def restore():
            self.layout_box.addWidget(self.toolbar)
            self.layout_box.addWidget(self.view, 1)
            self.dialog = None
            dialog.deleteLater()

        dialog.finished.connect(restore)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.showFullScreen()
