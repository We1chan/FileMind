"""Use the BGE tokenizer for passage boundaries and jieba for FTS terms."""

import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from itertools import accumulate
from pathlib import Path

import jieba
from tokenizers import BertWordPieceTokenizer

from filemind.config import checkout_data_dir, installed_data_dir
from filemind.documents import EXTENSIONS, Section, line_location

MAX_MODEL_TOKENS = 512
PASSAGE_TOKENS = 384
PASSAGE_OVERLAP = 48
VOCAB_PATH = Path(__file__).with_name("resources") / "bge-vocab.txt"
_SUFFIXES = "|".join(re.escape(ext) for ext in sorted(EXTENSIONS, key=len, reverse=True))
_TECHNICAL = re.compile(
    r"[a-z][a-z0-9_]*(?:[+#]+|(?:/[a-z][a-z0-9_]*)+)"
    rf"|(?:{_SUFFIXES})(?![a-z0-9_])|[a-z0-9_]+",
    re.IGNORECASE | re.ASCII,
)
_STOP_WORDS = frozenset(
    "的 了 是 吗 什么 请 如何 怎么 哪些 可以 为 与 和 在 该 有 一种 需要".split()
)


@dataclass(frozen=True, slots=True)
class TextChunk:
    start: int
    end: int
    location: str
    terms: str


@cache
def bge_tokenizer() -> BertWordPieceTokenizer:
    return BertWordPieceTokenizer(str(VOCAB_PATH), lowercase=False)


@cache
def _jieba_tokenizer() -> jieba.Tokenizer:
    directory = (checkout_data_dir() or installed_data_dir()) / "cache" / "jieba"
    directory.mkdir(parents=True, exist_ok=True)
    tokenizer = jieba.Tokenizer()
    tokenizer.tmp_dir = str(directory)
    return tokenizer


def _terms(text: str, *, precise: bool = False) -> Iterator[str]:
    tokenizer = _jieba_tokenizer()
    cut = tokenizer.cut if precise else tokenizer.cut_for_search
    previous = 0
    for match in _TECHNICAL.finditer(text):
        yield from cut(text[previous : match.start()])
        yield match.group()
        previous = match.end()
    yield from cut(text[previous:])


def keyword_terms(text: str, *, precise: bool = False) -> Iterator[str]:
    for word in _terms(text.casefold(), precise=precise):
        if word not in _STOP_WORDS and any(character.isalnum() for character in word):
            # FTS5 must see one token even when the term contains punctuation.
            yield "t" + word.encode("utf-8").hex()


def fts_query(query: str) -> str:
    return " OR ".join(list(dict.fromkeys(keyword_terms(query)))[:64])


def significant_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(keyword_terms(query, precise=True)))[:64]


def catalog_terms(value: str) -> str:
    separated = value.replace("/", " ").replace("\\", " ")
    terms = list(keyword_terms(separated))
    if "_" in separated:
        terms.extend(keyword_terms(separated.replace("_", " ")))
    return " ".join(dict.fromkeys(terms))


def _ranges(text: str) -> Iterator[tuple[int, int]]:
    tokenizer = bge_tokenizer()
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets
    if len(offsets) <= PASSAGE_TOKENS:
        yield 0, len(text)
        return
    first = 0
    while first < len(offsets):
        last = min(first + PASSAGE_TOKENS, len(offsets))
        start = offsets[first][0] if first else 0
        end = offsets[last][0] if last < len(offsets) else len(text)
        # Re-encoding a slice can change how a word at its boundary is split.
        while len(tokenizer.encode(text[start:end], add_special_tokens=False).ids) > PASSAGE_TOKENS:
            last -= 8
            end = offsets[last][0]
        if end > start:
            yield start, end
        if last == len(offsets):
            break
        first = max(first + 1, last - PASSAGE_OVERLAP)


def split_section(section: Section) -> list[TextChunk]:
    line_ends = list(accumulate(map(len, section.text.splitlines(keepends=True))))
    result = []
    for start, end in _ranges(section.text):
        raw = section.text[start:end]
        if not raw.strip():
            continue
        location = section.location
        if section.first_line is not None:
            first = start + len(raw) - len(raw.lstrip())
            last = start + len(raw.rstrip()) - 1
            location = line_location(
                section.first_line + bisect_right(line_ends, first),
                section.first_line + bisect_right(line_ends, last),
            )
        result.append(TextChunk(start, end, location, " ".join(keyword_terms(raw))))
    return result
