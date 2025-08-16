import re
import csv
import hashlib
from datetime import datetime
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from jisho_api.word import Word
from jisho_api.sentence import Sentence
import threading
# top of file
try:
    import sv_ttk  # pip install sv-ttk
except Exception:
    sv_ttk = None


# --- Google APIs (optional if you only want CSV/Anki) ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Anki ---
import genanki

# --- Open AI ---
from openai import OpenAI
import os

_client: OpenAI | None = None

def get_openai_client() -> OpenAI:
    """Lazy-load the OpenAI client (env var OPENAI_API_KEY or fallback file)."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            key_file = Path(__file__).resolve().parent / ".venv" / "Lib" / "gpt_api_secret.txt"
            api_key = key_file.read_text(encoding="utf-8").strip()
        _client = OpenAI(api_key=api_key)
    return _client



def generate_example_with_gpt(term: str) -> str:
    """Return a short Japanese example sentence using the supplied term."""
    client = get_openai_client()
    prompt = (
        f"Output ONLY one short natural Japanese example sentence using the word '{term}'. "
        "Do not include translations, explanations, or any other text."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
        temperature=0.7,
    )
    # ✅ current SDK uses attribute access
    # safer: handle SDK object and possible missing content
    msg = resp.choices[0].message
    text = getattr(msg, "content", "") or ""
    return text.strip()

# ------------- Parsing logic -------------
TERM_BLOCK_RE = re.compile(r'([^\s（]+)（([^）]+)）\s+(.+?)(?=\s+[^\s（]+（[^）]+）|\s*$)')

FURIGANA_RE = re.compile(r"\([^)]*\)")

def remove_furigana(text: str) -> str:
    return FURIGANA_RE.sub("", text)

def top_two_non_wiki_meanings(w_data) -> str:
    picked = []
    for sense in w_data.senses:
        if "Wikipedia definition" in (sense.parts_of_speech or []):
            continue
        if sense.english_definitions:
            picked.append(", ".join(sense.english_definitions))
        if len(picked) >= 2:
            break
    return "; ".join(picked[:2]) if picked else ""

def fetch_example_sentence(term: str) -> str:
    try:
        s_res = Sentence.request(term)
        if s_res.data:
            return remove_furigana(s_res.data[0].japanese)
    except Exception:
        pass
    return ""

def augment_row_with_jisho(term: str, reading_hint: str | None) -> list[str]:
    """
    Returns: [Term, Reading, Meaning(2 max, non-Wikipedia), Example, JLPT]
    """
    w_res = Word.request(term)
    if not w_res.data and reading_hint:
        w_res = Word.request(reading_hint)
    if not w_res.data:
        return [term, reading_hint or "", "", "", ""]

    w = w_res.data[0]
    jp0 = w.japanese[0]
    out_term = jp0.word or jp0.reading or term
    out_reading = jp0.reading or (reading_hint or "")
    meanings = top_two_non_wiki_meanings(w)
    example = fetch_example_sentence(term)
    jlpt = ", ".join(w.jlpt) if w.jlpt else ""

    return [out_term, out_reading, meanings or "", example, jlpt]

def split_meanings(s: str):
    """Split on commas not inside ASCII parentheses; keep semicolons as-is."""
    parts, current, depth = [], [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')' and depth > 0:
            depth -= 1
        if ch == ',' and depth == 0:
            part = ''.join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)
    last = ''.join(current).strip()
    if last:
        parts.append(last)
    return parts

def parse_blob(text: str):
    """
    Return list of [Term, Reading, Meaning, Example, JLPT].
    Example and JLPT are empty strings at parse time; the Jisho augment fills them later.
    """
    text = re.sub(r"\s+", " ", text.strip())
    rows = []
    for m in TERM_BLOCK_RE.finditer(text):
        term = m.group(1).strip()
        reading = m.group(2).strip()
        meanings_raw = m.group(3).strip()
        meanings = ", ".join(split_meanings(meanings_raw))
        rows.append([term, reading, meanings, "", ""])  # <-- pad Example, JLPT
    return rows


# ------------- Google Sheets helpers -------------
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
        # pad each row to width
        padded = [(r + ["", "", ""])[:len(headers)] for r in rows]
        values_api.update(
            spreadsheetId=sheet_id,
            range=write_start,
            valueInputOption="USER_ENTERED",
            body={"values": padded},
        ).execute()


def read_from_sheet(service, sheet_id: str, tab: str) -> list:
    """Read Term/Reading/Meaning rows (columns A:D) from a sheet."""
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



# ------------- Anki helpers -------------
def stable_id(name: str) -> int:
    """Deterministic 32-bit int from a name (for deck/model IDs)."""
    return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)

def make_anki_deck(rows: list, deck_name: str):
    """
    Build a genanki.Deck from rows = [[Term, Reading, Meaning, Example, JLPT], ...].
    Uses a model with 6 fields: Term, Reading, Meaning, Example, JLPT, Date.
    """
    deck_id = stable_id(deck_name)
    model_name = "JP Vocab Basic v3"
    model_id = stable_id(model_name)

    model = genanki.Model(
        model_id=model_id,
        name=model_name,
        fields=[
            {"name": "Term"},
            {"name": "Reading"},
            {"name": "Meaning"},
            {"name": "Example"},
            {"name": "JLPT"},
            {"name": "Date"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": """
