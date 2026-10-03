"""Jisho dictionary lookups and parsing of pasted/app-exported vocab text."""
from __future__ import annotations

import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from text_utils import remove_furigana, split_meanings

JISHO_WORDS_URL = "https://jisho.org/api/v1/search/words?keyword="
JISHO_SEARCH_URL = "https://jisho.org/search/"
TIMEOUT = 15


def search_words(keyword: str) -> list[dict]:
    """Raw entries from Jisho's word-search JSON API ([] when nothing matches)."""
    r = requests.get(JISHO_WORDS_URL + quote(keyword), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("data") or []


def top_two_non_wiki_meanings(entry: dict) -> str:
    picked = []
    for sense in entry.get("senses") or []:
        if "Wikipedia definition" in (sense.get("parts_of_speech") or []):
            continue
        if sense.get("english_definitions"):
            picked.append(", ".join(sense["english_definitions"]))
        if len(picked) >= 2:
            break
    return "; ".join(picked[:2]) if picked else ""


def fetch_example_sentence(term: str) -> str:
    """First example sentence for `term` from Jisho's sentence search, furigana stripped.

    Jisho has no sentence API, so this reads the search results page.
    """
    try:
        r = requests.get(JISHO_SEARCH_URL + quote(term + " #sentences"), timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        block = soup.find("div", {"class": "sentence_content"})
        if block is None:
            return ""
        parts = []
        for li in block.find_all("li"):
            unlinked = li.find("span", {"class": "unlinked"})
            if unlinked:
                parts.append(unlinked.text)
        return remove_furigana("".join(parts))
    except Exception:
        return ""


def augment_row_with_jisho(term: str, reading_hint: str | None) -> list[str]:
    """
    Returns: [Term, Reading, Meaning(2 max, non-Wikipedia), Example, JLPT]
    """
    entries = search_words(term)
    if not entries and reading_hint:
        entries = search_words(reading_hint)
    if not entries:
        return [term, reading_hint or "", "", "", ""]

    w = entries[0]
    jp0 = (w.get("japanese") or [{}])[0]
    out_term = jp0.get("word") or jp0.get("reading") or term
    out_reading = jp0.get("reading") or (reading_hint or "")
    meanings = top_two_non_wiki_meanings(w)
    example = fetch_example_sentence(term)
    jlpt = ", ".join(w.get("jlpt") or [])

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
