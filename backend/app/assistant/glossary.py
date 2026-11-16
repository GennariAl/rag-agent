"""Deterministic, glossary-based query expansion for the assistant retriever.
"""

from __future__ import annotations

import json
import re
from importlib import resources

_GLOSSARY_DATA_FILE = "glossary_data.json"


def _load_alias_pairs() -> tuple[tuple[str, str], ...]:
    """Load and validate the acronym/expansion pairs from ``glossary_data.json``.

    Fails fast at import with a clear message if an entry is malformed (missing or empty
    ``acronym``/``expanded``), rather than silently dropping terms from the expansion.
    """
    raw = json.loads(
        resources.files(__package__).joinpath(_GLOSSARY_DATA_FILE).read_text("utf-8")
    )
    pairs: list[tuple[str, str]] = []
    for index, entry in enumerate(raw):
        acronym = entry.get("acronym", "").strip()
        expanded = entry.get("expanded", "").strip()
        if not acronym or not expanded:
            raise ValueError(
                f"{_GLOSSARY_DATA_FILE}: entry {index} must have non-empty "
                f"'acronym' and 'expanded' fields, got {entry!r}"
            )
        pairs.append((acronym, expanded))
    return tuple(pairs)


# (acronym, expanded form). Expansion is bidirectional: whichever form appears in the query,
# the missing counterpart is appended.
ALIAS_PAIRS: tuple[tuple[str, str], ...] = _load_alias_pairs()


def _word_boundary_pattern(form: str) -> re.Pattern[str]:
    """Case-insensitive, word-boundary matcher for a glossary form.

    Word boundaries keep "ABS" from matching inside "absolute" even with IGNORECASE (the
    trailing "o" is a word char, so ``\\bABS\\b`` fails there), while still catching a
    lowercase user spelling like "abs".
    """
    return re.compile(rf"\b{re.escape(form)}\b", re.IGNORECASE)


# Pre-compile once: for each pair, the matcher for the found form and the counterpart to
# append (with its own matcher, to skip appending when it is already present).
_RULES: tuple[tuple[re.Pattern[str], str, re.Pattern[str]], ...] = tuple(
    (_word_boundary_pattern(found), counterpart, _word_boundary_pattern(counterpart))
    for acronym, expanded in ALIAS_PAIRS
    for found, counterpart in ((acronym, expanded), (expanded, acronym))
)


def expand_query(text: str) -> str:
    """Enrich a query with glossary counterparts of any term it contains.

    For every alias form found (word-boundary, case-insensitive), append ``- <counterpart>``
    right after it, keeping the original term. Skips a counterpart that is already present to
    avoid duplicates. Example::

        "What's the ABS of the loan XYZ?"
        -> "What's the ABS - asset-backed security of the loan XYZ?"
    """
    for found_pattern, counterpart, counterpart_pattern in _RULES:
        if counterpart_pattern.search(text):
            continue
        match = found_pattern.search(text)
        if match is None:
            continue
        text = f"{text[: match.end()]} - {counterpart}{text[match.end() :]}"
    return text
