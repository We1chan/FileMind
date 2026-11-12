"""Build synthetic documents in the five supported formats."""

from pathlib import Path

from docx import Document
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def write_pdf(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if not text:
            continue
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        ),
                    }
                ),
            }
        )
        operations = ["BT /F1 11 Tf 14 TL 42 740 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operations.append(f"({escaped}) Tj T*")
        stream = DecodedStreamObject()
        stream.set_data("\n".join([*operations, "ET"]).encode("ascii"))
        page.replace_contents(stream)
    writer.write(path)


def write_sample(path: Path, title: str, paragraphs: list[str]) -> None:
    if path.suffix == ".pdf":
        write_pdf(path, [f"{title}\n{paragraphs[0]}", *paragraphs[1:]])
    elif path.suffix == ".docx":
        doc = Document()
        doc.add_heading(title, 1)
        doc.add_paragraph(paragraphs[0])
        for paragraph in paragraphs[1:]:
            table = doc.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "补充说明"
            table.cell(0, 1).text = paragraph
        doc.save(path)
    elif path.suffix == ".pptx":
        deck = Presentation()
        for paragraph in paragraphs:
            slide = deck.slides.add_slide(deck.slide_layouts[5])
            slide.shapes.title.text = title
            text_box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(3))
            text_box.text_frame.text = paragraph
        deck.save(path)
    else:
        heading = f"# {title}" if path.suffix == ".md" else title
        path.write_text("\n\n".join([heading, *paragraphs]) + "\n", encoding="utf-8")
