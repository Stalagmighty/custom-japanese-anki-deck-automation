#!/usr/bin/env python3
"""
kanji_to_vocab.py

Reads kanji dictionary data from a Google Sheet and writes generated
vocabulary entries to a target sheet (or tab).

Source columns expected:
    Unique ID, kanji, 読めるか, kun_reading, son_readings, meanings,
    school_grade, jlpt_level

Target columns written:
    Kanji, Word, Pronunciation, Reading, Meanings,
    All_Onyomi, All_Kunyomi

Authentication: Google Service Account JSON key file.

Usage:
    python kanji_to_vocab.py \
        --credentials path/to/service_account.json \
        --spreadsheet-id YOUR_SPREADSHEET_ID \
        --source-sheet "Sheet1" \
        --target-sheet "Vocab"

Install dependencies first:
    pip install google-auth google-auth-httplib2 google-api-python-client
"""

import argparse
import re
import sys
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Google Sheets API scope ────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def clean_reading(reading: str) -> str:
    """
    Strip okurigana suffix (everything after '.') from a reading token,
    but keep the full reading for the Pronunciation / Word columns.
    e.g. 'よそお.う' → stem 'よそお', word-reading 'よそおう'
    """
    return reading.strip()


def reading_to_word(kanji: str, reading: str) -> tuple[str, str]:
    """
    Given a kanji and a reading token (possibly with '.'),
    return (word, pronunciation).

    The '.' in kun-readings marks where okurigana begins:
        kanji   = '勇'
        reading = 'いさ.む'
        → word          = '勇む'   (kanji + okurigana)
        → pronunciation = 'いさむ'  (full hiragana, no dot)

    For on-readings (no dot), return:
        word          = kanji          (just the kanji)
        pronunciation = reading        (katakana)
    """
    reading = reading.strip()
    if "." in reading:
        stem, okurigana = reading.split(".", 1)
        word = kanji + okurigana
        pronunciation = stem + okurigana
    else:
        word = kanji
        pronunciation = reading
    return word, pronunciation


def parse_readings(raw: str) -> list[str]:
    """Split a semicolon-separated readings string into individual tokens."""
    if not raw or not raw.strip():
        return []
    return [r.strip() for r in raw.split(";") if r.strip()]


def format_onyomi(raw: str) -> str:
    """Return on-readings joined with newline (matching target format)."""
    readings = parse_readings(raw)
    return "\n".join(readings)


def format_kunyomi(raw: str) -> str:
    """Return kun-readings joined with newline (matching target format)."""
    readings = parse_readings(raw)
    return "\n".join(readings)


def build_service(credentials_path: str):
    """Authenticate and return a Google Sheets service object."""
    creds = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds)
    return service.spreadsheets()


# ── Read ───────────────────────────────────────────────────────────────────────

def read_source(sheets, spreadsheet_id: str, sheet_name: str) -> list[dict]:
    """
    Read all rows from the source sheet and return as a list of dicts
    keyed by the header row.
    """
    range_name = f"'{sheet_name}'"
    result = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()

    rows = result.get("values", [])
    if not rows:
        print(f"[WARN] No data found in sheet '{sheet_name}'.")
        return []

    headers = [h.strip() for h in rows[0]]
    records = []
    for row in rows[1:]:
        # Pad short rows so zip always produces a value per header
        padded = row + [""] * (len(headers) - len(row))
        records.append(dict(zip(headers, padded)))
    return records


# ── Transform ──────────────────────────────────────────────────────────────────

