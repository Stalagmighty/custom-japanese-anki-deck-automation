"""User settings remembered between runs (Google Sheet details and a few preferences).

Stored as JSON in the user's home folder, outside the project, so nothing here
can be committed by accident.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SECRETS_DIR = PROJECT_DIR / "secrets"
SETTINGS_PATH = Path.home() / ".jp_vocab_builder.json"


def default_service_account_path() -> str:
    """First *.json key in the project's gitignored secrets/ folder, else where one should go."""
    found = sorted(SECRETS_DIR.glob("*.json"))
    return str(found[0] if found else SECRETS_DIR / "service_account.json")


@dataclass
class Settings:
    service_account: str = ""
    sheet_id: str = ""
    tab: str = "List_Test_Data"
    backup_tab: str = "Raw_Backup"
    backup_raw: bool = True
    example_source: str = "jisho_then_claude"
    only_fill_empty: bool = True
    reverse_cards: bool = False
    append: bool = True

    def resolved_service_account(self) -> str:
        """The saved key path if it still exists, otherwise the secrets/ default."""
        if self.service_account and Path(self.service_account).is_file():
            return self.service_account
        return default_service_account_path()

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
