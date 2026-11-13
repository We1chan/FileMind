"""Rank files using BM25, cosine similarity, or reciprocal rank fusion."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from filemind.embeddings import ModelError, SemanticIndex
from filemind.library import Library, _continue
from filemind.storage import FileRecord, Passage, SearchScope
from filemind.text_index import catalog_terms, fts_query

SearchMode = Literal["keyword", "semantic", "hybrid", "filename"]
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
        wait_for_vectors: bool = True,
        cancelled: Callable[[], bool] = _continue,
    ) -> SearchResult:
        query = query.strip()[:MAX_QUERY_CHARS]
        if not query or cancelled():
            return SearchResult((), mode)
        if mode == "filename":
            ids = self.store.filename_ranks(query, scope, max(120, limit * 4))
            records = self.store.files_by_id(set(ids))
            hits = []
            for file_id in ids:
                file = records.get(file_id)
                if file is not None and self.library.catalog_current(file):
                    hits.append(SearchHit(file, ()))
                    if len(hits) >= limit:
                        break
            return SearchResult(tuple(hits), mode)

        notice = ""
        semantic_rows = []
        if mode != "keyword":
            try:
                semantic_rows = self.semantic.rank(
                    query, scope, cancelled, populate=wait_for_vectors
                )
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
        catalog_ids = []
        if mode != "semantic" and not scope.content_only:
            terms = catalog_terms(query).split()[:24]
            catalog_ids = self.store.catalog_ranks(" AND ".join(terms), scope)

        file_ids = {file_id for file_id, _ in keyword_rows}
        file_ids.update(file_id for file_id, _ in semantic_rows)
        file_ids.update(catalog_ids)
        files = self.store.files_by_id(file_ids)
        current: dict[int, bool] = {}
        rank_limit = max(150, limit * 4)

        def group(rows: list[tuple[int, int]]) -> dict[int, list[int]]:
            results: dict[int, list[int]] = {}
            for file_id, chunk_id in rows:
                if cancelled():
                    break
                if len(results) >= rank_limit and file_id not in results:
                    continue
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
        catalog = {
            file_id: keyword.get(file_id, [])
            for file_id in catalog_ids
            if file_id in files and self.library.catalog_current(files[file_id])
        }
        keyword = {**catalog, **keyword}
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
            if found or file_id in catalog:
                hits.append(SearchHit(files[file_id], found))
        if not all(current.values()):
            notice += "部分文件已变更或无法访问，请刷新索引。"
        return SearchResult(tuple(hits), mode, notice)
