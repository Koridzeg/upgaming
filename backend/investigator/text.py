"""Text helpers shared by dataset preparation and ingestion."""

import hashlib
import unicodedata


def normalize_text(text: str) -> str:
    """Canonical form for duplicate detection: NFKC, then all whitespace runs -> one space.

    NFKC folds compatibility characters (non-breaking space -> space, 'ﬁ' ligature -> 'fi',
    full-width digits -> ASCII digits), so two copies that differ only in such encoding
    details compare equal.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


def content_hash(text: str) -> str:
    """SHA-256 of the normalised text: equal for copies that differ only in formatting."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
