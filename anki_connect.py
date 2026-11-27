"""Send rows straight into desktop Anki through the AnkiConnect add-on.

This is the no-import alternative to the .apkg export: the note type, fields
and templates are the same ones anki_export.py writes. AnkiConnect only runs
inside desktop Anki, so Anki must be open on this computer; the phone gets the
cards on its next AnkiWeb sync.

Words already in the collection (same note type, same term and reading) are
updated in place instead of added again, matching what an .apkg re-import does.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from anki_export import CSS, FIELDS, MODEL_NAME, PRODUCTION, RECOGNITION, note_fields
from models import Row

URL = "http://127.0.0.1:8765"
# Agent Connect (a fork of AnkiConnect, same API) listens here instead.
FALLBACK_URLS = ("http://127.0.0.1:8766",)
ADDON_CODE = "2055492159"
BATCH = 50

_TAG_RE = re.compile(r"<[^>]+>")


class AnkiConnectError(RuntimeError):
    pass


@dataclass
class SendResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    stopped_early: bool = False

    def summary(self, deck: str) -> str:
        parts = [f"{self.added} added to “{deck}”", f"{self.updated} updated"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped (no term)")
        if self.stopped_early:
            parts.append("stopped early")
        return "Sent to Anki: " + ", ".join(parts) + "."


def invoke(action: str, url: str = URL, timeout: float = 60, **params):
    """Call one AnkiConnect action and return its result, raising AnkiConnectError on failure."""
    body = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise AnkiConnectError(
            "Couldn't reach Anki. Open Anki on this computer and make sure the AnkiConnect "
            f"add-on is installed (Tools → Add-ons → Get Add-ons, code {ADDON_CODE}), "
            f"then try again.\n\n({e})") from e
    if not isinstance(reply, dict) or set(reply) != {"result", "error"}:
        raise AnkiConnectError(f"Unexpected reply from AnkiConnect: {str(reply)[:200]}")
    if reply["error"]:
        raise AnkiConnectError(f"AnkiConnect {action}: {reply['error']}")
    return reply["result"]


def find_anki(url: str = URL) -> tuple[str, int]:
    """(url, API version) of the first AnkiConnect-compatible add-on that answers."""
    first_error = None
    for candidate in (url, *[u for u in FALLBACK_URLS if u != url]):
        try:
            return candidate, invoke("version", candidate, timeout=5)
        except AnkiConnectError as e:
            first_error = first_error or e
    raise first_error


def _multi(actions: list[dict], url: str) -> list:
    """Run several actions in one request; raise if any of them failed."""
    results = invoke("multi", url, actions=actions)
    errors = [r["error"] for r in results if isinstance(r, dict) and r.get("error")]
    if errors:
        raise AnkiConnectError(f"AnkiConnect: {len(errors)} update(s) failed, first: {errors[0]}")
    return [r["result"] if isinstance(r, dict) and "result" in r else r for r in results]


def _plain(value: str) -> str:
    """A stored field as plain text, for matching ('&lt;b&gt;' and tags removed)."""
    return html.unescape(_TAG_RE.sub("", value)).strip()


def ensure_note_type(url: str = URL) -> bool:
    """Create the note type if missing, else bring its templates and styling up to date.

    Returns True if it was created.
    """
    templates = [RECOGNITION, PRODUCTION]
    if MODEL_NAME not in invoke("modelNames", url):
        invoke("createModel", url, modelName=MODEL_NAME, inOrderFields=FIELDS, css=CSS, isCloze=False,
               cardTemplates=[{"Name": t["name"], "Front": t["qfmt"], "Back": t["afmt"]} for t in templates])
        return True
    missing = [f for f in FIELDS if f not in invoke("modelFieldNames", url, modelName=MODEL_NAME)]
    if missing:
        raise AnkiConnectError(
            f"The “{MODEL_NAME}” note type in Anki is missing these fields: {', '.join(missing)}. "
            "Add them in Tools → Manage Note Types → Fields, or rename that note type.")
    invoke("updateModelTemplates", url, model={
        "name": MODEL_NAME,
        "templates": {t["name"]: {"Front": t["qfmt"], "Back": t["afmt"]} for t in templates},
    })
    invoke("updateModelStyling", url, model={"name": MODEL_NAME, "css": CSS})
    return False


def existing_notes(url: str = URL) -> dict[tuple[str, str], dict]:
    """{(term, reading): notesInfo entry} for every note of our note type."""
    ids = invoke("findNotes", url, query=f'"note:{MODEL_NAME}"')
    found: dict[tuple[str, str], dict] = {}
    for start in range(0, len(ids), 500):
        for info in invoke("notesInfo", url, notes=ids[start:start + 500]):
            fields = {k: v.get("value", "") for k, v in (info.get("fields") or {}).items()}
            found.setdefault((_plain(fields.get("Term", "")), _plain(fields.get("Reading", ""))), info)
    return found


def send_rows(rows: list[Row], deck_name: str, *, reverse_cards: bool = False,
              task=None, url: str = URL) -> SendResult:
    """Add new rows to `deck_name` and update ones already in the collection.

    An update never blanks a field Anki already has, keeps the note's original
    Date, leaves it in whatever deck it is in, and only ever turns the reverse
    card on (unticking the box doesn't remove reverse cards made earlier).
    """
    log = task.log if task else (lambda msg: None)
    progress = task.progress if task else (lambda done, total: None)
    result = SendResult()

    url, version = find_anki(url)
    if version < 6:
        raise AnkiConnectError(f"AnkiConnect is too old (API version {version}); update the add-on.")
    if ensure_note_type(url):
        log(f"Created the “{MODEL_NAME}” note type in Anki.")
    else:
        log(f"Updated the “{MODEL_NAME}” card templates in Anki.")
    invoke("createDeck", url, deck=deck_name)

    known = existing_notes(url)
    today = datetime.today().strftime("%Y-%m-%d")
    new_notes, updates, seen = [], [], set()
    for row in rows:
        key = (row.term.strip(), row.reading.strip())
        if not key[0]:
            result.skipped += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        values = note_fields(row, today, reverse_cards)
        info = known.get(key)
        if info is None:
            new_notes.append({"deckName": deck_name, "modelName": MODEL_NAME, "fields": values,
                              "options": {"allowDuplicate": True}, "tags": []})
            continue
        changes = {k: v for k, v in values.items() if v and k != "Date"}
        if not reverse_cards:
            changes.pop("Reverse", None)
        updates.append({"action": "updateNoteFields",
                        "params": {"note": {"id": info["noteId"], "fields": changes}}})

    total, done = len(new_notes) + len(updates), 0
    progress(0, total)
    for start in range(0, len(updates), BATCH):
        if task and task.cancelled:
            result.stopped_early = True
            return result
        batch = updates[start:start + BATCH]
        _multi(batch, url)
        result.updated += len(batch)
        done += len(batch)
        progress(done, total)
    for start in range(0, len(new_notes), BATCH):
        if task and task.cancelled:
            result.stopped_early = True
            return result
        batch = new_notes[start:start + BATCH]
        ids = invoke("addNotes", url, notes=batch)
        result.added += sum(1 for i in ids if i)
        failed = len(batch) - sum(1 for i in ids if i)
        if failed:
            log(f"Anki refused {failed} note(s) in one batch.")
        done += len(batch)
        progress(done, total)
    return result
