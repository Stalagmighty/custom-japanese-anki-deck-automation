"""Anki deck construction.

Deck and model IDs are derived from a hash of their name so that re-importing
an updated deck updates the existing one instead of creating a duplicate.
"""
from __future__ import annotations

import hashlib
import html
from datetime import datetime

import genanki

from models import Row

MODEL_NAME = "JP Vocab v4"

# Colours are left to Anki so cards read correctly in both light and night mode.
CSS = """
.card { font-family: "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif;
        font-size: 22px; text-align: center; line-height: 1.5; }
.front { display: flex; flex-direction: column; justify-content: center; align-items: center;
         min-height: 70vh; }
/* shrinks on narrow phones so words up to ~8 characters (オープンソース) stay on one line */
.term { font-size: min(60px, 11vw); font-weight: 700; }
.reading { font-size: 32px; margin-top: 6px; }
.meaning { font-size: 26px; max-width: 900px; margin: 0 auto; }
.example { font-size: 24px; margin: 18px auto 0; max-width: 900px; }
.example-en, .meta { opacity: 0.7; }
.example-en { font-size: 18px; margin: 6px auto 0; max-width: 900px; }
.meta { font-size: 14px; margin-top: 16px; }
hr { margin: 16px 0; }
"""

_DETAILS = """
<div class="reading">{{Reading}}</div>
{{#Reading}}{{tts ja_JP:Reading}}{{/Reading}}{{^Reading}}{{tts ja_JP:Term}}{{/Reading}}
<hr>
<div class="meaning">{{Meaning}}</div>
{{#Example}}<div class="example">{{Example}}</div>{{/Example}}
{{#ExampleEN}}<div class="example-en">{{ExampleEN}}</div>{{/ExampleEN}}
<div class="meta">{{#JLPT}}JLPT {{JLPT}} · {{/JLPT}}Added {{Date}}</div>
"""

RECOGNITION = {
    "name": "Japanese → English",
    "qfmt": '<div class="front"><div class="term">{{Term}}</div></div>',
    "afmt": '<div class="term">{{Term}}</div>' + _DETAILS,
}
# Only generated for notes whose Reverse field is filled in (Anki skips cards
# whose front renders empty).
PRODUCTION = {
    "name": "English → Japanese",
    "qfmt": '{{#Reverse}}<div class="front"><div class="meaning">{{Meaning}}</div></div>{{/Reverse}}',
    "afmt": '<div class="meaning">{{Meaning}}</div><hr><div class="term">{{Term}}</div>' + _DETAILS,
}

FIELDS = ["Term", "Reading", "Meaning", "Example", "ExampleEN", "JLPT", "Date", "Reverse"]


def stable_id(name: str) -> int:
    """Deterministic 32-bit int from a name (for deck/model IDs)."""
    return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)


def highlight_term(example: str, term: str) -> str:
    """HTML-escape the sentence and bold the first occurrence of the term."""
    ex, t = html.escape(example), html.escape(term)
    return ex.replace(t, f"<b>{t}</b>", 1) if t else ex


def make_anki_deck(rows: list[Row], deck_name: str, *, reverse_cards: bool = False) -> genanki.Deck:
    model = genanki.Model(
        model_id=stable_id(MODEL_NAME),
        name=MODEL_NAME,
        fields=[{"name": f} for f in FIELDS],
        templates=[RECOGNITION, PRODUCTION],
        css=CSS,
    )
    deck = genanki.Deck(deck_id=stable_id(deck_name), name=deck_name)
    today = datetime.today().strftime("%Y-%m-%d")

    for row in rows:
        if not row.term:
            continue
        note = genanki.Note(
            model=model,
            fields=[
                html.escape(row.term),
                html.escape(row.reading),
                html.escape(row.meaning),
                highlight_term(row.example, row.term),
                html.escape(row.example_en),
                row.jlpt,
                today,
                "y" if reverse_cards else "",
            ],
            # Same GUID scheme as earlier versions, so re-imports update existing notes.
            guid=genanki.guid_for(f"v3|{row.term}|{row.reading}"),
        )
        deck.add_note(note)
    return deck
