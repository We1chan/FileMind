"""Compare local indexing throughput and PDF paging on reproducible synthetic files."""

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import tempfile
from collections.abc import Callable, Collection
from contextlib import closing
from pathlib import Path
from time import perf_counter, sleep

from xlsxwriter import Workbook

from filemind.config import AppPaths
from filemind.continuous import ContinuousIndex
from filemind.library import Library
from filemind.storage import LocalStore
from filemind.text_index import bge_tokenizer, keyword_terms
from samples.materialize import write_pdf, write_sample

PDF_LENGTHS = (2, 3, 4, 5, 8, 10, 12, 16, 20, 24, 30, 40)
PROCESSING_OPERATIONS = ("mixed_import", "mixed_watch", "markdown_watch", "markdown_changes")


def create_corpus(directory: Path) -> list[Path]:
    paths = []
    for index in range(24):
        marker = f"Scheduling case {index:02}"
        body = [
            f"{marker}, section {number}. Compare round robin and priority scheduling. "
            "Record context switches, queue length, response time and CPU utilization."
            for number in range(1 + index % 8)
        ]
        for extension in (".pdf", ".docx", ".pptx", ".xlsx", ".md" if index % 2 else ".txt"):
            path = directory / f"case-{index:02}{extension}"
            if extension == ".xlsx":
                with Workbook(path) as book:
                    sheet = book.add_worksheet("Results")
                    for row, text in enumerate(body):
                        sheet.write(row, 0, text)
            else:
                write_sample(path, marker, body)
            paths.append(path)
    return sorted(paths)


def index_files(
    files: list[Path], data: Path, *, managed: bool, new_files: bool = False
) -> tuple[float, int, str]:
    paths = AppPaths.resolve(data)
    paths.prepare()
    store = LocalStore(paths.database)
    store.initialize()
    library = Library(paths, store)
    # Development caches live in the checkout. Only these synthetic fixtures may be indexed here.
    library.project_data = None
    start = perf_counter()
    if managed:
        report = library.import_files(files)
        assert report.count == len(files) and not report.skipped, report
        elapsed = perf_counter() - start
    else:
        source = files[0].parent
        if new_files:
            source = data.parent / f"{data.name}-source"
            source.mkdir()
        library.register_directory(source)
        service = ContinuousIndex(library)
        service.start()
        try:
            wait_until(lambda: store.sources()[0].status == "ready", "source ready")
            if new_files:
                start = perf_counter()
                for path in files:
                    shutil.copyfile(path, source / path.name)
                wait_until(lambda: store.counts()[1] == len(files), "new files indexed")
            elapsed = perf_counter() - start
        finally:
            service.stop()
    assert store.counts() == (len(files), len(files)), store.counts()
    assert all(file.status == "ready" for file in store.files())
    with closing(sqlite3.connect(paths.database)) as connection:
        rows = connection.execute(
            """SELECT f.name, s.location, s.text, s.heading, c.start, c.end, c.location, c.body_terms
            FROM files f JOIN sections s ON s.file_id = f.id JOIN chunks c ON c.section_id = s.id
            ORDER BY f.name, s.id, c.id"""
        ).fetchall()
    assert {row[0] for row in rows} == {path.name for path in files}
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode("ascii")).hexdigest()
    return elapsed, len(rows), digest


def wait_until(predicate: Callable[[], bool], description: str) -> None:
    deadline = perf_counter() + 240
    while not predicate():
        if perf_counter() > deadline:
            raise TimeoutError(description)
        sleep(0.025)


def create_notes(directory: Path) -> list[Path]:
    directory.mkdir()
    notes = [directory / f"note-{index:03}.md" for index in range(120)]
    for index, path in enumerate(notes):
        path.write_text(
            f"# Note {index}\nCompare scheduling, context switches and queue lengths.\n",
            encoding="utf-8",
        )
    return notes


def benchmark_processing(root: Path, repeats: int, operations: Collection[str]) -> None:
    documents = root / "documents"
    documents.mkdir()
    files = create_corpus(documents)
    notes = create_notes(root / "markdown")
    print(
        json.dumps(
            {
                "mixed_files": len(files),
                "mixed_bytes": sum(path.stat().st_size for path in files),
                "markdown_files": len(notes),
            }
        ),
        flush=True,
    )
    for name, entries, managed in (
        ("mixed_import", files, True),
        ("mixed_watch", files, False),
        ("markdown_watch", notes, False),
        ("markdown_changes", notes, False),
    ):
        if name not in operations:
            continue
        trials = [
            index_files(
                entries, root / f"{name}-{i}", managed=managed, new_files=name == "markdown_changes"
            )
            for i in range(repeats)
        ]
        assert len({trial[2] for trial in trials}) == 1
        print(
            json.dumps(
                {
                    "operation": name,
                    "seconds": [round(trial[0], 3) for trial in trials],
                    "median_seconds": round(statistics.median(trial[0] for trial in trials), 3),
                    "chunks": trials[0][1],
                    "sha256": trials[0][2],
                }
            ),
            flush=True,
        )


def benchmark_pdf(root: Path, repeats: int) -> None:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from filemind.ui.pdf_reader import PdfReader

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([])
    reader = PdfReader()
    reader.resize(920, 740)
    reader.show()
    inputs = []
    for count in PDF_LENGTHS:
        source = root / f"slides-{count}.pdf"
        write_pdf(
            source,
            [
                "\n".join(
                    f"Scheduling {line}: page {page} of {count}, response time and throughput."
                    for line in range(10 + page % 26)
                )
                for page in range(1, count + 1)
            ],
        )
        inputs.append((count, source.read_bytes()))
    timings = {"open": [], "turn": []}
    for _ in range(repeats):
        for count, data in inputs:
            for offset, page in enumerate((1, count, max(1, count // 2), count)):
                start = perf_counter()
                if offset == 0:
                    reader.open_page(data, page, "Scheduling")
                else:
                    reader.jump(page - 1)
                while getattr(reader, "rendering", reader.process is not None):
                    if perf_counter() - start > 16:
                        raise TimeoutError(f"Could not render page {page} of {count}")
                    QTest.qWait(2)
                app.processEvents()
                assert not reader.image.isNull()
                assert reader.page.count() == count and reader.page.currentIndex() == page - 1
                timings["open" if offset == 0 else "turn"].append((perf_counter() - start) * 1000)
            reader.reset()
            QTest.qWait(20)
    for name, values in timings.items():
        values.sort()
        print(
            json.dumps(
                {
                    "operation": f"pdf_{name}",
                    "documents": len(inputs),
                    "total_pages": sum(PDF_LENGTHS),
                    "measurements": len(values),
                    "median_ms": round(statistics.median(values), 1),
                    "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1], 1),
                }
            ),
            flush=True,
        )
    reader.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("processing", "pdf"))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--operation", action="append", choices=PROCESSING_OPERATIONS)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.operation and args.scenario != "processing":
        parser.error("--operation is only supported for processing")
    if args.scenario == "processing":
        tuple(keyword_terms("进程调度 scheduling"))
        bge_tokenizer()
    with tempfile.TemporaryDirectory(prefix="filemind-benchmark-") as temporary:
        if args.scenario == "pdf":
            benchmark_pdf(Path(temporary), args.repeats)
        else:
            benchmark_processing(
                Path(temporary), args.repeats, args.operation or PROCESSING_OPERATIONS
            )


if __name__ == "__main__":
    main()
