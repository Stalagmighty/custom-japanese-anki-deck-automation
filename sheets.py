"""Google Sheets I/O for the working vocabulary table."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from models import STORAGE_HEADERS, Row

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_service(sa_json_path: str):
    if not Path(sa_json_path).is_file():
        raise FileNotFoundError(
            f"Service account JSON not found:\n{sa_json_path}\n\n"
            "Put your key file in the project's secrets/ folder, or pick it with Browse…"
        )
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


def write_to_sheet(service, sheet_id: str, tab: str, rows: list[Row]):
    """Replace the tab's contents with a header row plus `rows`."""
    ensure_sheet_exists(service, sheet_id, tab)
    values_api = service.spreadsheets().values()
    values_api.clear(spreadsheetId=sheet_id, range=f"{tab}!A:F").execute()
    values_api.update(
        spreadsheetId=sheet_id,
        range=f"{tab}!A1",
        valueInputOption="RAW",
        body={"values": [STORAGE_HEADERS] + [r.to_list() for r in rows]},
    ).execute()


def read_from_sheet(service, sheet_id: str, tab: str) -> list[Row]:
    """Read rows (columns A:F, header row skipped). Older 5-column sheets read fine."""
    values = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{tab}!A:F",
    ).execute().get("values", [])
    rows = [Row.from_list(r) for r in values[1:]]
    return [r for r in rows if not r.is_empty]


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
