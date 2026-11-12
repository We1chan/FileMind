"""Rank files using BM25, cosine similarity, or reciprocal rank fusion."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from filemind.embeddings import ModelError, SemanticIndex
from filemind.library import Library, _continue
from filemind.storage import FileRecord, Passage, SearchScope
from filemind.text_index import fts_query

SearchMode = Literal["keyword", "semantic", "hybrid"]
MAX_QUERY_CHARS = 1_500


@dataclass(frozen=True, slots=True)
class SearchHit:
    file: FileRecord
    passages: tuple[Passage, ...]


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    mode: SearchMode
    notice: str = ""


def _fuse(keyword: dict[int, list[int]], semantic: dict[int, list[int]]) -> dict[int, list[int]]:
    scores: dict[int, float] = {}
    passages: dict[int, list[int]] = {}
    for ranking in (keyword, semantic):
        for rank, (file_id, chunk_ids) in enumerate(ranking.items(), 1):
            scores[file_id] = scores.get(file_id, 0.0) + 1 / (60 + rank)
            passages.setdefault(file_id, []).extend(chunk_ids)
    return {
        file_id: list(dict.fromkeys(passages[file_id]))
        for file_id in sorted(scores, key=lambda key: (-scores[key], key))
    }


class Search:
    def __init__(self, library: Library) -> None:
        self.library = library
        self.store = library.store
        self.semantic = SemanticIndex(library.paths.model, self.store)

    def find(
        self,
        query: str,
        mode: SearchMode = "hybrid",
        scope: SearchScope = SearchScope(),
        *,
        limit: int = 30,
        passages_per_file: int = 1,
        cancelled: Callable[[], bool] = _continue,
    ) -> SearchResult:
        query = query.strip()[:MAX_QUERY_CHARS]
        if not query or cancelled():
            return SearchResult((), mode)

        notice = ""
        semantic_rows = []
        if mode != "keyword":
            try:
                semantic_rows = self.semantic.rank(query, scope, cancelled)
            except ModelError as error:
                mode = "keyword"
                notice = f"语义检索未启用。{error}"
                if self.store.fts5_available:
                    notice += "已使用关键词检索。"
        if not self.store.fts5_available and mode != "semantic":
            notice += "当前 SQLite 缺少 FTS5，关键词检索不可用。"
            if mode == "hybrid":
                mode = "semantic"
        keyword_rows = (
            self.store.keyword_ranks(fts_query(query), scope) if mode != "semantic" else []
        )

        files = {file.id: file for file in self.store.files()}
        current: dict[int, bool] = {}

        def group(rows: list[tuple[int, int]]) -> dict[int, list[int]]:
            results: dict[int, list[int]] = {}
            for file_id, chunk_id in rows:
                if cancelled():
                    break
                if file_id not in current:
                    current[file_id] = file_id in files and self.library.metadata_current(
                        files[file_id]
                    )
                if current[file_id]:
                    passages = results.setdefault(file_id, [])
                    if len(passages) < passages_per_file:
                        passages.append(chunk_id)
            return results

        keyword = group(keyword_rows)
        semantic = group(semantic_rows)
        if mode == "hybrid":
            ranked = _fuse(keyword, semantic)
        else:
            ranked = keyword if mode == "keyword" else semantic
        selected = list(ranked.items())[:limit]
        passages = self.store.passages(
            [chunk_id for _, chunk_ids in selected for chunk_id in chunk_ids[:passages_per_file]]
        )
        hits = []
        for file_id, chunk_ids in selected:
            found = tuple(
                passages[chunk_id]
                for chunk_id in chunk_ids[:passages_per_file]
                if chunk_id in passages
            )
            if found:
                hits.append(SearchHit(files[file_id], found))
        if not all(current.values()):
            notice += "部分文件已变更或无法访问，请刷新索引。"
        return SearchResult(tuple(hits), mode, notice)
