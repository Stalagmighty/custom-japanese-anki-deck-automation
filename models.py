"""The vocabulary row and the helpers that keep rows tidy."""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Iterable, Sequence

from text_utils import JP_RE

# Storage order for CSV and Google Sheets. JLPT stays in column 5 so sheets and
# CSVs written by earlier versions (5 columns) still read back correctly.
STORAGE_HEADERS = ["Term", "Reading", "Meaning", "Example", "JLPT", "Example (EN)"]


@dataclass
class Row:
    term: str = ""
    reading: str = ""
    meaning: str = ""
    example: str = ""
    jlpt: str = ""
    example_en: str = ""

    def __post_init__(self):
        for f in fields(self):
            setattr(self, f.name, (getattr(self, f.name) or "").strip())
        self.jlpt = normalize_jlpt(self.jlpt)

    @classmethod
    def from_list(cls, values: Sequence[str]) -> "Row":
        """Build from a storage-order list; short lists are padded."""
        padded = [str(v) if v is not None else "" for v in values][:6]
        padded += [""] * (6 - len(padded))
        return cls(*padded)

    def to_list(self) -> list[str]:
        return [self.term, self.reading, self.meaning, self.example, self.jlpt, self.example_en]

    def key(self) -> tuple[str, str]:
        return (self.term, self.reading)

    @property
    def is_empty(self) -> bool:
        return not any(self.to_list())

    @property
    def is_english(self) -> bool:
        """True when the term has no Japanese in it (e.g. a row read from an English list)."""
        return bool(self.term) and not JP_RE.search(self.term)


_JLPT_RE = re.compile(r"N\s*-?\s*([1-5])")


def normalize_jlpt(value: str) -> str:
    """'jlpt-n1', 'JLPT N2', 'n-3', 'Ｎ４' → 'N1'…'N4'. Unknown → ''. Several levels → the first."""
    s = (value or "").upper().replace("Ｎ", "N")
    s = s.translate(str.maketrans("１２３４５", "12345"))
    m = _JLPT_RE.search(s)
    return f"N{m.group(1)}" if m else ""


def merge_rows(existing: Iterable[Row], incoming: Iterable[Row]) -> tuple[list[Row], int, int]:
    """Append incoming rows; rows already present (same term + reading) only get blanks filled.

    Returns (merged, added, updated).
    """
    merged = list(existing)
    index = {r.key(): i for i, r in enumerate(merged) if r.term}
    added = updated = 0
    for new in incoming:
        if not new.term:
            continue
        i = index.get(new.key())
        if i is None:
            index[new.key()] = len(merged)
            merged.append(new)
            added += 1
            continue
        old = merged[i]
        filled = Row(*[o or n for o, n in zip(old.to_list(), new.to_list())])
        if filled != old:
            merged[i] = filled
            updated += 1
    return merged, added, updated
