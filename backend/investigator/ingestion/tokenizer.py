"""Token counting with the embedding model's own tokenizer.

Chunk limits are measured in model tokens, not characters or words: bge-small reads at most
512 tokens and silently drops the rest, so a limit in any other unit can lose text unnoticed.
"""

import re
from functools import lru_cache
from typing import Protocol

TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"
TOKENIZER_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"  # pinned model commit


class Tokenizer(Protocol):
    def spans(self, text: str) -> list[tuple[int, int]]:
        """(start, end) character offsets of every token in `text`."""
        ...


class WordPieceTokenizer:
    """bge-small's WordPiece tokenizer, via the Rust `tokenizers` library."""

    def __init__(self, model: str = TOKENIZER_MODEL, revision: str = TOKENIZER_REVISION):
        from tokenizers import Tokenizer as HFTokenizer

        self._tokenizer = HFTokenizer.from_pretrained(model, revision=revision)
        # The model config may enable truncation at 512 tokens. For chunking we must see every
        # token of a long document, or text past the limit would silently never be chunked.
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def spans(self, text: str) -> list[tuple[int, int]]:
        return self._tokenizer.encode(text, add_special_tokens=False).offsets


class WhitespaceTokenizer:
    """Every run of non-space characters is one token. Deterministic and offline: for tests."""

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [match.span() for match in re.finditer(r"\S+", text)]


@lru_cache
def get_tokenizer() -> WordPieceTokenizer:
    return WordPieceTokenizer()
