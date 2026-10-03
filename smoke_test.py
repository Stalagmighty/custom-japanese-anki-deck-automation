"""Offline smoke test — no network, no API keys, no GUI window.

Claude, Jisho and Google Sheets are replaced with fakes, so this checks the
app's own logic end to end. Run with:  python smoke_test.py
"""
from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace
from unittest import mock

# Windows consoles default to cp1252, which cannot print Japanese.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    "text_utils",
    "models",
    "settings",
    "anthropic_client",
    "jisho_lookup",
    "extractor",
    "pipeline",
    "sheets",
    "anki_export",
    "gui",
    "main",
]

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(label)


print("imports:")
for name in MODULES:
    try:
        importlib.import_module(name)
        print(f"  ok   {name}")
    except Exception as exc:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
        failures.append(name)

if failures:
    print(f"\n{len(failures)} import failure(s) — stopping.")
    sys.exit(1)

import jisho_lookup
import pipeline
import sheets
from anki_export import highlight_term, make_anki_deck, stable_id
from jisho_lookup import parse_blob
from models import Row, merge_rows, normalize_jlpt
from text_utils import remove_furigana, split_meanings


# ---------- fakes ----------

class FakeClaude:
    """Answers like Claude would, shaped by which prompt it receives."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        payload = json.loads(kw["messages"][0]["content"])
        if "seeds" in payload:
            words = {"cat": ("猫", "ねこ"), "dog": ("犬", "いぬ")}
            items = [
                {"id": s["id"], "term": words[s["seed"]][0], "reading": words[s["seed"]][1],
                 "meaning": s["seed"], "example": f"{words[s['seed']][0]}が好きです。",
                 "example_en": f"I like {s['seed']}s.", "jlpt": "N5"}
                for s in payload["seeds"] if s["seed"] in words
            ]
        elif "words" in payload:
            items = [{"id": w["id"], "example": f"{w['term']}を使った例文。", "example_en": "An example."}
                     for w in payload["words"]]
        else:  # topic
            pool = [("空港", "くうこう"), ("搭乗", "とうじょう"), ("空港", "くうこう"), ("荷物", "にもつ")]
            items = [{"term": t, "reading": r, "meaning": "m", "example": f"{t}の例。",
                      "example_en": "e", "jlpt": ""} for t, r in pool if t not in payload["avoid"]]
        text = json.dumps({"items": items}, ensure_ascii=False)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


JISHO = {
    "地政学": {"data": [{"jlpt": ["jlpt-n1"], "japanese": [{"word": "地政学", "reading": "ちせいがく"}],
                       "senses": [{"english_definitions": ["geopolitics"], "parts_of_speech": ["Noun"]},
                                  {"english_definitions": ["Geopolitics"],
                                   "parts_of_speech": ["Wikipedia definition"]}]}]},
}
SENTENCE_HTML = """<div class="sentence_content"><ul>
<li><span class="furigana">ちせいがく</span><span class="unlinked">地政学</span></li>
<li><span class="unlinked">は重要だ。</span></li></ul>
<span class="english">Geopolitics is important.</span></div>""".encode("utf-8")


def fake_get(url, timeout):
    resp = SimpleNamespace(raise_for_status=lambda: None)
    if "/api/v1/" in url:
        term = next((t for t in JISHO if jisho_lookup.quote(t) in url), None)
        resp.json = lambda: JISHO.get(term, {"data": []})
    else:
        resp.content = SENTENCE_HTML if jisho_lookup.quote("地政学") in url else b"<html></html>"
    return resp


# ---------- checks ----------

print("\ntext_utils:")
check("remove_furigana strips ASCII parens", remove_furigana("word(reading)"), "word")
# NOTE: current behaviour — FURIGANA_RE matches ASCII "()" only, so full-width
# Japanese "（）" are left intact. See README "Known gaps" before changing it.
check("remove_furigana leaves full-width parens (current behaviour)",
      remove_furigana("一般的（いっぱんてき）"), "一般的（いっぱんてき）")
check("split_meanings respects nested parens",
      split_meanings("general, common (and typical, broadly)"), ["general", "common (and typical, broadly)"])

print("\nmodels:")
check("normalize_jlpt handles Jisho tags", normalize_jlpt("jlpt-n1, jlpt-n2"), "N1")
check("normalize_jlpt handles full width", normalize_jlpt("Ｎ２"), "N2")
check("normalize_jlpt drops junk", normalize_jlpt("common"), "")
check("5-column lists keep JLPT in column 5", Row.from_list(["語", "ご", "word", "例", "N3"]).jlpt, "N3")
check("is_english", (Row("Disruption").is_english, Row("混乱").is_english), (True, False))
merged, added, updated = merge_rows([Row("猫", "ねこ")], [Row("猫", "ねこ", "cat"), Row("犬", "いぬ")])
check("merge fills blanks and appends", ([r.meaning for r in merged], added, updated), (["cat", ""], 1, 1))

print("\njisho_lookup:")
multiline = "一般的（いっぱんてき）\ngeneral, common\n\n地政学（ちせいがく）\ngeopolitics"
rows = parse_blob(multiline)
check("parse_blob multiline: count", len(rows), 2)
check("parse_blob multiline: first row", (rows[0].term, rows[0].reading, rows[0].meaning),
      ("一般的", "いっぱんてき", "general, common"))
with mock.patch("requests.get", side_effect=fake_get):
    found = jisho_lookup.lookup("地政学")
    check("lookup: reading/meaning/jlpt", (found.reading, found.meaning, found.jlpt),
          ("ちせいがく", "geopolitics", "N1"))
    check("lookup: example pair", (found.example, found.example_en), ("地政学は重要だ。", "Geopolitics is important."))
    check("lookup: no match keeps term", jisho_lookup.lookup("ない", "ない"), Row("ない", "ない"))

print("\npipeline (fake Claude):")
claude = FakeClaude()
task = pipeline.Task()
out = pipeline.translate_english(["cat", "zebra", "dog"], task, client=claude)
check("translate aligns results to input", [r.term if r else None for r in out], ["猫", None, "犬"])
call = claude.calls[0]
check("uses structured outputs", call["output_config"]["format"]["type"], "json_schema")
check("uses Sonnet 5.5", call["model"], "claude-sonnet-5-5")

rows, ok, failed = pipeline.translate_english_rows([Row("cat"), Row("語", "ご"), Row("zebra")], task, client=claude)
check("translate_english_rows replaces in place", ([r.term for r in rows], ok, failed),
      (["猫", "語", "zebra"], 1, 1))
rows, ok, _ = pipeline.translate_english_rows([Row("猫", "ねこ"), Row("cat")], task, client=claude)
check("translate_english_rows merges into an existing word", ([r.term for r in rows], rows[0].meaning, ok),
      (["猫"], "cat", 1))

topic = pipeline.generate_topic_rows("airport", 3, {"荷物"}, task, client=claude)
check("topic rows are unique and avoid existing", [r.term for r in topic], ["空港", "搭乗"])

with mock.patch("requests.get", side_effect=fake_get):
    table = [Row("地政学"), Row("Disruption"), Row("猫", "ねこ", "cat", "既存の例。")]
    rows, summary = pipeline.enrich(table, pipeline.JISHO_THEN_CLAUDE, True, task, client=claude)
check("enrich: Jisho fills a Japanese row", (rows[0].reading, rows[0].example), ("ちせいがく", "地政学は重要だ。"))
check("enrich: English rows left alone", rows[1], Row("Disruption"))
check("enrich: existing example kept", rows[2].example, "既存の例。")
check("enrich: summary mentions skipped English", "skipped 1 English" in summary, True)

rows, _ = pipeline.enrich([Row("猫", "ねこ", "cat")], pipeline.CLAUDE_ONLY, True, task, client=claude)
check("enrich: Claude writes missing examples", (rows[0].example, rows[0].example_en), ("猫を使った例文。", "An example."))

cancelled = pipeline.Task()
cancelled.cancel()
check("cancelled task stops before calling Claude",
      pipeline.translate_english(["cat"], cancelled, client=FakeClaude()), [None])

print("\nsheets (fake service):")
store = {}


class FakeValues:
    def get(self, spreadsheetId, range):
        return SimpleNamespace(execute=lambda: {"values": [["Term", "Reading", "Meaning", "Example", "JLPT"],
                                                           ["語", "ご", "word", "例", "N3"], ["", ""]]})

    def clear(self, **kw):
        return SimpleNamespace(execute=lambda: store.update(cleared=kw["range"]))

    def update(self, **kw):
        return SimpleNamespace(execute=lambda: store.update(written=kw["body"]["values"]))


class FakeService:
    def spreadsheets(self):
        return SimpleNamespace(values=FakeValues,
                               get=lambda spreadsheetId: SimpleNamespace(
                                   execute=lambda: {"sheets": [{"properties": {"title": "Tab"}}]}))


got = sheets.read_from_sheet(FakeService(), "id", "Tab")
check("read: old 5-column sheet, blank rows dropped", got, [Row("語", "ご", "word", "例", "N3")])
sheets.write_to_sheet(FakeService(), "id", "Tab", [Row("語", "ご", "word", "例", "N3", "Example.")])
check("write: header + 6 columns", store["written"][0][-1], "Example (EN)")
check("write: row in storage order", store["written"][1], ["語", "ご", "word", "例", "N3", "Example."])

print("\nanki_export:")
check("stable_id is deterministic", stable_id("JP Vocab v4"), stable_id("JP Vocab v4"))
check("highlight_term escapes and bolds", highlight_term("<猫>が猫", "猫"), "&lt;<b>猫</b>&gt;が猫")
deck = make_anki_deck([Row("語", "ご", "word", "例文の語です。", "N3", "Example.")], "Smoke Test Deck")
check("deck has one note", len(deck.notes), 1)
check("deck id matches name hash", deck.deck_id, stable_id("Smoke Test Deck"))
check("no reverse card unless asked", len(deck.notes[0].cards), 1)
deck = make_anki_deck([Row("語", "ご", "word")], "Smoke Test Deck", reverse_cards=True)
check("reverse cards when asked", len(deck.notes[0].cards), 2)
check("card text isn't forced white", "#fff" in deck.notes[0].model.css, False)

print("\nextractor (morphological analysis):")
sample = "日本の文化は地域によって多様で、伝統芸能や祭りが各地で行われています。"
extracted = pipeline.extract_from_text(sample, 5)
check("returns Rows", all(isinstance(r, Row) for r in extracted), True)
check("found some terms", len(extracted) > 0, True)
pairs = [(r.term, r.reading) for r in pipeline.extract_from_text(sample + "新幹線は主要都市を結ぶ。", 40)]
check("verbs keep dictionary-form readings", ("行う", "おこなう") in pairs, True)
check("compounds join prefixes and na-adjectives", {("新幹線", "しんかんせん"), ("主要都市", "しゅようとし")} <= set(pairs), True)
check("auxiliary verbs and particle-spanning phrases dropped",
      [t for t, _ in pairs if t in ("居る", "因る") or "は" in t or t.endswith("や")], [])

print()
if failures:
    print(f"{len(failures)} failure(s): {', '.join(failures)}")
    sys.exit(1)
print("All checks passed.")
