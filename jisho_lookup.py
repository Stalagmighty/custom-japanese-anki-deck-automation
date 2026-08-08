"""Jisho dictionary lookups and parsing of pasted/app-exported vocab text."""
from __future__ import annotations

import re

from jisho_api.sentence import Sentence
from jisho_api.word import Word

from text_utils import remove_furigana, split_meanings


def top_two_non_wiki_meanings(w_data) -> str:
    picked = []
    for sense in w_data.senses:
        if "Wikipedia definition" in (sense.parts_of_speech or []):
            continue
        if sense.english_definitions:
            picked.append(", ".join(sense.english_definitions))
        if len(picked) >= 2:
            break
    return "; ".join(picked[:2]) if picked else ""


def fetch_example_sentence(term: str) -> str:
    try:
        s_res = Sentence.request(term)
        if s_res.data:
            return remove_furigana(s_res.data[0].japanese)
    except Exception:
        pass
    return ""


def augment_row_with_jisho(term: str, reading_hint: str | None) -> list[str]:
    """
    Returns: [Term, Reading, Meaning(2 max, non-Wikipedia), Example, JLPT]
    """
    w_res = Word.request(term)
    if not w_res.data and reading_hint:
        w_res = Word.request(reading_hint)
    if not w_res.data:
        return [term, reading_hint or "", "", "", ""]

    w = w_res.data[0]
    jp0 = w.japanese[0]
    out_term = jp0.word or jp0.reading or term
    out_reading = jp0.reading or (reading_hint or "")
    meanings = top_two_non_wiki_meanings(w)
    example = fetch_example_sentence(term)
    jlpt = ", ".join(w.jlpt) if w.jlpt else ""

    return [out_term, out_reading, meanings or "", example, jlpt]


# Matches entries like:  一般的（いっぱんてき） general, common, typical
# …and also tolerates missing readings:  一般的  general, common
TERM_BLOCK_RE = re.compile(r"""
    \s*                                  # optional leading space
    (?P<term>[^\s（）()]+)                # term (until space or bracket)
    (?:\s*[（(](?P<reading>[^）)]+)[）)])? # optional reading in JP/ASCII parens
    \s+                                  # at least one space
    (?P<meaning>.+?)                     # meaning (lazy)
    (?=                                  # stop when we see the next term…
        \s+[^\s（）()]+(?:\s*[（(][^）)]+[）)])? # …optionally with reading…
        \s+                              # …and a space
      | \s*$                             # …or end of string
    )
""", re.VERBOSE | re.DOTALL)


_TERM_LINE_RE = re.compile(
    r"^(?P<term>[^\s（）()]+)\s*(?:[（(](?P<reading>[^）)]+)[）)])?\s*$"
)

def _parse_multiline(text: str):
    """Parse the app-export format: Term（reading）\\nmeaning\\n\\nTerm…"""
    rows = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        m = _TERM_LINE_RE.match(lines[0])
        if not m:
            continue
        term = m.group("term").strip()
        reading = (m.group("reading") or "").strip()
        meaning = " ".join(lines[1:])
        rows.append([term, reading, meaning, "", ""])
    return rows


def parse_blob(text: str):
    # Try the multi-line app-export format first (term line + meaning line per block)
    rows = _parse_multiline(text)
    if rows:
        return rows
    # Fall back to the original single-line inline format
    flat = re.sub(r"\s+", " ", text.strip())
    rows = []
    for m in TERM_BLOCK_RE.finditer(flat):
        term = m.group("term").strip()
        reading = (m.group("reading") or "").strip()
        meanings_raw = m.group("meaning").strip()
        meanings = ", ".join(split_meanings(meanings_raw))
        rows.append([term, reading, meanings, "", ""])
    return rows
