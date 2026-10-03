"""Turning words into finished rows: Claude calls, Jisho lookups and batching.

Nothing here touches Tk. Long-running functions take a `Task`, report progress
through it and stop early (returning what they have so far) when it is cancelled.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Callable

import jisho_lookup
from anthropic_client import generate_json
from extractor import build_rows_from_text
from models import Row, merge_rows
from text_utils import JP_RE

# Example-source modes for enrich()
JISHO_THEN_CLAUDE = "jisho_then_claude"
JISHO_ONLY = "jisho"
CLAUDE_ONLY = "claude"


class Task:
    """Progress, log and cancel handle passed from the GUI into long-running work."""

    def __init__(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ):
        self._cancel = threading.Event()
        self._on_progress = on_progress or (lambda done, total: None)
        self._on_log = on_log or (lambda msg: None)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def progress(self, done: int, total: int) -> None:
        self._on_progress(done, total)

    def log(self, msg: str) -> None:
        self._on_log(msg)


# ---------- schemas and prompts ----------

def _items_schema(props: dict) -> dict:
    item = {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item}},
        "required": ["items"],
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_ENTRY = {
    "term": _STR,
    "reading": _STR,
    "meaning": _STR,
    "example": _STR,
    "example_en": _STR,
    "jlpt": {"type": "string", "enum": ["N5", "N4", "N3", "N2", "N1", ""]},
}
TRANSLATE_SCHEMA = _items_schema({"id": {"type": "integer"}, **_ENTRY})
EXAMPLES_SCHEMA = _items_schema({"id": {"type": "integer"}, "example": _STR, "example_en": _STR})
TOPIC_SCHEMA = _items_schema(_ENTRY)

_FIELD_RULES = (
    "term: the Japanese headword (kanji or kana, never English). "
    "reading: its reading in hiragana (katakana for loanwords). "
    "meaning: concise English gloss(es). "
    "jlpt: the word's JLPT level, or empty if unknown. "
)
_EXAMPLE_RULES = (
    "example: ONE natural Japanese sentence of about 60-110 characters that uses the word, "
    "in a context-rich register (news, academic or professional). "
    "example_en: a natural English translation of that sentence. "
)

TRANSLATE_SYSTEM = (
    "You are a careful bilingual lexicographer. For each English seed term, give the single "
    "most natural Japanese vocabulary word for it. Prefer the common native or Sino-Japanese "
    "word over a katakana loanword unless the loanword is standard. "
    + _FIELD_RULES
    + "In meaning, include the seed itself if it isn't already one of the glosses. "
    + _EXAMPLE_RULES
    + "Return exactly one item per seed, carrying the seed's id."
)
EXAMPLES_SYSTEM = (
    "You write example sentences for Japanese vocabulary flashcards. For each word: "
    + _EXAMPLE_RULES
    + "Use the word itself, inflected only where grammar requires. "
    "Return exactly one item per word, carrying the word's id."
)
TOPIC_SYSTEM = (
    "You build Japanese vocabulary lists for an advanced learner. Give `count` distinct words "
    "that are genuinely useful for the topic, none of which appear in `avoid`. Mix parts of "
    "speech and prefer words a learner would meet in real material on the topic. "
    + _FIELD_RULES
    + _EXAMPLE_RULES
)


def _entry_row(item: dict) -> Row:
    return Row(**{k: item.get(k, "") for k in _ENTRY})


def _has_japanese(row: Row) -> bool:
    return bool(row.term) and bool(JP_RE.search(row.term))


# ---------- Claude ----------

def translate_english(terms: list[str], task: Task, *, client=None, batch_size: int = 25) -> list[Row | None]:
    """One Japanese row per English term, aligned with `terms` (None where it failed or was cancelled)."""
    results: list[Row | None] = [None] * len(terms)
    for start in range(0, len(terms), batch_size):
        if task.cancelled:
            break
        seeds = [{"id": i, "seed": terms[i]} for i in range(start, min(start + batch_size, len(terms)))]
        data = generate_json(TRANSLATE_SYSTEM, {"seeds": seeds}, TRANSLATE_SCHEMA, client=client)
        for item in data["items"]:
            i = item.get("id")
            if isinstance(i, int) and start <= i < start + len(seeds):
                row = _entry_row(item)
                if _has_japanese(row):
                    results[i] = row
        task.progress(start + len(seeds), len(terms))
    return results


def translate_english_rows(rows: list[Row], task: Task, *, client=None) -> tuple[list[Row], int, int]:
    """Replace every English row in the table with its Japanese translation, in place.

    A translation that matches a word already in the table is merged into it
    rather than duplicated. Returns (rows, translated, failed).
    """
    idxs = [i for i, r in enumerate(rows) if r.is_english]
    found = translate_english([rows[i].term for i in idxs], task, client=client)
    rows = list(rows)
    translated = 0
    for i, row in zip(idxs, found):
        if row is not None:
            rows[i] = row
            translated += 1
    rows, _, _ = merge_rows([], rows)
    return rows, translated, len(idxs) - translated


def generate_examples(rows: list[Row], idxs: list[int], task: Task, *, client=None,
                      batch_size: int = 20) -> dict[int, tuple[str, str]]:
    """{row index: (example, example_en)} for the requested rows."""
    out: dict[int, tuple[str, str]] = {}
    for start in range(0, len(idxs), batch_size):
        if task.cancelled:
            break
        batch = idxs[start:start + batch_size]
        words = [
            {"id": i, "term": rows[i].term, "reading": rows[i].reading, "meaning": rows[i].meaning}
            for i in batch
        ]
        data = generate_json(EXAMPLES_SYSTEM, {"words": words}, EXAMPLES_SCHEMA, client=client)
        for item in data["items"]:
            i = item.get("id")
            example = (item.get("example") or "").strip()
            if i in batch and example:
                out[i] = (example, (item.get("example_en") or "").strip())
        task.progress(start + len(batch), len(idxs))
    return out


def generate_topic_rows(topic: str, count: int, avoid_terms: set[str], task: Task, *,
                        client=None, max_rounds: int = 6) -> list[Row]:
    """Up to `count` new rows for `topic`, skipping terms in `avoid_terms`.

    Asks again for the shortfall when Claude repeats words, and gives up after
    two rounds that add nothing.
    """
    rows: list[Row] = []
    seen = set(avoid_terms)
    stale = 0
    for _ in range(max_rounds):
        need = count - len(rows)
        if need <= 0 or task.cancelled:
            break
        payload = {"topic": topic, "count": min(need + 5, 40), "avoid": sorted(seen)[:300]}
        data = generate_json(TOPIC_SYSTEM, payload, TOPIC_SCHEMA, client=client)
        gained = 0
        for item in data["items"]:
            row = _entry_row(item)
            if not _has_japanese(row) or row.term in seen:
                continue
            seen.add(row.term)
            rows.append(row)
            gained += 1
            if len(rows) >= count:
                break
        task.progress(len(rows), count)
        task.log(f"Topic '{topic}': {len(rows)}/{count} words")
        stale = stale + 1 if gained == 0 else 0
        if stale >= 2:
            break
    return rows


# ---------- Jisho + Claude enrichment ----------

def _combine(old: Row, found: Row, only_fill_empty: bool) -> Row:
    """Merge a Jisho result into a row. The example and its translation move as a pair."""
    def pick(o: str, f: str) -> str:
        return (o or f) if only_fill_empty else (f or o)

    keep_old_example = bool(old.example) if only_fill_empty else not found.example
    src = old if keep_old_example else found
    return Row(
        term=old.term,
        reading=pick(old.reading, found.reading),
        meaning=pick(old.meaning, found.meaning),
        example=src.example,
        jlpt=pick(old.jlpt, found.jlpt),
        example_en=src.example_en,
    )


def enrich(rows: list[Row], mode: str, only_fill_empty: bool, task: Task, *,
           client=None, jisho_workers: int = 4) -> tuple[list[Row], str]:
    """Fill readings, meanings, JLPT and example sentences.

    English rows are skipped: they need translating first, otherwise Claude
    writes Japanese sentences around English words. Returns (rows, summary).
    """
    rows = list(rows)
    targets = [i for i, r in enumerate(rows) if _has_japanese(r)]
    english = sum(1 for r in rows if r.is_english)
    jisho_updates = claude_updates = 0

    if mode in (JISHO_THEN_CLAUDE, JISHO_ONLY) and targets:
        task.log(f"Jisho: looking up {len(targets)} words…")
        with ThreadPoolExecutor(max_workers=jisho_workers) as pool:
            futures = {pool.submit(jisho_lookup.lookup, rows[i].term, rows[i].reading): i for i in targets}
            for done, fut in enumerate(as_completed(futures), 1):
                if task.cancelled:
                    pool.shutdown(cancel_futures=True)
                    break
                i = futures[fut]
                try:
                    new = _combine(rows[i], fut.result(), only_fill_empty)
                except Exception as e:
                    task.log(f"Jisho failed for {rows[i].term}: {e}")
                    continue
                if new != rows[i]:
                    rows[i] = new
                    jisho_updates += 1
                task.progress(done, len(targets))

    if mode in (JISHO_THEN_CLAUDE, CLAUDE_ONLY) and not task.cancelled:
        rewrite_all = mode == CLAUDE_ONLY and not only_fill_empty
        need = [i for i in targets if rewrite_all or not rows[i].example]
        if need:
            task.log(f"Claude: writing {len(need)} example sentences…")
            for i, (example, example_en) in generate_examples(rows, need, task, client=client).items():
                rows[i] = replace(rows[i], example=example, example_en=example_en)
                claude_updates += 1

    parts = []
    if mode != CLAUDE_ONLY:
        parts.append(f"Jisho updated {jisho_updates}")
    if mode != JISHO_ONLY:
        parts.append(f"Claude wrote {claude_updates} examples")
    if english:
        parts.append(f"skipped {english} English rows (translate them first)")
    if task.cancelled:
        parts.append("stopped early")
    return rows, "; ".join(parts) + "."


def extract_from_text(text: str, max_words: int) -> list[Row]:
    """Rank vocabulary in Japanese text (no network)."""
    found = build_rows_from_text(text, top_k=max_words, min_freq=1, allow_phrases=True, max_ngram_len=3)
    return [Row.from_list(r) for r in found]
