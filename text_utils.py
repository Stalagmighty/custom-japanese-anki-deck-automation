"""Small pure-text helpers shared across the pipeline."""
from __future__ import annotations

import re


JP_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

def pick_jp_term(term: str, reading: str) -> str:
    term = (term or "").strip()
    reading = (reading or "").strip()
    # If the term has no JP chars but the reading does, use the reading
    if not JP_RE.search(term) and JP_RE.search(reading):
        return reading
    return term


FURIGANA_RE = re.compile(r"\([^)]*\)")
def remove_furigana(text: str) -> str:
    return FURIGANA_RE.sub("", text)


def split_meanings(s: str):
    """Split on commas not inside ASCII parentheses; keep semicolons as-is."""
    parts, current, depth = [], [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')' and depth > 0:
            depth -= 1
        if ch == ',' and depth == 0:
            part = ''.join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    last = ''.join(current).strip()
    if last:
        parts.append(last)
    return parts
