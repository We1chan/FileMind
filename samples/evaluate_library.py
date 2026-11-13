"""Measure retrieval against locally labelled files in an existing library."""

import argparse
import json
import sqlite3
import statistics
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from time import perf_counter

from filemind.config import AppPaths
from filemind.library import Library
from filemind.search import Search, SearchMode
from filemind.storage import LocalStore


class ReadOnlyStore(LocalStore):
    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA temp_store = MEMORY")
            yield connection


def evaluate(search: Search, mode: SearchMode, cases: list[dict]) -> dict:
    warm = search.find(cases[0]["query"], mode, limit=10, wait_for_vectors=False)
    if warm.mode != mode:
        return {"skipped": warm.notice}
    rows = []
    for case in cases:
        targets = {target["sha256"] for target in case["targets"]}
        start = perf_counter()
        result = search.find(case["query"], mode, limit=10, wait_for_vectors=False)
        elapsed = (perf_counter() - start) * 1_000
        rank = next((i for i, hit in enumerate(result.hits, 1) if hit.file.sha256 in targets), None)
        rows.append(
            {
                "query": case["query"],
                "answerable": bool(targets),
                "rank": rank,
                "latency_ms": elapsed,
                "top10": [
                    {"id": hit.file.id, "name": hit.file.name, "sha256": hit.file.sha256}
                    for hit in result.hits
                ],
            }
        )
    ranks = [row["rank"] for row in rows if row["answerable"]]
    latencies = [row["latency_ms"] for row in rows]
    return {
        "answered": len(ranks),
        "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks),
        "mrr_at_10": sum(1 / rank for rank in ranks if rank is not None) / len(ranks),
        "no_answer_queries": sum(not row["answerable"] for row in rows),
        "no_answer_with_results": sum(bool(row["top10"]) for row in rows if not row["answerable"]),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": statistics.quantiles(latencies, n=100, method="inclusive")[94],
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--modes",
        choices=["keyword", "semantic", "hybrid"],
        nargs="+",
        default=["keyword", "hybrid"],
    )
    args = parser.parse_args()
    paths = AppPaths.resolve(args.data_dir)
    paths.use_local_temp()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if len(cases) < 2 or not any(case["targets"] for case in cases):
        parser.error("Provide at least two queries and one labelled target.")
    store = ReadOnlyStore(paths.database)
    with store._session() as connection:
        store.fts5_available = (
            connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunk_fts'").fetchone()
            is not None
        )
        report = {
            "files": connection.execute(
                "SELECT count(*) FROM files WHERE status != 'missing'"
            ).fetchone()[0],
            "chunks": connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "vectors": connection.execute(
                "SELECT count(*) FROM chunks WHERE vector IS NOT NULL"
            ).fetchone()[0],
            "modes": {},
        }
    search = Search(Library(paths, store))
    for mode in args.modes:
        print(f"Measuring {mode} for {len(cases)} queries", flush=True)
        metrics = evaluate(search, mode, cases)
        report["modes"][mode] = metrics
        print({key: value for key, value in metrics.items() if key != "cases"}, flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
