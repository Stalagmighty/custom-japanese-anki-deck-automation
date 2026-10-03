"""Jisho dictionary lookups and parsing of pasted/app-exported vocab text."""
from __future__ import annotations

import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from models import Row
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


def fetch_example_sentence(term: str) -> tuple[str, str]:
    """(Japanese, English) for the first example sentence Jisho has for `term`.

    Jisho has no sentence API, so this reads the search results page.
    Returns ("", "") when there is none or the request fails.
    """
    try:
        r = requests.get(JISHO_SEARCH_URL + quote(term + " #sentences"), timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        block = soup.find("div", {"class": "sentence_content"})
        if block is None:
            return "", ""
        parts = []
        for li in block.find_all("li"):
            unlinked = li.find("span", {"class": "unlinked"})
            if unlinked:
                parts.append(unlinked.text)
        english = block.find("span", {"class": "english"})
        return remove_furigana("".join(parts)), (english.text.strip() if english else "")
    except Exception:
        return "", ""


def _best_entry(entries: list[dict], term: str, reading: str) -> dict:
    """Prefer an entry whose headword (or kana reading) is exactly the term."""
    for e in entries:
        for jp in e.get("japanese") or []:
            if term and term in (jp.get("word"), jp.get("reading")):
                if not reading or jp.get("reading") in (None, reading):
                    return e
    return entries[0]


def lookup(term: str, reading: str = "") -> Row:
    """Jisho's view of a Japanese term: reading, meanings, an example sentence and JLPT.

    The term itself is never replaced; Jisho only supplies the other fields.
    Returns a Row with just the term (and reading hint) when Jisho has nothing.
    """
    entries = search_words(term) or (search_words(reading) if reading else [])
    if not entries:
        return Row(term=term, reading=reading)
    entry = _best_entry(entries, term, reading)
    jp = next(
        (j for j in entry.get("japanese") or [] if term in (j.get("word"), j.get("reading"))),
        (entry.get("japanese") or [{}])[0],
    )
    example, example_en = fetch_example_sentence(term)
    return Row(
        term=term,
        reading=jp.get("reading") or reading,
        meaning=top_two_non_wiki_meanings(entry),
        example=example,
        jlpt=", ".join(entry.get("jlpt") or []),
        example_en=example_en,
    )


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

def _parse_multiline(text: str) -> list[Row]:
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
        rows.append(Row(term=term, reading=reading, meaning=meaning))
    return rows


def parse_blob(text: str) -> list[Row]:
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
        rows.append(Row(term=term, reading=reading, meaning=meanings))
    return rows
