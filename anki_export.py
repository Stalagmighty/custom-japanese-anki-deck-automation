"""Anki deck construction.

Deck and model IDs are derived from a hash of their name so that re-importing
an updated deck updates the existing one instead of creating a duplicate.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import genanki


def stable_id(name: str) -> int:
    """Deterministic 32-bit int from a name (for deck/model IDs)."""
    return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)


def make_anki_deck(rows: list, deck_name: str):
    """
    Build a genanki.Deck from rows = [[Term, Reading, Meaning, Example, JLPT], ...].
    Uses a model with 6 fields: Term, Reading, Meaning, Example, JLPT, Date.
    """
    deck_id = stable_id(deck_name)
    model_name = "JP Vocab Basic v3"
    model_id = stable_id(model_name)

    model = genanki.Model(
        model_id=model_id,
        name=model_name,
        fields=[
            {"name": "Term"},
            {"name": "Reading"},
            {"name": "Meaning"},
            {"name": "Example"},
            {"name": "JLPT"},
            {"name": "Date"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": """
<div style="display:flex;align-items:center;justify-content:center;min-height:65vh;font-size:60px;font-weight:700;">
  {{Term}}
</div>
                """,
                "afmt": """
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:65vh;padding:10px;color:#fff !important;">
  <div style="font-size:60px;font-weight:700;">{{Term}}</div>
  <div style="font-size:34px;margin-top:10px;">{{Reading}}</div>
  <hr style="width:100%;border:none;border-top:1px solid #aaa;margin:16px 0;">
  <div style="font-size:28px;line-height:1.4;text-align:center;max-width:900px;">
    {{Meaning}}
  </div>
  {{#Example}}
  <div style="margin-top:16px;font-size:24px;line-height:1.4;text-align:center;max-width:900px;">
    <b>Example:</b> {{Example}}
  </div>
  {{/Example}}
  {{#JLPT}}
  <div style="margin-top:12px;font-size:18px;color:#ddd;">
    JLPT: {{JLPT}}
  </div>
  {{/JLPT}}
  <div style="font-size:14px;color:#eaeaea;margin-top:16px;">
    Added: {{Date}}
  </div>
</div>
"""
            }
        ],
    )

    deck = genanki.Deck(deck_id=deck_id, name=deck_name)
    today = datetime.today().strftime("%Y-%m-%d")

    for row in rows:
        term, reading, meaning, example, jlpt = (row + ["", "", ""])[:5]
        guid = genanki.guid_for(f"v3|{term}|{reading}")  # version tag to avoid collision
        note = genanki.Note(
            model=model,
            fields=[term, reading, meaning, example, jlpt, today],
            guid=guid,
        )
        deck.add_note(note)

    return deck
