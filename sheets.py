"""Google Sheets I/O for the working vocabulary table."""
from __future__ import annotations

from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_service(sa_json_path: str):
    creds = service_account.Credentials.from_service_account_file(sa_json_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def ensure_sheet_exists(service, spreadsheet_id: str, title: str):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    if any(s.get("properties", {}).get("title") == title for s in meta.get("sheets", [])):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()


def write_to_sheet(service, sheet_id: str, tab: str, rows: list,
                   write_headers=True, clear_body=True):
    """Write headers and rows; adapts to 3 or 5 columns based on data."""
    values_api = service.spreadsheets().values()
    width = max(len(r) for r in rows) if rows else 3
    if width >= 5:
        headers = ["Term", "Reading", "Meaning", "Example", "JLPT"]
        header_range = f"{tab}!A1:E1"
        clear_range = f"{tab}!A2:E"
        write_start = f"{tab}!A2"
    else:
        headers = ["Term", "Reading", "Meaning"]
        header_range = f"{tab}!A1:C1"
        clear_range = f"{tab}!A2:C"
        write_start = f"{tab}!A2"

    if write_headers:
        values_api.update(
            spreadsheetId=sheet_id,
            range=header_range,
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()
    if clear_body:
        values_api.clear(spreadsheetId=sheet_id, range=clear_range).execute()
    if rows:
        padded = [(r + ["", "", ""])[:len(headers)] for r in rows]
        values_api.update(
            spreadsheetId=sheet_id,
            range=write_start,
            valueInputOption="USER_ENTERED",
            body={"values": padded},
        ).execute()


def read_from_sheet(service, sheet_id: str, tab: str) -> list:
    """Read Term/Reading/Meaning rows (columns A:E) from a sheet."""
    values = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{tab}!A:E",
    ).execute().get("values", [])
    rows = []
    for r in values[1:]:  # drop header row
        padded = (r + ["", "", ""])[:5]
        if any(c.strip() for c in padded):
            rows.append(padded)
    return rows


def backup_raw(service, sheet_id: str, backup_tab: str, raw_text: str):
    ensure_sheet_exists(service, sheet_id, backup_tab)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{backup_tab}!A:B",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[ts, raw_text]]},
    ).execute()
