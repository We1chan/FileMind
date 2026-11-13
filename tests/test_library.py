import os
from pathlib import Path

import pytest

from filemind.library import FileAccessError, Library
from filemind.search import Search
from filemind.storage import SearchScope
from filemind.text_index import PASSAGE_TOKENS, bge_tokenizer
from samples.materialize import write_pdf, write_sample


def test_import_keeps_same_named_copies_and_special_terms(library: Library, tmp_path: Path) -> None:
    first = tmp_path / "first" / "实验.txt"
    second = tmp_path / "second" / "实验.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes("文法\r\n\r\nFIRST/FOLLOW、B+、C++ 和 .pdf\r\n".encode("utf-8"))
    second.write_text("FIRST FOLLOWS B C 资料。", encoding="utf-8")
    secret = tmp_path / "credentials.txt"
    secret.write_text("private", encoding="utf-8")
    inside = library.paths.root / "ignored.txt"
    inside.write_text("app data", encoding="utf-8")

    report = library.import_files([first, second, secret, inside])
    assert report.count == 2 and len(report.skipped) == 2
    copies = library.store.files()
    assert copies[0].name == copies[1].name == "实验.txt"
    assert library.path_for(copies[0]).read_bytes() == first.read_bytes()
    assert library.path_for(copies[1]).read_bytes() == second.read_bytes()
    search = Search(library)
    for term in ("FIRST/FOLLOW", "B+", "C++", ".pdf"):
        result = search.find(term, "keyword")
        assert [hit.file.id for hit in result.hits] == [copies[0].id]
        passage = result.hits[0].passages[0]
        assert passage.location == "第 1–3 行"
        assert term in library.preview(passage.chunk_id, passage.file.version).text
    fallback = search.find("C++", "semantic")
    assert fallback.mode == "keyword" and "语义检索未启用" in fallback.notice
    assert search.find("C++", scope=SearchScope(extension=".md")).hits == ()


