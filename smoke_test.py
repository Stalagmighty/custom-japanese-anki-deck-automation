"""Offline smoke test — no network, no API keys, no GUI window.

Verifies that every module imports and that the pure functions still behave
after the split. Run with:  python smoke_test.py
"""
from __future__ import annotations

import importlib
import sys

# Windows consoles default to cp1252, which cannot print Japanese.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    "text_utils",
    "anthropic_client",
    "enrichment",
    "jisho_lookup",
    "sheets",
    "anki_export",
    "gui",
    "main",
    "extractor",
    "utils",
    "topic_service",
    "From_English_Translate",
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

from anki_export import make_anki_deck, stable_id
from extractor import build_rows_from_text
from jisho_lookup import parse_blob
from text_utils import remove_furigana, split_meanings

print("\ntext_utils:")
check("remove_furigana strips ASCII parens", remove_furigana("word(reading)"), "word")
# NOTE: current behaviour — FURIGANA_RE matches ASCII "()" only, so full-width
# Japanese "（）" are left intact. Asserted so the split is provably faithful;
# see README "Known gaps" before changing it.
check(
    "remove_furigana leaves full-width parens (current behaviour)",
    remove_furigana("一般的（いっぱんてき）"),
    "一般的（いっぱんてき）",
)
check(
    "split_meanings respects nested parens",
    split_meanings("general, common (and typical, broadly)"),
    ["general", "common (and typical, broadly)"],
)

print("\njisho_lookup.parse_blob:")
multiline = "一般的（いっぱんてき）\ngeneral, common\n\n地政学（ちせいがく）\ngeopolitics"
rows = parse_blob(multiline)
check("multiline: row count", len(rows), 2)
check("multiline: first term", rows[0][0], "一般的")
check("multiline: first reading", rows[0][1], "いっぱんてき")
check("multiline: second term", rows[1][0], "地政学")

print("\nanki_export:")
check("stable_id is deterministic", stable_id("JP Vocab Basic v3"), stable_id("JP Vocab Basic v3"))
deck = make_anki_deck([["語", "ご", "word", "例文です。", "N3"]], "Smoke Test Deck")
check("deck has one note", len(deck.notes), 1)
check("deck id matches name hash", deck.deck_id, stable_id("Smoke Test Deck"))

print("\nextractor (morphological analysis):")
sample = "日本の文化は地域によって多様で、伝統芸能や祭りが各地で行われています。"
extracted = build_rows_from_text(sample, top_k=5, min_freq=1)
check("returns 5-column rows", all(len(r) == 5 for r in extracted), True)
check("found some terms", len(extracted) > 0, True)

print()
if failures:
    print(f"{len(failures)} failure(s): {', '.join(failures)}")
    sys.exit(1)
print("All checks passed.")
