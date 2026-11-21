"""Render pages from one verified PDF snapshot in an isolated process."""

import sys

import pypdfium2 as pdfium
from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from filemind.documents import MAX_FILE_BYTES, MAX_PAGES


def main():
    source = sys.stdin.buffer
    term_size = int.from_bytes(source.read(4), "big")
    size = int.from_bytes(source.read(4), "big")
    if not 0 <= term_size <= 65536 or not 0 < size <= MAX_FILE_BYTES:
        raise ValueError("PDF request")
    term = source.read(term_size).decode("utf-8")
    data = source.read(size)
    if len(data) != size:
        raise ValueError("PDF size")
    with pdfium.PdfDocument(data) as doc:
        count = len(doc)
        if not 0 < count <= MAX_PAGES:
            raise ValueError("PDF page count")
        while request := source.read(4):
            number = int.from_bytes(request, "big")
            if not 1 <= number <= count:
                raise ValueError("PDF page")
            png = render(doc, number, term)
            sys.stdout.buffer.write(count.to_bytes(4, "big") + len(png).to_bytes(4, "big"))
            sys.stdout.buffer.write(png)
            sys.stdout.buffer.flush()


def render(doc, number, term):
    page = doc[number - 1]
    try:
        width, height = page.get_size()
        scale = min(3.0, 2200 / max(width, height))
        bitmap = page.render(scale=scale, rev_byteorder=True)
        image = QImage(
            bytes(bitmap.buffer),
            bitmap.width,
            bitmap.height,
            bitmap.stride,
            QImage.Format.Format_RGB888,
        ).copy()
        if term:
            text = page.get_textpage()
            search = text.search(term)
            painter = QPainter(image)
            coords = bitmap.get_posconv(page)
            for _ in range(150):
                hit = search.get_next()
                if not hit:
                    break
                count = text.count_rects(*hit)
                for i in range(count):
                    left, bottom, right, top = text.get_rect(i)
                    x, y = coords.to_bitmap(left, top)
                    x2, y2 = coords.to_bitmap(right, bottom)
                    painter.fillRect(
                        QRectF(x, y, x2 - x, y2 - y).normalized(), QColor(141, 182, 107, 85)
                    )
            painter.end()
        out = QBuffer()
        out.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(out, "PNG"):
            raise ValueError("PDF bitmap")
        return bytes(out.data())
    finally:
        page.close()


if __name__ == "__main__":
    main()
