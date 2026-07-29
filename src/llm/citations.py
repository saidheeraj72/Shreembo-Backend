"""Inline ``[n]`` citation markers.

The parsing rules live here so that "what counts as a citation" — and what
counts as code that merely looks like one — is defined in exactly one place.
"""
import re
from typing import List

# A citation marker, but not a markdown link label like [1](https://…)
_CITATION_RE = re.compile(r"\[(\d{1,3})\](?!\()")
# Fenced blocks and inline code are left completely alone — `arr[10]` is code,
# not a citation.
_CODE_SEGMENT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")


def _rewrite_outside_code(text: str, replace) -> str:
    """Apply *replace* to citation markers, skipping code spans and fences."""
    out: List[str] = []
    for segment in _CODE_SEGMENT_RE.split(text):
        if segment.startswith("`"):
            out.append(segment)
            continue
        segment = _CITATION_RE.sub(replace, segment)
        # Tidy up spacing left behind by removed markers
        segment = re.sub(r" +([.,;:!?])", r"\1", segment)
        out.append(re.sub(r"[ \t]{2,}", " ", segment))
    return "".join(out)


def strip_invalid_citations(text: str, valid_count: int) -> str:
    """Remove citation markers that point at sources which do not exist.

    Models occasionally emit [7] when only five sources were provided; a marker
    the UI cannot resolve is worse than no marker.
    """
    if not text or "[" not in text:
        return text

    def _replace(match: "re.Match") -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= valid_count else ""

    return _rewrite_outside_code(text, _replace)
