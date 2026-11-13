import os
import shutil
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from zipfile import ZipFile

import numpy as np
import pytest
from xlsxwriter import Workbook

from filemind.continuous import ContinuousIndex
from filemind.embeddings import DIMENSIONS, FINGERPRINT, SemanticIndex
from filemind.library import Library
from filemind.qa import Questions
from filemind.search import Search
from filemind.storage import SearchScope


def wait_for(predicate, timeout: float = 12) -> None:
    deadline = monotonic() + timeout
    while not predicate():
        if monotonic() >= deadline:
            pytest.fail("Background index did not reach the expected state")
        sleep(0.05)


def test_catalog_scans_past_the_old_limit_and_retains_large_files(
    library: Library, tmp_path: Path
) -> None:
    root = tmp_path / "disk"
    root.mkdir()
    for index in range(20_100):
        (root / f"skip-{index:05}.bin").touch()
    nested = root / "nested"
    nested.mkdir()
    target = nested / "workspace-Installer.exe"
    with target.open("wb") as stream:
        stream.truncate(35 * 1024 * 1024)
    secret = nested / "credentials.txt"
    secret.write_text("ExcludedNeedle", encoding="utf-8")
    note = nested / "中文笔记.md"
    note.write_text("FileMindMarkdownNeedle", encoding="utf-8")
    hardlink = nested / "硬链接.md"
    os.link(note, hardlink)
    library.link_directory(root)

    search = Search(library)
    assert library.store.sources()[0].status == "ready"
    assert [hit.file.name for hit in search.find("Installer.exe", "filename").hits] == [target.name]
    assert [hit.file.name for hit in search.find("workspace Installer", "keyword").hits] == [
        target.name
    ]
    hits = search.find("FileMindMarkdownNeedle", "keyword").hits
    assert len(hits) == 1 and hits[0].copies == 2
    assert hits[0].file.name in {note.name, hardlink.name}
    assert not search.find("ExcludedNeedle", "keyword").hits
    assert library.store.counts() == (3, 3)
    assert not Questions(search).ask("Installer.exe", "keyword").citations


def test_spreadsheet_rows_and_archive_member_names_are_searchable(
    library: Library, tmp_path: Path
) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    book = root / "Budget.xlsx"
    with Workbook(book) as workbook:
        sheet = workbook.add_worksheet("Results")
        sheet.write("C7", "SpreadsheetUniqueNeedle")
        sheet.write("D9", "OtherRow")
    archive_path = root / "materials.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/参考资料-ArchiveUniqueNeedle.txt", "ArchiveBodyMustRemainUnread")
    library.link_directory(root)
    search = Search(library)
    cell = search.find("SpreadsheetUniqueNeedle", "keyword").hits[0].passages[0]
    assert cell.location == "工作表「Results」· 第 7 行"
    assert library.preview(cell.chunk_id, cell.file.version).text == "SpreadsheetUniqueNeedle"
    archive = (
        search.find("ArchiveUniqueNeedle", "keyword", SearchScope(extension=".zip"))
        .hits[0]
        .passages[0]
    )
    assert "ArchiveUniqueNeedle.txt" in library.preview(archive.chunk_id, archive.file.version).text
    assert "参考资料" in archive.text
    assert not search.find("ArchiveBodyMustRemainUnread", "keyword").hits
    assert not Questions(search).ask("ArchiveUniqueNeedle", "keyword").citations
    assert not (root / "docs").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows executable version resource")
