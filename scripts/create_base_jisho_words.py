#!/usr/bin/env python3
"""
kanji_to_vocab.py

For each kanji in the source sheet, cycles through every kun-yomi and on-yomi
reading, queries Jisho for real words using that reading, and writes results
to the target sheet.

Source sheet columns:  kanji | kun_readings | on_readings | meanings (optional)
Target sheet columns:  English Meaning | Reading | Japanese Word | Example

Install dependencies:
    pip install google-auth google-auth-httplib2 google-api-python-client requests
"""

import sys
import time

import requests
from urllib.parse import quote_plus
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# -- Configuration -- edit these values ---------------------------------------

CREDENTIALS    = r"C:/Users/Tom/OneDrive/Coding Projects/Japanese_App_BackUp_Words/.venv/Scripts/python-fs-automation-3181504752ca.json"  # path to your service account JSON key file
SPREADSHEET_ID = "1cmYuOb_Tpa2LcSVUxGbBOC-uafDzWFamaJ3aHf7AqdE"        # from your Google Sheet URL
SOURCE_SHEET   = "Pre-identified_Kanji"                     # tab to read kanji from
TARGET_SHEET   = "Copy of N1_N2_MyKanji_Updated"                      # tab to write vocab entries to
COMMON_ONLY    = False                         # set False to include uncommon words too
API_DELAY      = 0.5                          # seconds between Jisho requests


# -- Google Sheets helpers ----------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def ensure_sheet_exists(service, title):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    if any(s.get("properties", {}).get("title") == title for s in meta.get("sheets", [])):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    print(f"[INFO] Created new tab: '{title}'")


def read_source(service):
    values = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SOURCE_SHEET,
    ).execute().get("values", [])

    if not values:
        print(f"[WARN] No data in '{SOURCE_SHEET}'.")
        return []

    headers = [h.strip() for h in values[0]]
    records = []
    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, padded))
        if any(v.strip() for v in padded):
            records.append(record)
    return records


def write_target(service, rows):
    """Write header + rows to target sheet only, never touches source sheet."""
    values_api = service.spreadsheets().values()
    headers = ["English Meaning", "Reading", "Japanese Word", "Example", "All_Onyomi", "All_Kunyomi"]

    # Write header
    values_api.update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{TARGET_SHEET}!A1:G1",
        valueInputOption="USER_ENTERED",
        body={"values": [headers]},
    ).execute()

    # Clear only the target body
    values_api.clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{TARGET_SHEET}!A2:G",
    ).execute()

    if rows:
        values_api.update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TARGET_SHEET}!A2",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

    print(f"[INFO] Wrote {len(rows)} rows to '{TARGET_SHEET}'.")


# -- Jisho helpers ------------------------------------------------------------

JISHO_API = "https://jisho.org/api/v1/search/words?keyword={q}&page={p}"


def jisho_search(keyword, page=1, timeout=15):
    url = JISHO_API.format(q=quote_plus(keyword), p=page)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json().get("data", [])


def katakana_to_hiragana(text):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ン" else c for c in text)


def strip_dot(reading):
    """'いさ.む' -> 'いさむ'"""
    return reading.replace(".", "")


def get_stem(reading):
    """'いさ.む' -> 'いさ'  (the part before the dot, for prefix matching)"""
    return reading.split(".")[0] if "." in reading else reading


def find_words_for_reading(kanji, reading, fallback_meaning, seen, all_onyomi, all_kunyomi, max_pages=2):
    """
    Search Jisho for words containing `kanji` and matching `reading`.
    `seen` is shared across all readings for a kanji to prevent duplicates.
    Returns list of [english_meaning, reading, japanese_word, ""].
    """
    stem      = katakana_to_hiragana(get_stem(reading))
    full_hira = katakana_to_hiragana(strip_dot(reading))
    results   = []

    for page in range(1, max_pages + 1):
        try:
            items = jisho_search(kanji, page=page)
        except Exception as e:
            print(f"    [WARN] Jisho error ({kanji} / {reading} page {page}): {e}")
            break

        if not items:
            break

        for item in items:
            if COMMON_ONLY and not item.get("is_common"):
                continue

            jap          = (item.get("japanese") or [{}])[0]
            word         = jap.get("word") or jap.get("reading") or ""
            word_reading = jap.get("reading") or ""

            if not word or not word_reading:
                continue

            # Only keep words that actually contain the kanji
            if kanji not in word:
                continue

            # Only keep if reading starts with our stem
            word_hira = katakana_to_hiragana(word_reading)
            if not word_hira.startswith(stem):
                continue

            if word in seen:
                continue
            seen.add(word)

            # Meaning: try Jisho first, fall back to source sheet meaning
            sense         = (item.get("senses") or [{}])[0]
            jisho_meaning = "; ".join(sense.get("english_definitions", []))
            meaning       = jisho_meaning if jisho_meaning else fallback_meaning

            results.append([meaning, word_reading, word, "", all_onyomi, all_kunyomi])
            return results  # one word per reading, stop at first match

        time.sleep(API_DELAY)

    return results


# -- Main ---------------------------------------------------------------------

def main():
    print("[INFO] Authenticating…")
    try:
        service = get_service()
    except Exception as e:
        print(f"[ERROR] Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Reading source sheet '{SOURCE_SHEET}'…")
    try:
        records = read_source(service)
    except HttpError as e:
        print(f"[ERROR] Could not read source sheet: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] {len(records)} kanji records found.")

    all_rows = []
    total    = len(records)

    for i, rec in enumerate(records, 1):
        kanji    = rec.get("kanji", "").strip()
        kun_raw  = rec.get("kun_readings", "").strip()
        on_raw   = rec.get("on_readings", "").strip()
        fallback = rec.get("meanings", "").strip()

        if not kanji:
            continue

        kun_readings = [r.strip() for r in kun_raw.split(";") if r.strip()]
        on_readings  = [r.strip() for r in on_raw.split(";")  if r.strip()]
        readable     = rec.get("読めるか", "").strip()

        # On Only = knows on, needs to practise kun
        # Kun Only = knows kun, needs to practise on
        # Neither  = practise both
        # Both     = already knows both, skip
        if readable == "Both":
            print(f"[{i}/{total}] {kanji}  skipping (Both)")
            continue
        elif readable == "On Only":
            all_readings = kun_readings
        elif readable == "Kun Only":
            all_readings = on_readings
        else:  # Neither or blank
            all_readings = kun_readings + on_readings

        print(f"[{i}/{total}] {kanji}  ({len(all_readings)} readings)…")

        if not all_readings:
            continue

        all_onyomi    = ";".join(on_readings)
        all_kunyomi   = ";".join(kun_readings)
        seen_for_kanji = set()
        for reading in all_readings:
            words = find_words_for_reading(kanji, reading, fallback, seen_for_kanji, all_onyomi, all_kunyomi)
            if words:
                print(f"    {reading}: {len(words)} word(s)")
                all_rows.extend(words)
            else:
                print(f"    {reading}: no match, skipping")

    print(f"\n[INFO] {len(all_rows)} total vocab rows generated.")

    print(f"[INFO] Writing to '{TARGET_SHEET}'…")
    try:
        ensure_sheet_exists(service, TARGET_SHEET)
        write_target(service, all_rows)
    except HttpError as e:
        print(f"[ERROR] Could not write to target sheet: {e}", file=sys.stderr)
        sys.exit(1)

    print("[DONE]")


if __name__ == "__main__":
    main()