"""Rank files using BM25, cosine similarity, or reciprocal rank fusion."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from filemind.embeddings import ModelError, SemanticIndex
from filemind.library import Library, _continue
from filemind.storage import FileRecord, Passage, SearchScope
from filemind.text_index import catalog_terms, fts_query, significant_terms

SearchMode = Literal["keyword", "semantic", "hybrid", "filename"]
MAX_QUERY_CHARS = 1_500


@dataclass(frozen=True, slots=True)
class SearchHit:
    file: FileRecord
    passages: tuple[Passage, ...]
    copies: int = 1


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    mode: SearchMode
    notice: str = ""


type ContentKey = str | int
type Ranking = dict[ContentKey, tuple[int, list[int]]]


def _content_hash(file: FileRecord) -> str | None:
    return file.sha256 if file.size else None


def _fuse(keyword: Ranking, semantic: Ranking) -> Ranking:
    scores: dict[ContentKey, float] = {}
    passages: Ranking = {}
    for ranking in (keyword, semantic):
        for rank, (key, (file_id, chunk_ids)) in enumerate(ranking.items(), 1):
            scores[key] = scores.get(key, 0.0) + 1 / (60 + rank)
            representative, current = passages.setdefault(key, (file_id, []))
            if representative == file_id:
                current.extend(chunk_ids)
            elif not current and chunk_ids:
                passages[key] = file_id, list(chunk_ids)
    return {
        key: passages[key]
        for key in sorted(scores, key=lambda key: (-scores[key], passages[key][0]))
    }


class Search:
    def __init__(self, library: Library) -> None:
        self.library = library
        self.store = library.store
        self.semantic = SemanticIndex(library.paths.model, self.store)

    def _count_copies(self, hits: list[SearchHit], scope: SearchScope) -> tuple[SearchHit, ...]:
        hashes = {sha256 for hit in hits if (sha256 := _content_hash(hit.file))}
        counts = self.store.copy_counts(hashes, scope)
        return tuple(
            replace(hit, copies=counts.get(_content_hash(hit.file) or "", 1)) for hit in hits
        )

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
        keyword_rows = []
        if mode != "semantic":
            terms = significant_terms(query)
            strict = " AND ".join(terms)
            keyword_rows = self.store.keyword_ranks(strict, scope)
            if not keyword_rows:
                relaxed = " OR ".join(terms) if len(terms) >= 5 else fts_query(query)
                if relaxed != strict:
                    keyword_rows = self.store.keyword_ranks(
                        relaxed, scope, terms=terms if len(terms) >= 5 else ()
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

        def group(rows: list[tuple[int, int]]) -> Ranking:
            results: Ranking = {}
            for file_id, chunk_id in rows:
                if cancelled():
                    break
                file = files.get(file_id)
                if file is None:
                    continue
                key = _content_hash(file) or file_id
                if len(results) >= rank_limit and key not in results:
                    continue
                if file_id not in current:
                    current[file_id] = self.library.metadata_current(file)
                if current[file_id]:
                    representative, passages = results.setdefault(key, (file_id, []))
                    if file_id == representative and len(passages) < passages_per_file:
                        passages.append(chunk_id)
            return results

        keyword = group(keyword_rows)
        semantic = group(semantic_rows)
        catalog: Ranking = {}
        for file_id in catalog_ids:
            file = files.get(file_id)
            if file is not None and self.library.catalog_current(file):
                key = _content_hash(file) or file_id
                catalog.setdefault(key, keyword.get(key, (file_id, [])))
        keyword = {**catalog, **keyword}
        if mode == "hybrid":
            ranked = _fuse(keyword, semantic)
        else:
            ranked = keyword if mode == "keyword" else semantic
        selected = [
            (key, (file_id, list(dict.fromkeys(chunk_ids))[:passages_per_file]))
            for key, (file_id, chunk_ids) in list(ranked.items())[:limit]
        ]
        passages = self.store.passages(
            [chunk_id for _, (_, chunk_ids) in selected for chunk_id in chunk_ids]
        )
        hits = []
        for key, (file_id, chunk_ids) in selected:
            found = tuple(passages[chunk_id] for chunk_id in chunk_ids if chunk_id in passages)
            if found or key in catalog:
                hits.append(SearchHit(files[file_id], found))
        if not all(current.values()):
            notice += "部分文件已变更或无法访问，请刷新索引。"
        return SearchResult(self._count_copies(hits, scope), mode, notice)
