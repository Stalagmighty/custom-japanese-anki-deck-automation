# Create Custom Japanese Vocab List and Anki Deck

A desktop tool for turning Japanese text I actually encounter — articles, app
exports, my own English word-lists — into a reviewed vocabulary table and an
importable [Anki](https://apps.ankiweb.net) deck.

It exists because the useful vocabulary is the vocabulary you met in context,
but getting from "a paragraph I struggled with" to "a properly formed flashcard
with reading, meaning and an example sentence" is tedious enough that I stopped
doing it by hand.

## What it does

The window is laid out as three steps.

```
1  Get words                    2  Review                       3  Export
───────────                     ─────────                       ─────────
Vocab list / app export ─┐                                   ┌─► Anki deck (.apkg)
English words ───────────┤      table + row editor           │   audio, highlighted
Japanese article ────────┼────► "Add details & examples" ────┼─► Google Sheet
Topic (Claude) ──────────┤        Jisho → Claude             └─► CSV
Google Sheet / CSV ──────┘
```

| Stage | Module | What happens |
| --- | --- | --- |
| Extract | `extractor.py` | Morphological analysis of raw Japanese via fugashi/unidic-lite — nouns, verbs, adjectives and na-adjectives, verbs lemmatised, compounds such as 新幹線 / 主要都市 joined, ranked by frequency and length |
| Translate | `pipeline.py` | English → Japanese with Claude, for when you know the concept but not the word. English rows read from a Sheet are translated in place |
| Look up | `jisho_lookup.py` | Jisho readings, meanings, JLPT level and an example sentence with its English translation |
| Examples | `pipeline.py` | Claude writes an example sentence (plus translation) wherever one is missing |
| Topic | `pipeline.py` | Claude suggests new words for a topic, avoiding words already in the table |
| Store | `sheets.py` | The working table in Google Sheets — editable on a phone, with a raw-text backup tab |
| Export | `anki_export.py` | A ready-to-import deck: text-to-speech of the reading, the word bolded in its example, optional English → Japanese cards, readable in light and night mode |

## Layout

```
main.py            entry point
gui.py             Tkinter front end — layout and background-task plumbing only
pipeline.py        translate / examples / topic / Jisho enrichment (no Tk)
  ├── anthropic_client.py  key resolution + the one structured-output Claude call
  ├── jisho_lookup.py      Jisho lookups + parsing pasted/exported vocab
  └── extractor.py         morphological analysis of raw Japanese
models.py          the Row type, merging, JLPT normalisation
settings.py        Google Sheet settings and preferences (~/.jp_vocab_builder.json)
sheets.py          Google Sheets read/write/backup
anki_export.py     deck construction
text_utils.py      pure string helpers
scripts/           standalone tools, not part of the app
smoke_test.py      offline checks with fake Claude/Jisho/Sheets — no network, no keys
```

The GUI uses `ttkbootstrap`'s "darkly" theme when it's installed, and a built-in dark
theme with the same colours otherwise (the activity log says why it fell back).

## Notes on a few decisions

**Readings are normalised to hiragana.** unidic gives katakana readings
regardless of the surface form, so `katakana_to_hiragana` converts by codepoint
offset. Without it, every card's reading field looks wrong to a learner.

**Verbs and adjectives are stored as lemmas, nouns as surface forms.** A card
for 食べました isn't useful; a card for 食べる is (with the dictionary-form
reading, たべる). But lemmatising nouns sometimes destroys the compound you
actually wanted to learn.

**Claude replies use structured outputs.** Every call sends a JSON schema, so
the reply is guaranteed to parse — no lenient JSON repair, no retry-on-bad-JSON.
Items carry an id, so results map back to the right row even when Claude
reorders or skips one.

**English rows are never "enriched".** Asking for example sentences for an
English term produces Japanese sentences with English words in them. Rows
without Japanese are highlighted and translated first.

**Claude calls are batched** rather than one-per-term — a 200-term list is a
handful of requests instead of 200. Jisho lookups run four at a time.

**Slow work never runs on the Tk thread.** Background tasks only post events to
a queue that the main loop drains, so the window stays responsive and Cancel
works mid-run (finished batches are kept).

**Anki deck IDs are derived from a hash of the deck name**, and note GUIDs from
term + reading, so re-importing an updated deck updates it instead of creating
duplicates. The card type is `JP Vocab v4`; notes imported with the older
`JP Vocab Basic v3` keep their old layout unless you tick *Merge notetypes* when
importing.

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

Google Sheets needs a GCP service account JSON with the Sheets API enabled.
Put it in a `secrets/` folder at the project root and the app picks it up
automatically (or choose another file in **File → Google Sheet settings…**).
Share the Sheet with the `client_email` from that file. `secrets/` and `*.json`
are gitignored. The Sheet ID (or the whole Sheet URL) and tab name are entered
once in the same dialog and remembered.

```bash
python main.py
```

To check an install without touching the network or needing an API key:

```bash
python smoke_test.py
```

## Troubleshooting

**`PermissionError … virtual_file.log` on import.** The `SSLKEYLOGFILE`
environment variable is set (some debugging and security tools set it), and
`requests`/`httpx` try to open that file. Check with `echo $env:SSLKEYLOGFILE`
in PowerShell and remove it if nothing needs it.

**`No module named 'fugashi.fugashi'` / `'jiter.jiter'`.** The virtualenv's
compiled files are damaged — common when the venv lives inside OneDrive.
Recreate it outside OneDrive (e.g. `py -3.14 -m venv C:\venvs\anki`).

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
