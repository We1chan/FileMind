"""Measure file-level retrieval on the synthetic P0 corpus."""

import argparse
import json
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from filemind.config import AppPaths
from filemind.embeddings import MODEL_ID, MODEL_SHA256
from filemind.library import Library
from filemind.search import Search, SearchMode
from filemind.storage import LocalStore
from samples.materialize import write_sample


def evaluate(search: Search, mode: SearchMode, queries: list, file_count: int) -> dict:
    started = time.perf_counter()
    warm = search.find("FileMind 评测预热", mode, limit=file_count)
    cold_ms = (time.perf_counter() - started) * 1_000
    if warm.mode != mode:
        return {"skipped": warm.notice}

    answered = []
    unanswerable = []
    latencies = []
    for question, expected in queries:
        started = time.perf_counter()
        result = search.find(question, mode, limit=file_count)
        elapsed = (time.perf_counter() - started) * 1_000
        if result.mode != mode:
            return {"skipped": result.notice}
        names = [hit.file.name for hit in result.hits]
        latencies.append(elapsed)
        if expected:
            rank = next((rank for rank, name in enumerate(names, 1) if name in expected), None)
            answered.append(
                {"query": question, "expected": expected, "rank": rank, "top5": names[:5]}
            )
        else:
            unanswerable.append({"query": question, "returned": len(names), "top5": names[:5]})

    ranks = [record["rank"] for record in answered]
    return {
        "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / len(ranks),
        "mrr": sum(1 / rank if rank is not None else 0 for rank in ranks) / len(ranks),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": statistics.quantiles(latencies, n=100, method="inclusive")[94],
        "first_query_ms": cold_ms,
        "answered": answered,
        "unanswerable": unanswerable,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=AppPaths.resolve().model)
    parser.add_argument("--output", type=Path, default=Path("data/evaluation.json"))
    args = parser.parse_args()
    corpus = json.loads(Path(__file__).with_name("corpus.json").read_text(encoding="utf-8"))
    documents, queries = corpus["documents"], corpus["queries"]
    report = {
        "files": len(documents),
        "queries": len(queries),
        "environment": {
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "python": sys.version.split()[0],
        },
        "model": {"id": MODEL_ID, "sha256": MODEL_SHA256} if args.model.is_file() else None,
        "modes": {},
    }
    with tempfile.TemporaryDirectory(prefix="filemind-eval-") as directory:
        root = Path(directory)
        inputs = root / "corpus"
        inputs.mkdir()
        paths = AppPaths.resolve(root / "app")
        paths.prepare()
        if args.model.is_file():
            paths.model.parent.mkdir()
            shutil.copyfile(args.model, paths.model)
        store = LocalStore(paths.database)
        store.initialize()
        library = Library(paths, store)
        files = []
        for name, title, paragraphs in documents:
            file = inputs / name
            write_sample(file, title, paragraphs)
            files.append(file)
        started = time.perf_counter()
        imported = library.import_files(files)
        report["import_index_ms"] = (time.perf_counter() - started) * 1_000
        failed = [file.name for file in store.files() if file.status != "ready"]
        if imported.count != len(files) or failed or not store.fts5_available:
            raise RuntimeError(f"Corpus indexing failed: {imported.skipped}, {failed}")
        search = Search(library)
        for mode in ("keyword", "semantic", "hybrid"):
            report["modes"][mode] = evaluate(search, mode, queries, len(files))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for mode, metrics in report["modes"].items():
        if "skipped" in metrics:
            print(f"{mode}: skipped")
        else:
            print(
                f"{mode}: Hit@5={metrics['hit_at_5']:.3f}, MRR={metrics['mrr']:.3f}, p95={metrics['latency_p95_ms']:.1f} ms"
            )


if __name__ == "__main__":
    main()
