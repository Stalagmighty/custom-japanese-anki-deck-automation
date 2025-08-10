from jisho_api import scrape
from jisho_api.word import Word

word_requests = scrape(Word, ['water', 'fire'], r'C:\Users\Tom\OneDrive\Coding Projects\Japanese_App_BackUp_Words\.venv\Lib\Test_JishoAPI_Data.txt')



# # -*- coding: utf-8 -*-
# """
# GUI: Paste raw vocab -> Parse -> Save CSV / Write to Google Sheet / Make Anki Deck (.apkg)
# Writes headers (Term, Reading, Meaning) to A1:C1 and rows from A2 downward.
# Optional: backup raw input to a 'Raw_Backup' tab with a timestamp.
# """
#
# import re
# import csv
# import hashlib
# from datetime import datetime
# import tkinter as tk
# from tkinter import ttk, filedialog, messagebox, simpledialog
#
# # --- Google APIs (optional if you only want CSV/Anki) ---
# from google.oauth2 import service_account
# from googleapiclient.discovery import build
# from googleapiclient.errors import HttpError
#
# # --- Anki ---
# import genanki
#
# # ------------- Parsing logic -------------
# TERM_BLOCK_RE = re.compile(r'([^\s（]+)（([^）]+)）\s+(.+?)(?=\s+[^\s（]+（[^）]+）|\s*$)')
#
# def split_meanings(s: str):
#     """Split on commas not inside ASCII parentheses; keep semicolons as-is."""
#     parts, current, depth = [], [], 0
#     for ch in s:
#         if ch == '(':
#             depth += 1
#         elif ch == ')' and depth > 0:
#             depth -= 1
#         if ch == ',' and depth == 0:
#             part = ''.join(current).strip()
#             if part:
#                 parts.append(part)
#             current = []
#         else:
#             current.append(ch)
#     last = ''.join(current).strip()
#     if last:
#         parts.append(last)
#     return parts
#
# def parse_blob(text: str):
#     """Return list of [Term, Reading, Meaning]."""
#     text = re.sub(r"\s+", " ", text.strip())
#     rows = []
#     for m in TERM_BLOCK_RE.finditer(text):
#         term = m.group(1).strip()
#         reading = m.group(2).strip()
#         meanings_raw = m.group(3).strip()
#         meanings = ", ".join(split_meanings(meanings_raw))
#         rows.append([term, reading, meanings])
#     return rows
#
# # ------------- Google Sheets helpers -------------
# SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
#
# def get_service(sa_json_path: str):
#     creds = service_account.Credentials.from_service_account_file(sa_json_path, scopes=SCOPES)
#     return build("sheets", "v4", credentials=creds)
#
# def ensure_sheet_exists(service, spreadsheet_id: str, title: str):
#     meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
#     if any(s.get("properties", {}).get("title") == title for s in meta.get("sheets", [])):
#         return
#     service.spreadsheets().batchUpdate(
#         spreadsheetId=spreadsheet_id,
#         body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
#     ).execute()
#
# def write_to_sheet(service, sheet_id: str, tab: str, rows: list,
#                    write_headers=True, clear_body=True):
#     """Write headers to A1:C1 and rows from A2 downward."""
#     values_api = service.spreadsheets().values()
#     if write_headers:
#         values_api.update(
#             spreadsheetId=sheet_id,
#             range=f"{tab}!A1:C1",
#             valueInputOption="USER_ENTERED",
#             body={"values": [["Term", "Reading", "Meaning"]]},
#         ).execute()
#     if clear_body:
#         values_api.clear(spreadsheetId=sheet_id, range=f"{tab}!A2:C").execute()
#     if rows:
#         values_api.update(
#             spreadsheetId=sheet_id,
#             range=f"{tab}!A2",
#             valueInputOption="USER_ENTERED",
#             body={"values": rows},
#         ).execute()
#
# def backup_raw(service, sheet_id: str, backup_tab: str, raw_text: str):
#     ensure_sheet_exists(service, sheet_id, backup_tab)
#     ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#     service.spreadsheets().values().append(
#         spreadsheetId=sheet_id,
#         range=f"{backup_tab}!A:B",
#         valueInputOption="RAW",
#         insertDataOption="INSERT_ROWS",
#         body={"values": [[ts, raw_text]]},
#     ).execute()
#
# # ------------- Anki helpers -------------
# def stable_id(name: str) -> int:
#     """Deterministic 32-bit int from a name (for deck/model IDs)."""
#     return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16)
#
# def make_anki_deck(rows: list, deck_name: str):
#     """
#     Build a genanki.Deck from rows = [[Term, Reading, Meaning], ...].
#     Uses a simple model: fields = Term, Reading, Meaning, Date.
#     """
#     deck_id = stable_id(deck_name)
#     model_name = "JP Vocab Basic v1"
#     model_id = stable_id(model_name)
#
#     model = genanki.Model(
#         model_id=model_id,
#         name=model_name,
#         fields=[
#             {"name": "Term"},
#             {"name": "Reading"},
#             {"name": "Meaning"},
#             {"name": "Date"},
#         ],
#         templates=[
#             {
#                 "name": "Card 1",
#                 "qfmt": """
# <div style="display:flex;align-items:center;justify-content:center;min-height:65vh;font-size:60px;font-weight:700;">
#   {{Term}}
# </div>
#                 """,
#                 "afmt": """
# <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:65vh;padding:10px;color:#fff !important;">
#   <div style="font-size:60px;font-weight:700;">{{Term}}</div>
#   <div style="font-size:34px;margin-top:10px;opacity:1 !important;">{{Reading}}</div>
#   <hr style="width:100%;border:none;border-top:1px solid #aaa;margin:16px 0;">
#   <div style="font-size:28px;opacity:1 !important;line-height:1.4;text-align:center;max-width:900px;text-shadow:0 0 1px rgba(0,0,0,.35);">
#     {{Meaning}}
#   </div>
#   <div style="font-size:14px;color:#eaeaea !important;opacity:1 !important;margin-top:16px;">
#     Added: {{Date}}
#   </div>
# </div>
# """
#             }
#         ],
#     )
#
#     deck = genanki.Deck(deck_id=deck_id, name=deck_name)
#     today = datetime.today().strftime("%Y-%m-%d")
#
#     for term, reading, meaning in rows:
#         guid = genanki.guid_for(f"{term}|{reading}")  # stable per entry
#         note = genanki.Note(
#             model=model,
#             fields=[term, reading, meaning, today],
#             guid=guid,
#         )
#         deck.add_note(note)
#
#     return deck
#
# # ------------- GUI -------------
# class App(tk.Tk):
#     def __init__(self):
#         super().__init__()
#         self.title("Japanese Vocab Parser → CSV / Google Sheets / Anki")
#         self.geometry("1000x680")
#
#         # State
#         self.rows = []
#
#         # Top frame: inputs + buttons
#         top = ttk.Frame(self, padding=8)
#         top.pack(side=tk.TOP, fill=tk.X)
#
#         # Raw input
#         ttk.Label(top, text="Paste raw vocab text:").grid(row=0, column=0, sticky="w")
#         self.text = tk.Text(top, height=8, wrap="word")
#         self.text.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(4, 8))
#         text_scroll = ttk.Scrollbar(top, orient="vertical", command=self.text.yview)
#         text_scroll.grid(row=1, column=6, sticky="ns")
#         self.text.configure(yscrollcommand=text_scroll.set)
#
#         # Google config
#         ttk.Label(top, text="Service Account JSON:").grid(row=2, column=0, sticky="w", pady=(2, 0))
#         self.sa_entry = ttk.Entry(top, width=60)
#         self.sa_entry.grid(row=2, column=1, columnspan=4, sticky="we", padx=(4, 4))
#         ttk.Button(top, text="Browse…", command=self.browse_sa).grid(row=2, column=5, sticky="w")
#
#         ttk.Label(top, text="Sheet ID:").grid(row=3, column=0, sticky="w", pady=(2, 0))
#         self.sheet_entry = ttk.Entry(top, width=60)
#         self.sheet_entry.grid(row=3, column=1, columnspan=4, sticky="we", padx=(4, 4))
#
#         ttk.Label(top, text="Tab name:").grid(row=4, column=0, sticky="w", pady=(2, 0))
#         self.tab_entry = ttk.Entry(top, width=20)
#         self.tab_entry.insert(0, "List_Test_Data")
#         self.tab_entry.grid(row=4, column=1, sticky="w")
#
#         ttk.Label(top, text="Backup tab:").grid(row=4, column=2, sticky="e")
#         self.backup_tab_entry = ttk.Entry(top, width=20)
#         self.backup_tab_entry.insert(0, "Raw_Backup")
#         self.backup_tab_entry.grid(row=4, column=3, sticky="w", padx=(4, 4))
#         self.backup_var = tk.BooleanVar(value=True)
#         ttk.Checkbutton(top, text="Backup raw input", variable=self.backup_var).grid(row=4, column=4, sticky="w")
#
#         # Buttons
#         btns = ttk.Frame(top)
#         btns.grid(row=5, column=0, columnspan=6, sticky="w", pady=(10, 4))
#         ttk.Button(btns, text="Parse", command=self.on_parse).pack(side=tk.LEFT, padx=4)
#         ttk.Button(btns, text="Save CSV…", command=self.on_save_csv).pack(side=tk.LEFT, padx=4)
#         ttk.Button(btns, text="Write to Google Sheet", command=self.on_write_sheet).pack(side=tk.LEFT, padx=4)
#         ttk.Button(btns, text="Make Anki Deck…", command=self.on_make_anki).pack(side=tk.LEFT, padx=4)
#
#         # Status
#         self.status = ttk.Label(top, text="Ready")
#         self.status.grid(row=6, column=0, columnspan=6, sticky="w", pady=(6, 0))
#
#         # Make the text area expandable
#         top.grid_columnconfigure(1, weight=1)
#         top.grid_rowconfigure(1, weight=1)
#
#         # Table preview
#         table_frame = ttk.Frame(self, padding=8)
#         table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
#
#         self.tree = ttk.Treeview(table_frame, columns=("Term", "Reading", "Meaning"), show="headings")
#         self.tree.heading("Term", text="Term")
#         self.tree.heading("Reading", text="Reading")
#         self.tree.heading("Meaning", text="Meaning")
#         self.tree.column("Term", width=140, anchor="w")
#         self.tree.column("Reading", width=160, anchor="w")
#         self.tree.column("Meaning", width=620, anchor="w")
#         self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
#
#         tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
#         tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
#         self.tree.configure(yscrollcommand=tree_scroll.set)
#
#     # --- UI handlers ---
#     def browse_sa(self):
#         path = filedialog.askopenfilename(title="Select Service Account JSON",
#                                           filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
#         if path:
#             self.sa_entry.delete(0, tk.END)
#             self.sa_entry.insert(0, path)
#
#     def on_parse(self):
#         raw = self.text.get("1.0", "end-1c")
#         self.rows = parse_blob(raw)
#         self.refresh_table()
#         self.status.config(text=f"Parsed {len(self.rows)} rows.")
#         if not self.rows:
#             messagebox.showwarning("No entries parsed",
#                                    "I couldn't find any entries. Format should look like: 語（ご） meanings ...")
#
#     def refresh_table(self):
#         for item in self.tree.get_children():
#             self.tree.delete(item)
#         for r in self.rows:
#             self.tree.insert("", "end", values=r)
#
#     def on_save_csv(self):
#         if not self.rows:
#             messagebox.showinfo("Nothing to save", "Parse first, then save.")
#         else:
#             path = filedialog.asksaveasfilename(
#                 title="Save CSV",
#                 defaultextension=".csv",
#                 filetypes=[("CSV", "*.csv"), ("All files", "*.*")]
#             )
#             if not path:
#                 return
#             try:
#                 with open(path, "w", encoding="utf-8-sig", newline="") as f:
#                     writer = csv.writer(f)
#                     writer.writerow(["Term", "Reading", "Meaning"])
#                     writer.writerows(self.rows)
#                 self.status.config(text=f"Saved CSV: {path}")
#             except Exception as e:
#                 messagebox.showerror("Save error", str(e))
#
#     def on_write_sheet(self):
#         if not self.rows:
#             messagebox.showinfo("Nothing to write", "Parse first, then write to Google Sheet.")
#             return
#
#         sa = self.sa_entry.get().strip()
#         sheet_id = self.sheet_entry.get().strip()
#         tab = self.tab_entry.get().strip()
#         backup_tab = self.backup_tab_entry.get().strip()
#         do_backup = self.backup_var.get()
#
#         if not (sa and sheet_id and tab):
#             messagebox.showwarning("Missing details",
#                                    "Please provide Service Account JSON path, Sheet ID, and Tab name.")
#             return
#         try:
#             svc = get_service(sa)
#             if do_backup:
#                 raw = self.text.get("1.0", "end-1c").strip()
#                 if raw:
#                     backup_raw(svc, sheet_id, backup_tab, raw)
#
#             write_to_sheet(svc, sheet_id, tab, self.rows,
#                            write_headers=True, clear_body=True)
#
#             self.status.config(text=f"Wrote {len(self.rows)} rows to {tab} in the Google Sheet.")
#             messagebox.showinfo("Done", f"Wrote {len(self.rows)} rows to '{tab}'.")
#         except HttpError as he:
#             messagebox.showerror("Google API error", str(he))
#         except Exception as e:
#             messagebox.showerror("Error", str(e))
#
#     def on_make_anki(self):
#         if not self.rows:
#             messagebox.showinfo("Nothing to export", "Parse first, then make an Anki deck.")
#             return
#
#         # Ask for deck name
#         default_name = f"JP Vocab ({datetime.today().strftime('%Y-%m-%d')})"
#         deck_name = simpledialog.askstring("Deck name", "Enter Anki deck name:", initialvalue=default_name)
#         if not deck_name:
#             return
#
#         # Choose output file
#         out_path = filedialog.asksaveasfilename(
#             title="Save Anki Deck",
#             defaultextension=".apkg",
#             filetypes=[("Anki package", "*.apkg"), ("All files", "*.*")]
#         )
#         if not out_path:
#             return
#
#         try:
#             deck = make_anki_deck(self.rows, deck_name)
#             genanki.Package(deck).write_to_file(out_path)
#             self.status.config(text=f"Anki deck saved: {out_path}")
#             messagebox.showinfo("Done", f"Anki deck saved:\n{out_path}")
#         except Exception as e:
#             messagebox.showerror("Anki export error", str(e))
#
#
# if __name__ == "__main__":
#     App().mainloop()