def test_binary_locations_and_ocr(library: Library, tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    docx = tmp_path / "table.docx"
    pptx = tmp_path / "slides.pptx"
    blank = tmp_path / "scan.pdf"
    write_pdf(pdf, ["", "PageUniqueWord"])
    write_pdf(blank, [""])
    write_sample(docx, "HeadingUniqueWord", ["ParagraphUniqueWord", "TableUniqueWord"])
    write_sample(pptx, "演示", ["slide number one", "SlideUniqueWord"])
    library.import_files([pdf, docx, pptx, blank])
    assert {file.name: file.status for file in library.store.files()} == {
        "manual.pdf": "ready",
        "scan.pdf": "ocr",
        "slides.pptx": "ready",
        "table.docx": "ready",
    }
    search = Search(library)
    for term, location in {
        "PageUniqueWord": "第 2 页",
        "HeadingUniqueWord": "第 1 段（标题）",
        "ParagraphUniqueWord": "第 2 段",
        "TableUniqueWord": "表 1 · 第 1 行",
        "SlideUniqueWord": "第 2 张幻灯片",
    }.items():
        passage = search.find(term, "keyword").hits[0].passages[0]
        assert passage.location == location
        assert term in passage.text


def test_pdf_with_invalid_font_unicode_remains_searchable(library: Library, tmp_path: Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    source = tmp_path / "invalid-font.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    cmap = DecodedStreamObject()
    cmap.set_data(b"1 beginbfchar\n<41> <D800>\nendbfchar")
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): cmap,
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 230 Td (A uniquePDFtext) Tj ET")
    page.replace_contents(stream)
    writer.write(source)

    library.import_files([source])
    passage = Search(library).find("uniquePDFtext", "keyword").hits[0].passages[0]
    assert passage.location == "第 1 页"
    assert passage.text == "\ufffd uniquePDFtext"


def test_linked_refresh_checks_content_and_defers_missing_cleanup(
    library: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "课程目录"
    root.mkdir()
    note = root / "笔记.md"
    note.write_text("FIRST/FOLLOW", encoding="utf-8")
    private = root / ".env.txt"
    private.write_text("private", encoding="utf-8")
    old_stat = note.stat()
    source_id = library.link_directory(root)
    search = Search(library)
    scope = SearchScope(source="linked")
    first = search.find("FIRST/FOLLOW", "keyword", scope).hits[0].passages[0]
    assert len(library.store.files()) == 1 and not any(library.paths.workspace.iterdir())
    assert note.read_text(encoding="utf-8") == "FIRST/FOLLOW"

    note.write_text("replacement!", encoding="utf-8")
    os.utime(note, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    with pytest.raises(FileAccessError, match="原文已变化"):
        library.preview(first.chunk_id, first.file.version)
    library.refresh()
    second = search.find("replacement", "keyword", scope).hits[0].passages[0]
    assert second.file.id == first.file.id and second.file.version == first.file.version + 1
    assert library.store.passage(first.chunk_id, first.file.version) is None
    assert search.find("FIRST/FOLLOW", "keyword", scope).hits == ()

    note.unlink()
    with monkeypatch.context() as patched:
        patched.setattr(library, "_discover", lambda *_: ({}, "部分目录无法访问。"))
        library.refresh()
    assert library.store.passage(second.chunk_id, second.file.version) is not None
    assert library.store.sources()[0].status == "failed"
    assert not search.find("replacement", "keyword", scope).hits
    library.refresh()
    assert library.store.files()[0].status == "missing"
    assert library.store.passage(second.chunk_id, second.file.version) is None
    note.write_text("restored linked note", encoding="utf-8")
    library.refresh()
    assert library.store.files()[0].status == "ready"
    library.store.remove_source(source_id)
    assert not library.store.files()
    assert note.read_text(encoding="utf-8") == "restored linked note"
    assert private.read_text(encoding="utf-8") == "private"


def test_linked_exclusions_prune_existing_records_and_keep_shared_documents(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from filemind import library as library_module

    root = tmp_path / "Drive"
    cache = root / "Tool" / "CACHE"
    shared = root / "ProgramFiles" / "WeChat" / "xwechat_files" / "msg" / "file"
    unwanted = root / "custom"
    for directory in (cache, shared, unwanted):
        directory.mkdir(parents=True)
    generated = cache / "generated.txt"
    license_file = root / "LICENSE.txt"
    user_file = unwanted / "sample.txt"
    lecture = shared / "lecture.txt"
    for file in (generated, license_file, user_file, lecture):
        file.write_text(f"indexseed {file.stem}", encoding="utf-8")
    with monkeypatch.context() as patched:
        patched.setattr(library_module, "_noise_path", lambda *_: False)
        source_id = library.link_directory(root)
    ids = {file.name: file.id for file in library.store.files()}
    assert set(ids) == {file.name for file in (generated, license_file, user_file, lecture)}

    library.set_excluded_directories([unwanted])
    reopened = Library(library.paths, library.store)
    assert reopened.excluded_directories == (unwanted,)
    with monkeypatch.context() as patched:
        discover = reopened._discover

        def warn(*args):
            found, _ = discover(*args)
            return found, "部分文件或子目录无法访问。"

        patched.setattr(reopened, "_discover", warn)
        reopened.refresh()
    assert [file.name for file in library.store.files()] == [lecture.name]
    assert [hit.file.id for hit in Search(reopened).find("indexseed", "keyword").hits] == [
        ids[lecture.name]
    ]
    assert all(file.is_file() for file in (generated, license_file, user_file, lecture))
    reopened.set_excluded_directories([])
    reopened.refresh()
    assert {file.name for file in library.store.source_files(source_id).values()} == {
        lecture.name,
        user_file.name,
    }


def test_search_folds_identical_content_within_scope_and_keeps_a_current_preview(
    library: Library, tmp_path: Path
) -> None:
    notes = tmp_path / "source"
    notes.mkdir()
    first, second = notes / "copy-a.txt", notes / "copy-b.txt"
    for path in (first, second):
        path.write_text("uniqueDuplicateNeedle", encoding="utf-8")
    library.link_directory(notes)
    search = Search(library)
    hits = search.find("uniqueDuplicateNeedle", "keyword").hits
    assert len(hits) == 1 and hits[0].copies == 2
    names = search.find("copy-", "filename").hits
    assert {hit.file.name for hit in names} == {first.name, second.name}
    assert all(hit.copies == 1 for hit in names)
    records = {file.name: file for file in library.store.files()}
    second_id = records[second.name].id
    scoped = search.find(
        "uniqueDuplicateNeedle", "keyword", SearchScope(file_ids=(second_id,))
    ).hits[0]
    assert scoped.file.id == second_id and scoped.copies == 1
    first.unlink()
    current = search.find("uniqueDuplicateNeedle", "keyword").hits[0]
    assert current.file.id == second_id
    passage = current.passages[0]
    assert library.preview(passage.chunk_id, current.file.version).text == "uniqueDuplicateNeedle"


@pytest.mark.parametrize("mode", ["filename", "keyword"])
def test_search_lists_empty_files_separately(library: Library, tmp_path: Path, mode: str) -> None:
    first, second = tmp_path / "todo-a.txt", tmp_path / "todo-b.txt"
    for path in (first, second):
        path.touch()
    library.import_files([first, second])
    records = library.store.files()
    assert all(file.size == 0 for file in records) and records[0].sha256 == records[1].sha256

    hits = Search(library).find("todo", mode).hits
    assert {hit.file.name for hit in hits} == {first.name, second.name}
    assert all(hit.copies == 1 for hit in hits)


def test_retries_a_failed_parse_and_uses_actual_wordpiece_boundaries(
    library: Library, tmp_path: Path
) -> None:
    source = tmp_path / "failure.pdf"
    source.write_bytes(b"invalid PDF")
    library.import_files([source])
    broken = library.store.files()[0]
    assert broken.status == "failed" and broken.reason
    write_pdf(library.path_for(broken), ["FixedUniquePDF"])
    library.retry(broken.id)
    assert library.preview_file(broken.id).text.strip() == "FixedUniquePDF"

    text = "实验段落" * 230 + "unique_tail"
    note = tmp_path / "long.txt"
    note.write_text(text, encoding="utf-8")
    library.import_files([note])
    search = Search(library)
    tail = search.find("unique_tail", "keyword").hits[0].passages[0]
    assert tail.location == "第 1 行" and tail.text.endswith("unique_tail")
    assert tail.section_text == text and tail.start > 0
    assert len(bge_tokenizer().encode(tail.text, add_special_tokens=False).ids) <= PASSAGE_TOKENS


def test_real_model_supports_semantic_and_hybrid_search(library: Library, tmp_path: Path) -> None:
    model_path = os.environ.get("FILEMIND_TEST_MODEL")
    if not model_path:
        pytest.skip("Set FILEMIND_TEST_MODEL to the verified BGE ONNX model")
    expected, other = tmp_path / "books.txt", tmp_path / "sports.txt"
    expected.write_text("其他读者预约的图书不可延长借期。", encoding="utf-8")
    other.write_text("五人制足球每队有一名守门员。", encoding="utf-8")
    library.import_files([expected, other])
    search = Search(library)
    search.semantic.path = Path(model_path)
    for mode in ("semantic", "hybrid"):
        result = search.find("预订过的书还能续借吗", mode)
        assert result.mode == mode and result.hits[0].file.name == expected.name
