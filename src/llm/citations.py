"""Inline ``[n]`` citation markers.

Both the generator and the answer judge rewrite these — the judge renumbers
them after pruning unused sources — so the parsing rules live in one place.
"""
import re
from typing import Dict, List

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


def remap_citations(text: str, mapping: Dict[int, int]) -> str:
    """Renumber ``[n]`` markers through *mapping* (old number → new number).

    Markers with no entry point at a source that was dropped, so they are
    removed rather than left dangling.
    """
    if not text or "[" not in text:
        return text

    def _replace(match: "re.Match") -> str:
        new = mapping.get(int(match.group(1)))
        return f"[{new}]" if new else ""

    return _rewrite_outside_code(text, _replace)
