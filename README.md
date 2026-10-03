# Create Custom Japanese Vocab List and Anki Deck

A desktop tool for turning Japanese text I actually encounter — articles, app
exports, my own English word-lists — into a reviewed vocabulary table and an
importable [Anki](https://apps.ankiweb.net) deck.

It exists because the useful vocabulary is the vocabulary you met in context,
but getting from "a paragraph I struggled with" to "a properly formed flashcard
with reading, meaning and an example sentence" is tedious enough that I stopped
doing it by hand.

## What it does

Three ways in, one pipeline, two ways out.

```
Input                          Enrich                        Output
─────                          ──────                        ──────
Raw Japanese text ─┐
                   │      ┌─► Jisho API                 ┌─► Google Sheet
App export blob ───┼──────┤   (readings, meanings,      │   (working table,
                   │      │    example sentences)       │    reviewable)
English terms ─────┘      │                             │
                          └─► Claude (Anthropic)        └─► Anki deck (.apkg)
                              (example sentences,           via genanki
                               topic tagging)
```

| Stage | Module | What happens |
| --- | --- | --- |
| Extract | `extractor.py` | Morphological analysis of raw Japanese via fugashi/unidic-lite — filters to nouns, verbs and adjectives, lemmatises verbs, ranks by frequency and length, and pulls out noun compounds and n-gram phrases |
| Translate | `From_English_Translate.py` | Batch English → Japanese, for when I know the concept but not the word |
| Look up | `jisho_lookup.py` | Dictionary readings, meanings and example sentences |
| Tag | `topic_service.py` | Groups terms into topics so a deck can be studied thematically |
| Store | Google Sheets API | The working table — editable on a phone, with a raw-text backup tab |
| Export | `genanki` | A ready-to-import deck |

## Layout

```
main.py            entry point
gui.py             Tkinter front end — presentation and orchestration only
  ├── anthropic_client.py key resolution, Claude call helper, JSON extraction
  ├── enrichment.py      batched Claude example-sentence generation
  ├── jisho_lookup.py    dictionary lookups + parsing pasted/exported vocab
  ├── text_utils.py      pure string helpers
  ├── sheets.py          Google Sheets read/write/backup
  ├── anki_export.py     deck construction
  ├── extractor.py       morphological analysis of raw Japanese
  ├── topic_service.py   topic tagging
  └── utils.py           row merging
scripts/           standalone tools, not part of the pipeline
smoke_test.py      offline checks — no network, no keys, no GUI
```

The GUI uses optional `ttkbootstrap` / `sv_ttk` theming if either is installed,
and falls back to stock Tkinter otherwise.

## Notes on a few decisions

**Readings are normalised to hiragana.** unidic returns katakana readings
regardless of the surface form, so `katakana_to_hiragana` converts by codepoint
offset. Without it, every card's reading field looks wrong to a learner.

**Verbs and adjectives are stored as lemmas, nouns as surface forms.** A card
for 食べました isn't useful; a card for 食べる is. But lemmatising nouns
sometimes destroys the compound you actually wanted to learn.

**LLM JSON output is parsed leniently.** `_lenient_json_loads` copes with code
fences, smart quotes, single quotes, unquoted keys and trailing commas. Asking a
model for JSON and getting *nearly* JSON was the single most common failure mode
while building this, and strict `json.loads` made the app feel broken when the
data was actually fine.

**Claude calls are batched** rather than one-per-term — a 200-term list is a
handful of requests instead of 200.

**Anki deck IDs are derived from a hash of the deck name**, so re-importing an
updated deck updates the existing one instead of creating a duplicate.

## Setup

Tested on Python 3.12–3.14 (Windows wheels exist for every compiled dependency).

```bash
git clone https://github.com/Stalagmighty/Create_Custom_Japanese_Vocab_List_and_Anki_Deck.git
cd Create_Custom_Japanese_Vocab_List_and_Anki_Deck
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Credentials

The Anthropic key (`sk-ant-...`) is resolved in this order — environment variable first:

1. `ANTHROPIC_API_KEY` in the environment
2. `ANTHROPIC_API_KEY=...` in a `.env` file at the project root
3. `ANTHROPIC_API_KEY.txt` at the project root

Setting the environment variable is preferred. All three paths are gitignored,
and no key should ever be committed.

Google Sheets export additionally needs a GCP service account JSON with the
Sheets API enabled. Put it in a `secrets/` folder at the project root and the
app picks it up automatically (or choose another file with **Browse…**).
`secrets/` and `*.json` are gitignored.

```bash
python main.py
```

To check an install without touching the network or needing an API key:

```bash
python smoke_test.py
```

## Known gaps

`remove_furigana` strips ASCII `(...)` only — full-width Japanese `（...）` are
left intact. The app-export parser handles both bracket styles, so a reading in
full-width brackets can survive into a card field. Asserted in `smoke_test.py`
as current behaviour rather than silently changed, since fixing it would alter
existing rows.

## Status

A personal project, not a product.

## Built with

Python · Tkinter · fugashi/unidic-lite · Jisho API · Anthropic API (Claude Sonnet 5.5) · Google Sheets API · genanki
