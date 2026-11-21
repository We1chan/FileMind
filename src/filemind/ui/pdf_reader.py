"""Paged native PDF reader for verified in-memory document snapshots."""

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from filemind.documents import MAX_PAGES
from filemind.ui.pages import button, label

MAX_FRAME_BYTES = 16 * 1024 * 1024


class PdfPage(QLabel):
    def __init__(self):
        super().__init__()
        self.image = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def message(self, text):
        self.image = QPixmap()
        self.setText(text)
        self.adjustSize()
        self.update()

    def paintEvent(self, event):
        if self.image.isNull():
            super().paintEvent(event)
            return
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), self.image)


class PdfReader(QWidget):
    render_failed = Signal()

    def __init__(self):
        super().__init__()
        self.data = b""
        self.term = ""
        self.process: QProcess | None = None
        self._wanted = 0
        self._rendering = 0
        self._output = bytearray()
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(15000)
        self._deadline.timeout.connect(self._render_error)
        self._resize_delay = QTimer(self)
        self._resize_delay.setSingleShot(True)
        self._resize_delay.setInterval(16)
        self._resize_delay.timeout.connect(self.fit)
        self.scale = 1.0
        self.image = QPixmap()
        self.view = QScrollArea()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setAccessibleName("PDF 原始页面")
        self.paper = PdfPage()
        self.view.setWidget(self.paper)
        self.view.viewport().installEventFilter(self)
        self.setMinimumWidth(246)
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QWidget()
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 4, 0, 4)
        self.heading = label("原文预览", "SectionTitle")
        row.addWidget(self.heading)
        row.addStretch()
        self.previous = self.control(
            row, "‹", "上一页", lambda: self.page.setCurrentIndex(self.page.currentIndex() - 1)
        )
        self.page = QComboBox()
        self.page.setMinimumContentsLength(3)
        self.page.setAccessibleName("物理页码")
        row.addWidget(self.page)
        self.next = self.control(
            row, "›", "下一页", lambda: self.page.setCurrentIndex(self.page.currentIndex() + 1)
        )
        row.addStretch()
        self.control(row, "−", "缩小", lambda: self.zoom(0.8))
        self.control(row, "＋", "放大", lambda: self.zoom(1.25))
        self.control(row, "⛶", "全屏阅读", self.fullscreen)
        self.layout_box.addWidget(self.toolbar)
        self.layout_box.addWidget(self.view, 1)
        self.page.currentIndexChanged.connect(self.jump)
        self.dialog = None

    def control(self, layout, symbol, name, callback):
        action = button(symbol, "ReaderTool")
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
        self.paper.message("")
        self.page.clear()
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.hide()

    @property
    def rendering(self):
        return self._rendering != 0

    def stop_render(self):
        self._deadline.stop()
        self._resize_delay.stop()
        self._rendering = 0
        self._output.clear()
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
        self._wanted = number
        self.image = QPixmap()
        self.paper.message("正在读取原页…")
        self.previous.setEnabled(number > 1 and self.page.count() > 0)
        self.next.setEnabled(number < self.page.count())
        if self.rendering:
            return
        if self.process is None:
            self._start_renderer()
        if self.process is not None:
            self._request_page()

    def _start_renderer(self):
        process = QProcess(self)
        self.process = process
        process.readyReadStandardOutput.connect(lambda: self._read_frame(process))

        def finished():
            if process is self.process:
                self._render_error()

        process.finished.connect(finished)
        process.finished.connect(process.deleteLater)

        def start_failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                finished()
                process.deleteLater()

        process.errorOccurred.connect(start_failed)
        executable = Path(sys.executable)
        if executable.stem.lower() == "pythonw":
            executable = executable.with_name("python.exe")
        process.start(str(executable), ["-m", "filemind.pdf_render"])
        term = self.term.encode("utf-8")
        process.write(len(term).to_bytes(4, "big") + len(self.data).to_bytes(4, "big") + term)
        process.write(self.data)

    def _request_page(self):
        self._rendering = self._wanted
        self._deadline.start()
        self.process.write(self._rendering.to_bytes(4, "big"))

    def _read_frame(self, process):
        if process is not self.process or not self.rendering:
            return
        self._output.extend(bytes(process.readAllStandardOutput()))
        if len(self._output) < 8:
            return
        count = int.from_bytes(self._output[:4], "big")
        size = int.from_bytes(self._output[4:8], "big")
        if not self._rendering <= count <= MAX_PAGES or not 0 < size <= MAX_FRAME_BYTES:
            self._render_error()
            return
        if len(self._output) < size + 8:
            return
        if len(self._output) != size + 8:
            self._render_error()
            return

        number = self._rendering
        self._deadline.stop()
        self._rendering = 0
        if number != self._wanted:
            self._output.clear()
            self._request_page()
            return
        loaded = self.image.loadFromData(bytes(self._output[8:]), "PNG")
        self._output.clear()
        if not loaded:
            self._render_error()
            return
        self.page.blockSignals(True)
        if self.page.count() != count:
            self.page.clear()
            self.page.addItems([f"第 {i + 1} 页" for i in range(count)])
        self.page.setCurrentIndex(number - 1)
        self.page.blockSignals(False)
        self.previous.setEnabled(number > 1)
        self.next.setEnabled(number < count)
        self.fit()

    def _render_error(self):
        self.stop_render()
        self.paper.message("无法渲染此页，请重新选择文件或页码。")
        self.render_failed.emit()

    def fit(self):
        if self.image.isNull():
            return
        area = self.view.maximumViewportSize()
        size = self.image.size().scaled(area, Qt.AspectRatioMode.KeepAspectRatio) * self.scale
        self.paper.image = self.image
        self.paper.resize(size)
        self.paper.update()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.heading.setVisible(event.size().width() >= 350)
            self._resize_delay.start()
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
