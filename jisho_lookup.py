"""Jisho dictionary lookups and parsing of pasted/app-exported vocab text."""
from __future__ import annotations

import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from models import Row
from text_utils import JP_RE, remove_furigana, split_english_terms, split_meanings

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


# A Japanese headword: any run of non-space characters containing kana or kanji,
# optionally followed by its reading in full-width or ASCII brackets.
_JP_CHAR = r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff々〆ヵヶ]"
_ENTRY_RE = re.compile(
    rf"(?P<term>[^\s（）()]*{_JP_CHAR}[^\s（）()]*)\s*(?:[（(](?P<reading>[^）)]+)[）)])?"
)
_TERM_LINE_RE = re.compile(rf"^{_ENTRY_RE.pattern}\s*$")


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


def _parse_inline(text: str) -> list[Row]:
    """Parse the Jisho app's one-line export: 語（ご） word, language 岐路（きろ） forked road, crossroads

    Each Japanese headword starts an entry; everything up to the next headword is
    its meaning, so meanings may contain spaces and commas.
    """
    flat = re.sub(r"\s+", " ", text.strip())
    matches = list(_ENTRY_RE.finditer(flat))
    rows = []
    for m, nxt in zip(matches, matches[1:] + [None]):
        meaning = flat[m.end(): nxt.start() if nxt else len(flat)].strip()
        rows.append(Row(
            term=m.group("term"),
            reading=(m.group("reading") or "").strip(),
            meaning=", ".join(split_meanings(meaning)),
        ))
    return rows


def parse_blob(text: str) -> list[Row]:
    """Rows from pasted text in any of the supported shapes.

    - An English word list (no Japanese anywhere): one English row per term,
      ready to translate.
    - The multi-line app export: 語（ご）\\nmeaning\\n\\n…
    - The Jisho app's one-line export: 語（ご） meaning 語（ご） meaning …
    """
    if not JP_RE.search(text):
        return [Row(term=t) for t in split_english_terms(text)]
    return _parse_multiline(text) or _parse_inline(text)
