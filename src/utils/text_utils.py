"""Utility functions for text processing and sanitization."""
from typing import Any


def sanitize_text(text: str) -> str:
    """Remove null characters (\\u0000) from a string.

    PostgreSQL text/varchar columns cannot store null bytes,
    which commonly appear in text extracted from PDFs.
    """
    if not isinstance(text, str):
        return text
    return text.replace('\x00', '')


def sanitize_for_db(data: Any) -> Any:
    """Recursively sanitize all strings in a data structure for PostgreSQL.

    Handles nested dicts, lists, and plain strings. Removes null bytes
    (\\u0000) that cause PostgreSQL error 22P05.
    """
    if data is None:
        return data
    if isinstance(data, str):
        return data.replace('\x00', '')
    if isinstance(data, dict):
        return {k: sanitize_for_db(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_for_db(item) for item in data]
    return data