<div style="display:flex;align-items:center;justify-content:center;min-height:65vh;font-size:60px;font-weight:700;">
  {{Term}}
</div>
                """,
                "afmt": """
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:65vh;padding:10px;color:#fff !important;">
  <div style="font-size:60px;font-weight:700;">{{Term}}</div>
  <div style="font-size:34px;margin-top:10px;">{{Reading}}</div>
  <hr style="width:100%;border:none;border-top:1px solid #aaa;margin:16px 0;">
  <div style="font-size:28px;line-height:1.4;text-align:center;max-width:900px;">
    {{Meaning}}
  </div>
  {{#Example}}
  <div style="margin-top:16px;font-size:24px;line-height:1.4;text-align:center;max-width:900px;">
    <b>Example:</b> {{Example}}
  </div>
  {{/Example}}
  {{#JLPT}}
  <div style="margin-top:12px;font-size:18px;color:#ddd;">
    JLPT: {{JLPT}}
  </div>
  {{/JLPT}}
  <div style="font-size:14px;color:#eaeaea;margin-top:16px;">
    Added: {{Date}}
  </div>
</div>
"""
            }
        ],
    )

    deck = genanki.Deck(deck_id=deck_id, name=deck_name)
    today = datetime.today().strftime("%Y-%m-%d")

    for row in rows:
        # pad to 5 fields if needed
        term, reading, meaning, example, jlpt = (row + ["", "", ""])[:5]
        guid = genanki.guid_for(f"v3|{term}|{reading}")  # version tag to avoid collision
        note = genanki.Note(
            model=model,
            fields=[term, reading, meaning, example, jlpt, today],
            guid=guid,
        )
        deck.add_note(note)

    return deck

# ------------- GUI -------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Japanese Vocab Parser → CSV / Google Sheets / Anki")
        self.geometry("1000x680")

        # in App.__init__(), right after super().__init__()
        if sv_ttk:
            sv_ttk.set_theme("light")  # or "dark"
        else:
            ttk.Style().theme_use("clam")

        # in App.__init__(), after creating the root
        self.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style()
        style.configure("TButton", padding=(10, 6))
        style.configure("TRadiobutton", padding=(6, 2))
        style.configure("TCheckbutton", padding=(6, 2))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", rowheight=26)

        # State
        self.rows = []
        self.only_fill_empty_var = tk.BooleanVar(value=True)

        # Top frame: inputs + buttons
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        # Raw input
        ttk.Label(top, text="Paste raw vocab text:").grid(row=0, column=0, sticky="w")
        self.text = tk.Text(top, height=8, wrap="word")
        self.text.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(4, 8))
        text_scroll = ttk.Scrollbar(top, orient="vertical", command=self.text.yview)
        text_scroll.grid(row=1, column=6, sticky="ns")
        self.text.configure(yscrollcommand=text_scroll.set)

        # Google config
        ttk.Label(top, text="Service Account JSON:").grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.sa_entry = ttk.Entry(top, width=60)
        self.sa_entry.grid(row=2, column=1, columnspan=4, sticky="we", padx=(4, 4))

        # ✅ Insert your default path
        default_sa_path = r"C:/Users/Tom/OneDrive/Coding Projects/Japanese_App_BackUp_Words/.venv/Scripts/python-fs-automation-3181504752ca.json"
        self.sa_entry.insert(0, default_sa_path)

        ttk.Button(top, text="Browse…", command=self.browse_sa).grid(row=2, column=5, sticky="w")

        ttk.Label(top, text="Sheet ID:").grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.sheet_entry = ttk.Entry(top, width=60)
        self.sheet_entry.grid(row=3, column=1, columnspan=4, sticky="we", padx=(4, 4))

        ttk.Label(top, text="Tab name:").grid(row=4, column=0, sticky="w", pady=(2, 0))
        self.tab_entry = ttk.Entry(top, width=20)
        self.tab_entry.insert(0, "List_Test_Data")
        self.tab_entry.grid(row=4, column=1, sticky="w")

        ttk.Label(top, text="Backup tab:").grid(row=4, column=2, sticky="e")
        self.backup_tab_entry = ttk.Entry(top, width=20)
        self.backup_tab_entry.insert(0, "Raw_Backup")
        self.backup_tab_entry.grid(row=4, column=3, sticky="w", padx=(4, 4))
        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Backup raw input", variable=self.backup_var).grid(row=4, column=4, sticky="w")

        # Toggle
        ttk.Checkbutton(top, text="Only fill empty Example",
                        variable=self.only_fill_empty_var).grid(row=4, column=5, sticky="w")

        # Buttons
        btns = ttk.Frame(top)
        btns.grid(row=5, column=0, columnspan=6, sticky="w", pady=(10, 4))
        self._buttons = []
        style.configure("Tool.TButton", padding=(12, 6))

        def _add_btn(text, cmd):
            b = ttk.Button(btns, text=text, command=cmd, style="Tool.TButton")
            b.pack(side=tk.LEFT, padx=6)

        _add_btn("Parse🧾", self.on_parse)
        _add_btn("Save CSV💾", self.on_save_csv)
        _add_btn("Read from Google Sheet⬇️", self.on_read_sheet)
        _add_btn("Write to Google Sheet⬆️", self.on_write_sheet)
        _add_btn("Make Anki Deck🃏", self.on_make_anki)
        _add_btn("Augment Examples✨", self.on_augment_examples)

        # --- Source mode controls ---
        self.source_mode = tk.IntVar(value=2)  # 0=Jisho only, 1=GPT only, 2=Jisho→GPT

        # one separator to divide toolbar and options
        ttk.Separator(top, orient="horizontal").grid(row=6, column=0, columnspan=6, sticky="we", pady=(8, 8))

        # labelled group FOR radios (single instance)
        mode_row = ttk.LabelFrame(top, text="Example source")
        mode_row.grid(row=7, column=0, columnspan=6, sticky="we", pady=(0, 0), padx=(0, 0))
        mode_row.columnconfigure(3, weight=1)  # let the group breathe

        ttk.Radiobutton(mode_row, text="Jisho only", variable=self.source_mode, value=0) \
            .grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Radiobutton(mode_row, text="GPT only", variable=self.source_mode, value=1) \
            .grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Radiobutton(mode_row, text="Jisho → GPT", variable=self.source_mode, value=2) \
            .grid(row=0, column=2, padx=8, pady=6, sticky="w")

        # single status label (put it AFTER the radios)
        self.status = ttk.Label(top, text="Ready")
        self.status.grid(row=8, column=0, columnspan=6, sticky="w", pady=(8, 0))

        # Make the text area expandable
        top.grid_columnconfigure(1, weight=1)
        top.grid_rowconfigure(1, weight=1)

        # --- Table preview ---
        table_frame = ttk.Frame(self, padding=8)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        columns = ("Term", "Reading", "Meaning", "Example", "JLPT")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)

        # headings
        for col in columns:
            self.tree.heading(col, text=col)

        # column widths + stretching
        self.tree.column("Term", width=150, anchor="w", stretch=False)
        self.tree.column("Reading", width=140, anchor="w", stretch=False)
        self.tree.column("Meaning", width=260, anchor="w", stretch=True)
        self.tree.column("Example", width=360, anchor="w", stretch=True)
        self.tree.column("JLPT", width=80, anchor="w", stretch=False)

        # zebra striping
        self.tree.tag_configure("oddrow", background="#f6f7fb")  # light grey for readability

        # layout with vertical scrollbar
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

    # --- utilities ---
    def _set_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in getattr(self, "_buttons", []):
            try:
                b.configure(state=state)
            except Exception:
                pass

    # --- UI handlers ---
    def browse_sa(self):
        path = filedialog.askopenfilename(title="Select Service Account JSON",
                                          filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.sa_entry.delete(0, tk.END)
            self.sa_entry.insert(0, path)

    def on_parse(self):
        raw = self.text.get("1.0", "end-1c")
        self.rows = parse_blob(raw)
        self.refresh_table()
        self.status.config(text=f"Parsed {len(self.rows)} rows.")
        if not self.rows:
            messagebox.showwarning("No entries parsed",
                                   "I couldn't find any entries. Format should look like: 語（ご） meanings ...")

    def refresh_table(self):
        """Clear and repopulate the table from self.rows"""
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            # ensure exactly 4 values (pad if needed)
            row4 = (r + ["", "", "", ""])[:4]
            self.tree.insert("", "end", values=row4)

    def on_save_csv(self):
        if not self.rows:
            messagebox.showinfo("Nothing to save", "Parse first, then save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            width = max(len(r) for r in self.rows) if self.rows else 3
            headers = ["Term", "Reading", "Meaning", "Example", "JLPT"] if width >= 5 else ["Term", "Reading", "Meaning"]
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for r in self.rows:
                    row = (r + ["", "", ""])[:len(headers)]
                    writer.writerow(row)
            self.status.config(text=f"Saved CSV: {path}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def on_read_sheet(self):
        sa = self.sa_entry.get().strip()
        sheet_id = self.sheet_entry.get().strip()
        tab = self.tab_entry.get().strip()
        if not (sa and sheet_id and tab):
            messagebox.showwarning("Missing details",
                                   "Please provide Service Account JSON path, Sheet ID, and Tab name.")
            return
        try:
            svc = get_service(sa)
            self.rows = read_from_sheet(svc, sheet_id, tab)
            self.refresh_table()
            self.status.config(text=f"Read {len(self.rows)} rows from {tab}.")
            messagebox.showinfo("Done", f"Read {len(self.rows)} rows from '{tab}'.")
        except HttpError as he:
            messagebox.showerror("Google API error", str(he))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_write_sheet(self):
        if not self.rows:
            messagebox.showinfo("Nothing to write", "Parse first, then write to Google Sheet.")
            return
        sa = self.sa_entry.get().strip()
        sheet_id = self.sheet_entry.get().strip()
        tab = self.tab_entry.get().strip()
        backup_tab = self.backup_tab_entry.get().strip()
        do_backup = self.backup_var.get()
        if not (sa and sheet_id and tab):
            messagebox.showwarning("Missing details",
                                   "Please provide Service Account JSON path, Sheet ID, and Tab name.")
            return
        try:
            svc = get_service(sa)
            if do_backup:
                raw = self.text.get("1.0", "end-1c").strip()
                if raw:
                    backup_raw(svc, sheet_id, backup_tab, raw)
            write_to_sheet(svc, sheet_id, tab, self.rows, write_headers=True, clear_body=True)
            self.status.config(text=f"Wrote {len(self.rows)} rows to {tab} in the Google Sheet.")
            messagebox.showinfo("Done", f"Wrote {len(self.rows)} rows to '{tab}'.")
        except HttpError as he:
            messagebox.showerror("Google API error", str(he))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_augment_with_jisho(self):
        if not self.rows:
            messagebox.showinfo("Nothing to augment", "Parse first, then augment with Jisho.")
            return
        try:
            augmented = []
            for term, reading, *rest in self.rows:
                reading_hint = reading.strip() if reading else ""
                augmented.append(augment_row_with_jisho(term.strip(), reading_hint or None))
            self.rows = augmented  # now 5 columns per row
            self.refresh_table()
            self.status.config(text=f"Augmented {len(self.rows)} rows with Jisho.")
            messagebox.showinfo("Done", "Augmented with Jisho: meanings, example, JLPT.")
        except Exception as e:
            messagebox.showerror("Jisho error", str(e))

    def on_augment_examples(self):
        if not self.rows:
            messagebox.showinfo("Nothing to augment", "Parse first, then choose a source mode.")
            return

        mode = self.source_mode.get()  # 0=Jisho only, 1=GPT only, 2=Jisho→GPT

        def ensure_width5(row):
            row = list(row)
            if len(row) < 5:
                row.extend([""] * (5 - len(row)))
            return row

        def worker():
            try:
                n = len(self.rows)

                # PASS 1 — Jisho
                if mode in (0, 2):
                    for i, row in enumerate(self.rows):
                        row = ensure_width5(row)
                        term = (row[0] if row else "").strip()
                        if mode == 0 or not row[3].strip():
                            try:
                                ex = fetch_example_sentence(term) or ""
                            except Exception as e:
                                print(f"[JISHO ERROR] {term!r}: {e}")
                                ex = ""
                            row[3] = ex
                            self.rows[i] = row
                        self.after(0, self.status.config, {"text": f"Jisho pass… {i + 1}/{n}"})

                # PASS 2 — GPT
                if mode in (1, 2):
                    for i, row in enumerate(self.rows):
                        row = ensure_width5(row)
                        term = (row[0] if row else "").strip()
                        need_gpt = (mode == 1) or (mode == 2 and not row[3].strip())
                        if not need_gpt:
                            continue
                        try:
                            ex = generate_example_with_gpt(term)
                            ex = (ex or "").strip()
                        except Exception as e:
                            print(f"[GPT ERROR] {term!r}: {e}")
                            ex = ""
                        row[3] = ex
                        self.rows[i] = row
                        preview = (ex[:18] + "…") if len(ex) > 20 else ex
                        self.after(0, self.status.config, {"text": f"GPT pass… {i + 1}/{n} — '{preview}'"})
                        # Optional live refresh:
                        # self.after(0, self.refresh_table)
                        time.sleep(0.3)

            finally:
                self.after(0, self.refresh_table)
                self.after(0, self.status.config, {"text": "Augmentation complete."})
                self.after(0, self._set_buttons_enabled, True)
                self.after(0, messagebox.showinfo, "Done", "Examples augmented per selected mode.")

        self._set_buttons_enabled(False)
        self.status.config(text="Starting augmentation…")
        threading.Thread(target=worker, daemon=True).start()

    def on_make_anki(self):
        if not self.rows:
            messagebox.showinfo("Nothing to export", "Parse first, then make an Anki deck.")
            return

        default_name = f"JP Vocab ({datetime.today().strftime('%Y-%m-%d')})"
        deck_name = simpledialog.askstring("Deck name", "Enter Anki deck name:", initialvalue=default_name)
        if not deck_name:
            return

        out_path = filedialog.asksaveasfilename(
            title="Save Anki Deck",
            defaultextension=".apkg",
            filetypes=[("Anki package", "*.apkg"), ("All files", "*.*")]
        )
        if not out_path:
            return

        try:
            deck = make_anki_deck(self.rows, deck_name)
            genanki.Package(deck).write_to_file(out_path)
            self.status.config(text=f"Anki deck saved: {out_path}")
            messagebox.showinfo("Done", f"Anki deck saved:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Anki export error", str(e))

if __name__ == "__main__":
    App().mainloop()
