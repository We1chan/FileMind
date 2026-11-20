"""Render a single verified PDF page in a disposable process."""

import sys

import pypdfium2 as pdfium
from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from filemind.documents import MAX_FILE_BYTES, MAX_PAGES


def main():
    number = int(sys.argv[1])
    term = sys.stdin.buffer.readline(65536).decode("utf-8").rstrip("\n")
    data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("PDF size")
    with pdfium.PdfDocument(data) as doc:
        if not 1 <= number <= len(doc) <= MAX_PAGES:
            raise ValueError("PDF page")
        page = doc[number - 1]
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
        sys.stdout.buffer.write(len(doc).to_bytes(4, "big") + bytes(out.data()))


if __name__ == "__main__":
    main()