def test_exe_version_resource_is_indexed_as_metadata(library: Library, tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    source = Path(os.environ["SystemRoot"]) / "System32" / "where.exe"
    executable = root / "version-resource-sample.exe"
    shutil.copyfile(source, executable)
    library.link_directory(root)
    results = Search(library).find(
        "Microsoft Corporation", "keyword", SearchScope(extension=".exe")
    )
    assert results.hits[0].file.name == executable.name
    passage = results.hits[0].passages[0]
    assert passage.location == "EXE 版本信息"
    assert "Microsoft Corporation" in library.preview(passage.chunk_id, passage.file.version).text


def test_watcher_handles_new_changed_moved_and_deleted_text_without_refresh(
    library: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("filemind.continuous.EVENT_SECONDS", 0.12)
    root = tmp_path / "documents"
    root.mkdir()
    source_id = library.register_directory(root)
    service = ContinuousIndex(library)
    service.start()
    try:
        wait_for(lambda: library.store.sources()[0].status == "ready")
        original = root / "reference.md"
        original.write_text("InitialWatcherNeedle", encoding="utf-8")
        search = Search(library)
        wait_for(lambda: bool(search.find("InitialWatcherNeedle", "keyword").hits))
        first = library.store.file_at(source_id, "reference.md")
        assert first is not None
        original.write_text("UpdatedWatcherNeedle", encoding="utf-8")
        wait_for(lambda: bool(search.find("UpdatedWatcherNeedle", "keyword").hits))
        changed = library.store.file_at(source_id, "reference.md")
        assert (
            changed is not None and changed.id == first.id and changed.version == first.version + 1
        )
        moved = root / "moved.txt"
        original.rename(moved)
        wait_for(
            lambda: (
                [hit.file.name for hit in search.find("UpdatedWatcherNeedle", "keyword").hits]
                == [moved.name]
            )
        )
        moved.unlink()
        wait_for(lambda: library.store.file_at(source_id, "moved.txt").status == "missing")
        assert not search.find("UpdatedWatcherNeedle", "keyword").hits

        service.pause(True)
        another = root / "paused.txt"
        another.write_text("PausedWatcherNeedle", encoding="utf-8")
        assert library.store.get_preference("pause_monitoring") == "1"
        sleep(0.35)
        assert library.store.file_at(source_id, "paused.txt") is None
        service.pause(False)
        wait_for(lambda: bool(search.find("PausedWatcherNeedle", "keyword").hits))
    finally:
        service.stop()


def test_embeddings_are_committed_during_a_directory_scan(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_started = Event()
    release_scan = Event()

    def scan_in_progress(*args: object) -> None:
        scan_started.set()
        release_scan.wait()

    class Model:
        uses_gpu = True

        def embed(self, texts: list[str]) -> np.ndarray:
            assert scan_started.wait(5)
            return np.ones((len(texts), DIMENSIONS), dtype="<f4")

    note = tmp_path / "notes.txt"
    note.write_text(
        "".join(f"Sample {index}\n" + "\n" * 47 for index in range(65)), encoding="utf-8"
    )
    library.import_files([note])
    library.paths.model.parent.mkdir(parents=True, exist_ok=True)
    library.paths.model.touch()
    monkeypatch.setattr("filemind.embeddings.BgeModel", lambda _: Model())
    service = ContinuousIndex(library)
    monkeypatch.setattr(service, "_update", scan_in_progress)
    scope = SearchScope(content_only=True)
    assert len(library.store.embedding_inputs(FINGERPRINT, scope, limit=100)) == 65

    service.start()
    try:
        assert scan_started.wait(5)
        wait_for(lambda: len(list(library.store.embeddings(FINGERPRINT, scope))) == 65)
    finally:
        release_scan.set()
        service.stop()


def test_copy_reuses_vector_and_refreshes_cached_search(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = []

    class Model:
        uses_gpu = True

        def embed(self, texts: list[str]) -> np.ndarray:
            generated.extend(texts)
            return np.full((len(texts), DIMENSIONS), DIMENSIONS**-0.5, dtype="<f4")

    model = library.paths.model
    model.parent.mkdir()
    model.touch()
    monkeypatch.setattr("filemind.embeddings.BgeModel", lambda _: Model())
    semantic = SemanticIndex(model, library.store)
    scope = SearchScope(content_only=True)
    first, second = tmp_path / "source-a.txt", tmp_path / "source-b.txt"
    text = "ReusableCopyNeedle"
    first.write_text(text, encoding="utf-8")
    second.write_text(text, encoding="utf-8")

    library.import_files([first, second])
    records = library.store.files()
    assert semantic.populate_batch(SearchScope(file_ids=(records[0].id,)))
    assert len(semantic.rank("needle", scope, lambda: False, populate=False)) == 1
    revision = library.store.vector_revision
    assert semantic.populate_batch()
    stored = list(library.store.embeddings(FINGERPRINT, scope))
    appended = next(chunk_id for file_id, chunk_id, _ in stored if file_id == records[1].id)
    assert library.store.vector_updates_since(revision)[1] == {appended}
    assert len(semantic.rank("needle", scope, lambda: False, populate=False)) == 2
    assert generated.count(text) == 1

    library.store.set_embeddings(FINGERPRINT, [(stored[0][2], stored[0][1])])
    assert len(semantic.rank("needle", scope, lambda: False, populate=False)) == 2
