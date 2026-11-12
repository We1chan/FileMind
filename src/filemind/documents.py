"""Extract plain text and locations from an immutable file snapshot."""

import codecs
import json
import os
import posixpath
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring

EXTENSIONS = (".pdf", ".docx", ".pptx", ".md", ".txt")
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_SECTIONS = 20_000
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_PAGES = 1_000


class DocumentError(ValueError):
    """A file cannot be indexed."""


@dataclass(frozen=True, slots=True)
class Section:
    location: str
    text: str
    first_line: int | None = None


def parse_document(data: bytes, extension: str) -> list[Section]:
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError("文件超过 32 MiB。")
    if extension in (".md", ".txt"):
        return _collect(_text_sections(data))
    if extension not in EXTENSIONS:
        raise DocumentError("不支持此文件格式。")

    executable = Path(sys.executable)
    if executable.stem.lower() == "pythonw":
        executable = executable.with_name("python.exe")
    try:
        process = subprocess.run(
            [str(executable), "-m", "filemind.documents", extension],
            input=data,
            capture_output=True,
            timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise DocumentError("解析超过 25 秒。") from error
    if process.returncode:
        raise DocumentError("无法解析文件。请检查文件格式及完整性。")
    result = json.loads(process.stdout)
    if "error" in result:
        raise DocumentError(result["error"])
    return [Section(**item) for item in result["sections"]]


def _text_sections(data: bytes) -> Iterator[Section]:
    try:
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            content = data.decode("utf-16")
        else:
            try:
                content = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = data.decode("gb18030")
    except UnicodeError as error:
        raise DocumentError(
            "无法识别文本编码。请使用 UTF-8、带 BOM 的 UTF-16 或 GB18030。"
        ) from error
    if "\0" in content:
        raise DocumentError("文件包含二进制数据。")
    if len(content) > MAX_TEXT_CHARS:
        raise DocumentError("提取的文本超过 200 万字符。")
    lines = content.splitlines(keepends=True)
    for start in range(0, len(lines), 48):
        end = min(start + 48, len(lines))
        yield Section(line_location(start + 1, end), "".join(lines[start:end]), start + 1)


def line_location(first: int, last: int) -> str:
    return f"第 {first} 行" if first == last else f"第 {first}–{last} 行"


def _collect(sections: Iterator[Section]) -> list[Section]:
    result = []
    chars = 0
    for section in sections:
        if not section.text.strip():
            continue
        chars += len(section.text)
        if chars > MAX_TEXT_CHARS or len(result) >= MAX_SECTIONS:
            raise DocumentError("提取的文本或段落数量超过上限。")
        result.append(section)
    return result


def _pdf_sections(data: bytes) -> Iterator[Section]:
    from pypdf import PdfReader, apply_configuration

    with apply_configuration(
        maximum_declared_stream_length=MAX_FILE_BYTES,
        array_based_stream_maximum_output_length=MAX_ARCHIVE_BYTES,
        zlib_maximum_output_length=MAX_XML_BYTES,
        lzw_maximum_output_length=MAX_XML_BYTES,
        run_length_maximum_output_length=MAX_XML_BYTES,
        stream_decoding_work_maximum_length=MAX_ARCHIVE_BYTES,
        page_tree_maximum_entries=MAX_PAGES * 4,
    ):
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("PDF 已加密。请导入解密后的副本。")
        if len(reader.pages) > MAX_PAGES:
            raise DocumentError("PDF 超过 1000 页。")
        stream_bytes = 0
        for number, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            page_bytes = len(contents.get_data()) if contents else 0
            stream_bytes += page_bytes
            if page_bytes > MAX_XML_BYTES or stream_bytes > MAX_ARCHIVE_BYTES:
                raise DocumentError("PDF 解压后的内容超过解析上限。")
            yield Section(f"第 {number} 页", page.extract_text())


def _xml(archive: ZipFile, part: str) -> Element:
    with archive.open(part) as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise DocumentError("Office 文件的单个 XML 部件超过 8 MiB。")
    return fromstring(data, forbid_dtd=True)


def _part_path(base: str, target: str) -> str:
    url = urlsplit(target)
    target_path = unquote(url.path)
    if url.scheme or url.netloc or "\\" in target_path or not target_path:
        raise DocumentError("Office 文件包含无效的部件引用。")
    path = posixpath.normpath(
        target_path[1:] if target_path.startswith("/") else posixpath.join(base, target_path)
    )
    if path in (".", "..") or path.startswith(("../", "/")):
        raise DocumentError("Office 文件包含无效的部件引用。")
    return path


def _office_part(archive: ZipFile) -> str:
    for rel in _xml(archive, "_rels/.rels"):
        if rel.get("Type", "").endswith("/officeDocument") and rel.get("TargetMode") != "External":
            return _part_path("", rel.attrib["Target"])
    raise DocumentError("Office 文件缺少正文。")


def _local(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _attribute(element: Element, name: str, default: str = "") -> str:
    return next((v for k, v in element.attrib.items() if k.rsplit("}", 1)[-1] == name), default)


def _paragraph_text(paragraph: Element) -> str:
    parts = []
    for element in paragraph.iter():
        name = _local(element)
        if name == "t":
            parts.append(element.text or "")
        elif name in ("br", "cr"):
            parts.append("\n")
        elif name == "tab":
            parts.append("\t")
    return "".join(parts)


def _word_blocks(container: Element) -> Iterator[Element]:
    for element in container:
        name = _local(element)
        if name in ("p", "tbl"):
            yield element
        elif name in ("sdt", "sdtContent", "customXml", "ins"):
            yield from _word_blocks(element)


def _word_sections(container: Element, prefix: str = "") -> Iterator[Section]:
    paragraphs = tables = 0
    for element in _word_blocks(container):
        if _local(element) == "p":
            paragraphs += 1
            style = element.find("{*}pPr/{*}pStyle")
            heading = style is not None and _attribute(style, "val").lower().startswith(
                ("heading", "title", "标题")
            )
            location = f"{prefix}第 {paragraphs} 段"
            yield Section(f"{location}（标题）" if heading else location, _paragraph_text(element))
            continue

        tables += 1
        for row_number, row in enumerate(element.findall("{*}tr"), 1):
            column = 1
            for cell in row.findall("{*}tc"):
                place = f"{prefix}表 {tables} · 第 {row_number} 行第 {column} 列 · "
                yield from _word_sections(cell, place)
                span = cell.find("{*}tcPr/{*}gridSpan")
                column += max(1, int(_attribute(span, "val", "1"))) if span is not None else 1


def _slides(archive: ZipFile, part: str, root: Element) -> Iterator[Section]:
    slides = root.findall("{*}sldIdLst/{*}sldId")
    if not slides:
        return
    if len(slides) > MAX_PAGES:
        raise DocumentError("PPTX 超过 1000 张幻灯片。")
    base, filename = posixpath.split(part)
    rels = _xml(archive, posixpath.join(base, "_rels", f"{filename}.rels"))
    by_id = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels
        if rel.get("Type", "").endswith("/slide") and rel.get("TargetMode") != "External"
    }
    for number, slide in enumerate(slides, 1):
        rel_id = next(key for key in slide.attrib if key.endswith("}id"))
        content = _xml(archive, _part_path(base, by_id[slide.attrib[rel_id]]))
        shape_tree = content.find("{*}cSld/{*}spTree")
        if shape_tree is not None:
            paragraphs = (_paragraph_text(p) for p in shape_tree.iterfind(".//{*}p"))
            yield Section(f"第 {number} 张幻灯片", "\n".join(paragraphs))


def _office_sections(data: bytes, extension: str) -> Iterator[Section]:
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 5_000 or sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
            raise DocumentError("Office 文件的部件数量或解压体积超过上限。")
        part = _office_part(archive)
        root = _xml(archive, part)
        if extension == ".docx":
            body = root.find("{*}body")
            if body is None:
                raise DocumentError("DOCX 文件缺少正文。")
            yield from _word_sections(body)
        else:
            yield from _slides(archive, part, root)


def _parser_main() -> None:
    try:
        data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise DocumentError("文件超过 32 MiB。")
        extension = sys.argv[1]
        sections = _pdf_sections(data) if extension == ".pdf" else _office_sections(data, extension)
        result = {"sections": [asdict(item) for item in _collect(sections)]}
    except DocumentError as error:
        result = {"error": str(error)}
    except Exception:
        result = {"error": "无法解析文件。请检查文件格式及完整性。"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))


if __name__ == "__main__":
    _parser_main()