def generate_vocab_rows(records: list[dict]) -> list[list]:
    """
    For each source record, generate one output row per reading
    (both kun and on).  Returns a list of rows ready to write to Sheets,
    including a header row as the first item.
    """
    output_headers = [
        "Kanji", "Word", "Pronunciation", "Reading",
        "Meanings", "All_Onyomi", "All_Kunyomi",
    ]
    output_rows = [output_headers]

    for rec in records:
        kanji = rec.get("kanji", "").strip()
        meanings = rec.get("meanings", "").strip()
        kun_raw = rec.get("kun_reading", "").strip()
        on_raw = rec.get("son_readings", "").strip()
        readable = rec.get("読めるか", "").strip()  # "Kun Only" / "On Only" / "Both" / "Neither"

        if not kanji:
            continue

        all_onyomi = format_onyomi(on_raw)
        all_kunyomi = format_kunyomi(kun_raw)

        # Decide which readings to turn into vocab entries
        kun_readings = parse_readings(kun_raw)
        on_readings = parse_readings(on_raw)

        # Kun readings → one row each
        for reading in kun_readings:
            word, pronunciation = reading_to_word(kanji, reading)
            output_rows.append([
                kanji,
                word,
                pronunciation,
                reading,
                meanings,
                all_onyomi,
                all_kunyomi,
            ])

        # On readings → one row each
        for reading in on_readings:
            word, pronunciation = reading_to_word(kanji, reading)
            output_rows.append([
                kanji,
                word,
                pronunciation,
                reading,
                meanings,
                all_onyomi,
                all_kunyomi,
            ])

        # If neither kun nor on readings exist, still write one row
        if not kun_readings and not on_readings:
            output_rows.append([
                kanji, kanji, "", "", meanings, all_onyomi, all_kunyomi,
            ])

    return output_rows


# ── Write ──────────────────────────────────────────────────────────────────────

def ensure_sheet_exists(sheets, spreadsheet_id: str, sheet_name: str) -> None:
    """Create the target sheet tab if it doesn't already exist."""
    meta = sheets.get(spreadsheetId=spreadsheet_id).execute()
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if sheet_name not in existing:
        body = {
            "requests": [{
                "addSheet": {
                    "properties": {"title": sheet_name}
                }
            }]
        }
        sheets.batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        print(f"[INFO] Created new sheet tab: '{sheet_name}'")


def clear_sheet(sheets, spreadsheet_id: str, sheet_name: str) -> None:
    """Clear all existing content from the target sheet."""
    sheets.values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'",
        body={},
    ).execute()


def write_rows(
        sheets,
        spreadsheet_id: str,
        sheet_name: str,
        rows: list[list],
) -> None:
    """Write rows to the target sheet starting at A1."""
    body = {"values": rows}
    result = sheets.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()
    updated = result.get("updatedCells", 0)
    print(f"[INFO] Wrote {len(rows) - 1} vocab rows ({updated} cells) to '{sheet_name}'.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert kanji sheet → vocab sheet in Google Sheets."
    )
    parser.add_argument(
        "--credentials", required=True,
        help="Path to service account JSON key file.",
    )
    parser.add_argument(
        "--spreadsheet-id", required=True,
        help="Google Spreadsheet ID (from the URL).",
    )
    parser.add_argument(
        "--source-sheet", default="Sheet1",
        help="Name of the source sheet tab (default: Sheet1).",
    )
    parser.add_argument(
        "--target-sheet", default="Vocab",
        help="Name of the target sheet tab to write to (default: Vocab).",
    )
    args = parser.parse_args()

    print(f"[INFO] Authenticating with service account: {args.credentials}")
    try:
        sheets = build_service(args.credentials)
    except Exception as e:
        print(f"[ERROR] Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Read
    print(f"[INFO] Reading from sheet: '{args.source_sheet}'")
    try:
        records = read_source(sheets, args.spreadsheet_id, args.source_sheet)
    except HttpError as e:
        print(f"[ERROR] Could not read source sheet: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Read {len(records)} kanji records.")

    # Transform
    vocab_rows = generate_vocab_rows(records)
    print(f"[INFO] Generated {len(vocab_rows) - 1} vocab entries.")

    # Write
    print(f"[INFO] Writing to sheet: '{args.target_sheet}'")
    try:
        ensure_sheet_exists(sheets, args.spreadsheet_id, args.target_sheet)
        clear_sheet(sheets, args.spreadsheet_id, args.target_sheet)
        write_rows(sheets, args.spreadsheet_id, args.target_sheet, vocab_rows)
    except HttpError as e:
        print(f"[ERROR] Could not write to target sheet: {e}", file=sys.stderr)
        sys.exit(1)

    print("[DONE] All done.")


if __name__ == "__main__":
    main()