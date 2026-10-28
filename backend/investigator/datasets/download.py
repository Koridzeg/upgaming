"""Reproducible downloads: a pinned URL plus a SHA-256 check of the bytes received."""

import hashlib
import logging
import shutil
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, sha256: str) -> Path:
    """Download `url` to `dest`, unless a file with the expected hash is already there."""
    if dest.exists() and sha256_of(dest) == sha256:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary name and rename at the end, so an interrupted download
    # never leaves a truncated file that looks complete.
    partial = dest.with_suffix(dest.suffix + ".part")
    logger.info("Downloading %s", url)
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, length=1 << 20)

    actual = sha256_of(partial)
    if actual != sha256:
        partial.unlink()
        raise ValueError(f"Checksum mismatch for {url}: expected {sha256}, got {actual}")
    partial.replace(dest)
    return dest
