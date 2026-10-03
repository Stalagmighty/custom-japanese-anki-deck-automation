"""Tkinter front end.

All pipeline logic lives in the sibling modules; this file is presentation and
orchestration only.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List

import genanki
from googleapiclient.errors import HttpError

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

# Optional theming
try:
    import ttkbootstrap as tb
except Exception:
    tb = None

try:
    import sv_ttk
except Exception:
    sv_ttk = None

from anki_export import make_anki_deck
from enrichment import generate_examples_with_claude_batch
from extractor import build_rows_from_text
from From_English_Translate import translate_english_terms_batch
from jisho_lookup import augment_row_with_jisho, parse_blob
from anthropic_client import DEFAULT_MODEL, get_anthropic_client
from sheets import backup_raw, get_service, read_from_sheet, write_to_sheet
from text_utils import JP_RE, remove_furigana
from topic_service import TopicGeneratorService
from utils import merge_rows


SECRETS_DIR = Path(__file__).resolve().parent / "secrets"


def default_service_account_path() -> str:
    """First *.json key in the project's gitignored secrets/ folder, else where one should go."""
    found = sorted(SECRETS_DIR.glob("*.json"))
    return str(found[0] if found else SECRETS_DIR / "service_account.json")


class CollapsiblePane(ttk.Frame):
    """A simple collapsible panel with a header toggle."""
    def __init__(self, parent, title="Section", start_open=True, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self._open = bool(start_open)

        # Header row
        self._hdr = ttk.Frame(self)
        self._hdr.pack(fill="x")

        self._glyph = ttk.Label(self._hdr, width=2, anchor="w")
        self._title = ttk.Label(self._hdr, text=title)

        # Small button (optional, so focusable via keyboard)
        self._btn = ttk.Button(
            self._hdr, text="Toggle", command=self.toggle, width=1, style="Tool.TButton"
        )

        self._glyph.pack(side="left")
        self._title.pack(side="left", padx=(2, 6))
        self._btn.pack(side="right")

        self.content = ttk.Frame(self)  # container for actual content
        self._sync()

        # Clicking the header also toggles
        self._hdr.bind("<Button-1>", lambda e: self.toggle())
        self._title.bind("<Button-1>", lambda e: self.toggle())
        self._glyph.bind("<Button-1>", lambda e: self.toggle())

    def toggle(self):
        self._open = not self._open
        self._sync()

    def open(self):
        self._open = True
        self._sync()

    def close(self):
        self._open = False
        self._sync()

    def _sync(self):
        self._glyph.config(text="▾" if self._open else "▸")
        if self._open:
            self.content.pack(fill="both", expand=True)
        else:
            self.content.forget()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Japanese Vocab Parser → CSV / Google Sheets / Anki")
        self.geometry("1000x680")

        self._enrich_running = False

        # --- Top controls bar ---
        controls_frame = ttk.Frame(self)
        controls_frame.pack(fill="x", padx=10, pady=6)

        # Topic service (reused)
        client = get_anthropic_client()  # ensures the key is loaded from file or env
        self.topic_service = TopicGeneratorService(
            augment_row_with_jisho=augment_row_with_jisho,
            client=client,  # inject the working client
            model=DEFAULT_MODEL,
        )

        # Theming
        if tb:
            tb.Style("darkly")
        elif sv_ttk:
            sv_ttk.set_theme("dark")
        else:
            ttk.Style().theme_use("clam")

        self.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style()
        style.configure("TButton", padding=(10, 6))
        style.configure("TRadiobutton", padding=(6, 2))
        style.configure("TCheckbutton", padding=(6, 2))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("Treeview", rowheight=26)
        style.configure("Tool.TButton", padding=(12, 6))

        # Top bar widgets
        self.topic_var = tk.StringVar()
        self.topic_count_var = tk.IntVar(value=30)
        self.gpt_only_var = tk.BooleanVar(value=False)
        self.append_var = tk.BooleanVar(value=True)

        ttk.Label(controls_frame, text="Topic:").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        ttk.Entry(controls_frame, textvariable=self.topic_var, width=24).grid(
            row=0, column=1, padx=4, pady=4, sticky="we"
        )
        ttk.Label(controls_frame, text="Items:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        ttk.Spinbox(controls_frame, from_=5, to=200, textvariable=self.topic_count_var, width=6).grid(
            row=0, column=3, padx=4, pady=4
        )
        ttk.Checkbutton(controls_frame, text="Claude only", variable=self.gpt_only_var).grid(row=0, column=4, padx=6)
        ttk.Button(controls_frame, text="Generate from Topic", command=self.on_generate_from_topic).grid(
            row=0, column=5, padx=6
        )
        ttk.Checkbutton(controls_frame, text="Append", variable=self.append_var).grid(row=0, column=6, padx=6)

        # State
        self.rows: list[list[str]] = []
        self.only_fill_empty_var = tk.BooleanVar(value=True)

        # --- Inputs + buttons area ---
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
        self.sa_entry.insert(0, default_service_account_path())
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

        ttk.Checkbutton(top, text="Only fill empty Example", variable=self.only_fill_empty_var).grid(
            row=4, column=5, sticky="w"
        )

        # Action buttons row
        btns = ttk.Frame(top)
        btns.grid(row=5, column=0, columnspan=6, sticky="w", pady=(10, 4))
        self._buttons: list[ttk.Button] = []

        def _add_btn(text, cmd):
            b = ttk.Button(btns, text=text, command=cmd, style="Tool.TButton")
            b.pack(side=tk.LEFT, padx=6)
            self._buttons.append(b)

        _add_btn("Parse🧾", self.on_parse)
        _add_btn("Save CSV💾", self.on_save_csv)
        _add_btn("Read from Google Sheet⬇️", self.on_read_sheet)
        _add_btn("Write to Google Sheet⬆️", self.on_write_sheet)
        _add_btn("Make Anki Deck🃏", self.on_make_anki)
        _add_btn("Augment Examples✨", self.on_augment_examples)
        _add_btn("From English → Rows", self.from_english_rows)
        _add_btn("Extract from Text🧠", self.on_extract_from_text)
        _add_btn("Clear🧹", self.on_clear)

        # Source mode
        self.source_mode = tk.IntVar(value=2)  # 0=Jisho only, 1=GPT only, 2=Jisho→GPT
        ttk.Separator(top, orient="horizontal").grid(row=6, column=0, columnspan=6, sticky="we", pady=(8, 8))
        mode_row = ttk.LabelFrame(top, text="Example source")
        mode_row.grid(row=7, column=0, columnspan=6, sticky="we")
        mode_row.columnconfigure(3, weight=1)
        ttk.Radiobutton(mode_row, text="Jisho only", variable=self.source_mode, value=0).grid(
            row=0, column=0, padx=8, pady=6, sticky="w"
        )
        ttk.Radiobutton(mode_row, text="Claude only", variable=self.source_mode, value=1).grid(
            row=0, column=1, padx=8, pady=6, sticky="w"
        )
        ttk.Radiobutton(mode_row, text="Jisho → Claude", variable=self.source_mode, value=2).grid(
            row=0, column=2, padx=8, pady=6, sticky="w"
        )

        # Status
        self.status = ttk.Label(top, text="Ready")
        self.status.grid(row=8, column=0, columnspan=6, sticky="w", pady=(8, 0))

        # --- Progress bars ---
        self.gen_pb = ttk.Progressbar(top, orient="horizontal", mode="determinate", maximum=100, length=240)
        self.gen_pb.grid(row=8, column=4, sticky="e", padx=(8, 0))
        ttk.Label(top, text="Gen").grid(row=8, column=3, sticky="e", padx=(16, 0))

        self.enrich_pb = ttk.Progressbar(top, orient="horizontal", mode="determinate", maximum=100, length=240)
        self.enrich_pb.grid(row=8, column=5, sticky="e")
        ttk.Label(top, text="Enrich").grid(row=8, column=5, sticky="w", padx=(0, 8))

        # --- Activity log (collapsible) ---
        self.log_pane = CollapsiblePane(self, title="Activity log", start_open=False)
        self.log_pane.pack(side=tk.TOP, fill=tk.BOTH, expand=False, padx=8, pady=(0, 8))

        self.log_text = scrolledtext.ScrolledText(self.log_pane.content, height=8, wrap="word")
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state="disabled")

        # optional: hotkey to toggle the pane
        self.bind("<Control-Shift-L>", lambda e: self.log_pane.toggle())

        # for safe shutdown / avoiding bgerror
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Make text grow
        top.grid_columnconfigure(1, weight=1)
        top.grid_rowconfigure(1, weight=1)

        # --- Table preview ---
        table_frame = ttk.Frame(self, padding=8)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        columns = ("Term", "Reading", "Meaning", "Example", "JLPT")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        for col in columns:
            self.tree.heading(col, text=col)
        self.tree.column("Term", width=150, anchor="w", stretch=False)
        self.tree.column("Reading", width=140, anchor="w", stretch=False)
        self.tree.column("Meaning", width=260, anchor="w", stretch=True)
        self.tree.column("Example", width=360, anchor="w", stretch=True)
        self.tree.column("JLPT", width=80, anchor="w", stretch=False)
        # self.tree.tag_configure("oddrow", background="#f6f7fb")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------- utilities ----------
    def _set_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in getattr(self, "_buttons", []):
            try:
                b.configure(state=state)
            except Exception:
                pass

    def _pulse(self, text: str):
        try:
            self.after(0, self.status.config, {"text": text})
        except Exception:
            pass
        print(text, flush=True)

    # ---------- UI handlers ----------
    def browse_sa(self):
        path = filedialog.askopenfilename(
            title="Select Service Account JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.sa_entry.delete(0, tk.END)
            self.sa_entry.insert(0, path)


        def toggle(self):
            self._open = not self._open
            self._sync()

        def open(self):
            self._open = True
            self._sync()

        def close(self):
            self._open = False
            self._sync()

        def _sync(self):
            self._glyph.config(text="▾" if self._open else "▸")
            if self._open:
                self.content.pack(fill="both", expand=True)
            else:
                self.content.forget()

    def on_parse(self):
        raw = self.text.get("1.0", "end-1c")
        self.rows = parse_blob(raw)
        self.refresh_table()
        self.status.config(text=f"Parsed {len(self.rows)} rows.")
        if not self.rows:
            messagebox.showwarning(
                "No entries parsed",
                "I couldn't find any entries. Format should look like: 語（ご） meanings ...",
            )

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(self.rows):
            row5 = (r + ["", "", "", "", ""])[:5]  # Term, Reading, Meaning, Example, JLPT
            tags = ("oddrow",) if (idx % 2) else ()
            self.tree.insert("", "end", values=row5, tags=tags)

    def on_clear(self):
        self.text.delete("1.0", "end")
        self.rows = []
        self.tree.delete(*self.tree.get_children())
        self.status.config(text="Cleared input and table.")
        try:
            self.text.focus_set()
        except Exception:
            pass

    def on_save_csv(self):
        if not self.rows:
            messagebox.showinfo("Nothing to save", "Parse first, then save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            width = max(len(r) for r in self.rows) if self.rows else 3
            headers = (
                ["Term", "Reading", "Meaning", "Example", "JLPT"]
                if width >= 5
                else ["Term", "Reading", "Meaning"]
            )
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
            messagebox.showwarning(
                "Missing details", "Please provide Service Account JSON path, Sheet ID, and Tab name."
            )
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
            messagebox.showwarning(
                "Missing details", "Please provide Service Account JSON path, Sheet ID, and Tab name."
            )
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

    def on_augment_examples(self):
        if not self.rows:
            messagebox.showinfo("Nothing to augment", "Parse or load rows first, then choose a source mode.")
            return
        mode = int(self.source_mode.get())  # 0=Jisho only, 1=GPT only, 2=Jisho→GPT
        self._set_buttons_enabled(False)
        self.status.config(text="Starting augmentation…")
        threading.Thread(target=self._enrich_worker, args=(mode,), daemon=True).start()

    def _enrich_worker(self, mode: int):
        """Run Jisho + GPT enrichment over self.rows with progress bars."""

        if self._enrich_running:
            self._safe_after(0, self.log, "Enrichment already running; request ignored.", False)
            self._safe_after(0, self._set_buttons_enabled, True)
            return
        self._enrich_running = True

        def ensure_width5(row):
            row = list(row)
            if len(row) < 5:
                row.extend([""] * (5 - len(row)))
            return row

        def has_text(s: str | None) -> bool:
            return bool((s or "").strip())

        try:
            n = len(self.rows)
            jisho_updates = 0
            gpt_updates = 0

            # -------- PASS 1: Jisho --------
            if mode in (0, 2):
                self._safe_after(0, self.log, f"Jisho pass starting… ({n} rows)")
                for i, row in enumerate(self.rows):
                    if self._closing:
                        break
                    row = ensure_width5(row)
                    term = (row[0] if row else "").strip()
                    reading_hint = (row[1] if len(row) > 1 else "").strip() or None
                    try:
                        new_row = augment_row_with_jisho(term, reading_hint)
                        new_row = ensure_width5(new_row)
                    except Exception as e:
                        self._safe_after(0, self.log, f"[JISHO] {term!r}: {e}", False)
                        new_row = row

                    if self.only_fill_empty_var.get() and has_text(row[3]) and not has_text(new_row[3]):
                        new_row[3] = row[3]

                    changed = (new_row != row)
                    self.rows[i] = new_row
                    if changed:
                        jisho_updates += 1

                    if i % 2 == 0 or i == n - 1:
                        self._safe_after(0, self._set_pb, self.enrich_pb, i + 1, n)
                        self._safe_after(0, self.log,
                                         f"Jisho {i + 1}/{n} (upd {jisho_updates})",
                                         False)

                self._safe_after(0, self.refresh_table)  # <— refresh table after Jisho pass

            # Reset bar before Claude pass
            self._safe_after(0, self._set_pb, self.enrich_pb, 0, 1)

            # -------- PASS 2: Claude (BATCHED) --------
            if mode in (1, 2) and not self._closing:
                need_idxs: List[int] = []
                need_terms: List[str] = []

                for i, row in enumerate(self.rows):
                    row = ensure_width5(row)
                    term = (row[0] if row else "").strip()
                    reading = (row[1] if len(row) > 1 else "").strip()
                    meaning = (row[2] if len(row) > 2 else "").strip()

                    if not term and not reading and not meaning:
                        continue

                    # If Term is English (no JP chars), use Meaning; else use Term.
                    # If Meaning is empty, fall back to Reading if it's JP.
                    if term and not JP_RE.search(term):
                        term_for_gen = meaning or (reading if JP_RE.search(reading) else term)
                    else:
                        term_for_gen = term or (reading if JP_RE.search(reading) else meaning)

                    if mode == 1:
                        if self.only_fill_empty_var.get() and (row[3] or "").strip():
                            continue
                        need_idxs.append(i)
                        need_terms.append(term_for_gen)
                    else:  # mode == 2 (Jisho → GPT): only if Example empty
                        if not (row[3] or "").strip():
                            need_idxs.append(i)
                            need_terms.append(term_for_gen)

                need_total = len(need_idxs)
                done = 0
                self._safe_after(0, self.log, f"Claude pass starting… ({need_total} rows need examples)")
                self._safe_after(0, self._set_pb, self.enrich_pb, 0, max(1, need_total))

                if need_total > 0 and not self._closing:
                    try:
                        term_to_example = generate_examples_with_claude_batch(
                            need_terms,
                            model=DEFAULT_MODEL,
                            batch_size=20,
                            max_tokens_per_batch=8000,
                        )

                    except Exception as e:
                        self._safe_after(0, self.log, f"[Claude batch] failed: {e}", False)
                        term_to_example = {}

                    # Build ordered fallback to handle term key drift
                    ordered_fallback = []
                    for t in need_terms:
                        ordered_fallback.append(
                            term_to_example.get(remove_furigana(t)) or term_to_example.get(t) or ""
                        )

                    if need_total > 0 and not self._closing and not term_to_example:
                        self._safe_after(0, self.log, "[Claude batch] returned no usable items.", False)

                    # Write back
                    for j, (idx, term) in enumerate(zip(need_idxs, need_terms)):
                        if self._closing:
                            break
                        ex = (term_to_example.get(remove_furigana(term))
                              or term_to_example.get(term)
                              or ordered_fallback[j] or "").strip()
                        if ex:
                            row = ensure_width5(self.rows[idx])
                            row[3] = ex
                            self.rows[idx] = row
                            gpt_updates += 1
                            preview = (ex[:18] + "…") if len(ex) > 20 else ex
                            self._safe_after(0, self.log, f"Claude wrote: {preview}", False)
                        done += 1
                        self._safe_after(0, self._set_pb, self.enrich_pb, done, max(1, need_total))
                        if done % 20 == 0 or done == need_total:
                            self._safe_after(0, self.refresh_table)

                    # final refresh
                    self._safe_after(0, self.refresh_table)


        finally:
            self._enrich_running = False
            self._safe_after(0, self._set_buttons_enabled, True)

    def _on_close(self):
        """Mark as closing and destroy safely (prevents bgerror after window closes)."""
        self._closing = True
        try:
            self.destroy()
        except Exception:
            pass

    def _safe_after(self, delay_ms, func, *args, **kwargs):
        """Only schedule UI updates if window still exists."""
        if not self._closing and self.winfo_exists():
            self.after(delay_ms, func, *args, **kwargs)

    def log(self, msg: str, status: bool = True):
        """Append a timestamped line to the log and print to console."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        print(line.strip(), flush=True)
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass
        if status:
            try:
                self.status.config(text=msg)
            except Exception:
                pass

    def _set_pb(self, bar: ttk.Progressbar, current: int, total: int):
        """Update a progressbar deterministically (0–100)."""
        try:
            val = int(100 * (current / max(1, total)))
            bar["value"] = max(0, min(100, val))
            bar.update_idletasks()
        except Exception:
            pass

    def _reset_progress(self):
        for pb in (self.gen_pb, self.enrich_pb):
            try:
                pb["value"] = 0
                pb.update_idletasks()
            except Exception:
                pass

    def on_extract_from_text(self):
        raw = self.text.get("1.0", "end-1c").strip()
        if not raw:
            messagebox.showinfo("No text", "Paste a Japanese article or any unstructured text first.")
            return

        try:
            candidates_rows = build_rows_from_text(
                raw,
                top_k=int(self.topic_count_var.get() or 80),
                min_freq=1,  # adjust if articles are long/short
                allow_phrases=True,
                max_ngram_len=3,
            )
        except Exception as e:
            messagebox.showerror("Extract error", str(e))
            return

        if not candidates_rows:
            messagebox.showinfo("Nothing found", "I couldn't extract useful terms.")
            return

        # merge with existing rows (you already have merge_rows)
        try:
            merged, added, updated, *_ = merge_rows(self.rows or [], candidates_rows)
        except ValueError:
            merged, added, updated = merge_rows(self.rows or [], candidates_rows)

        self.rows = merged
        self.refresh_table()
        self.log(f"Extracted {len(candidates_rows)} items; appended {added}, updated {updated}.")

        # optionally kick off Jisho → Claude enrichment
        # self.start_enrichment_worker()

    # ----- Self-Service Topic Generator (unique & fills to requested count) -----
    def on_generate_from_topic(self):
        topic = (self.topic_var.get() or "").strip()
        if not topic:
            messagebox.showinfo("Topic required", "Type a topic (e.g., 空港 / airport).")
            return

        count = max(5, int(self.topic_count_var.get() or 30))
        gpt_only = bool(self.gpt_only_var.get())

        self._set_buttons_enabled(False)
        self._reset_progress()
        self.log(f"Generating {count} unique items for: {topic} …")

        def run():
            try:
                existing_keys = set()
                for r in (self.rows or []):
                    term = (r[0] if len(r) > 0 else "").strip()
                    reading = (r[1] if len(r) > 1 else "").strip()
                    if term:
                        existing_keys.add((term, reading))

                target = count
                new_rows: list[list[str]] = []
                batch_size = max(10, min(50, target * 2))
                rounds = 0
                no_progress_rounds = 0
                MAX_ROUNDS = 30

                while len(new_rows) < target and not self._closing:
                    rounds += 1
                    needed = target - len(new_rows)
                    ask_for = max(needed, batch_size)
                    self._safe_after(0, self.log, f"[Gen {rounds}] Asking service for ~{ask_for} (need {needed})…")

                    batch = self.topic_service.generate_rows(
                        topic=topic,
                        count=ask_for,
                        gpt_only=gpt_only,
                        existing_keys=existing_keys
                    )

                    kept_before = len(new_rows)
                    for row in (batch or []):
                        term = (row[0] if len(row) > 0 else "").strip()
                        reading = (row[1] if len(row) > 1 else "").strip()
                        if not term:
                            continue
                        key = (term, reading)
                        if key in existing_keys:
                            continue
                        if any(((r[0] if len(r) > 0 else "").strip(),
                                (r[1] if len(r) > 1 else "").strip()) == key for r in new_rows):
                            continue
                        new_rows.append(row)
                        existing_keys.add(key)
                        if len(new_rows) >= target:
                            break

                    gained = len(new_rows) - kept_before
                    self._safe_after(0, self._set_pb, self.gen_pb, len(new_rows), target)
                    self._safe_after(0, self.log,
                                     f"[Gen {rounds}] Got {len(batch)}; kept {gained}; total {len(new_rows)}/{target}",
                                     False)

                    no_progress_rounds = no_progress_rounds + 1 if gained == 0 else 0
                    if no_progress_rounds >= 5:
                        raise RuntimeError("No progress after several rounds. The generator may be stuck.")
                    if rounds >= MAX_ROUNDS:
                        raise RuntimeError("Too many rounds while trying to reach requested count.")

                # merge/replace
                if self.append_var.get():
                    try:
                        merged, added, updated, *_ = merge_rows(self.rows or [], new_rows)
                    except ValueError:
                        merged, added, updated = merge_rows(self.rows or [], new_rows)
                    self.rows = merged
                    msg = f"Appended {added} new, updated {updated}. Total: {len(self.rows)}"
                else:
                    self.rows = new_rows
                    msg = f"Replaced with {len(new_rows)} rows."

                self._safe_after(0, self.refresh_table)
                self._safe_after(0, self.log, msg)
                self._safe_after(0, self.start_enrichment_worker)

            except Exception as e:
                self._safe_after(0, messagebox.showerror, "Topic generation failed", str(e))
                self._safe_after(0, self.log, "Topic generation failed.")
            finally:
                self._safe_after(0, self._set_buttons_enabled, True)

        threading.Thread(target=run, daemon=True).start()

    def start_enrichment_worker(self):
        try:
            mode_value = int(self.source_mode.get())
        except Exception:
            mode_value = 2
        self.status.config(text="Starting enrichment…")
        threading.Thread(target=self._enrich_worker, args=(mode_value,), daemon=True).start()

    def from_english_rows(self):
        """Translate English words/phrases in the big text box into JP rows (batched)."""
        raw = (self.text.get("1.0", "end-1c") or "").strip()
        if not raw:
            messagebox.showinfo("Nothing to translate", "Paste English words/phrases first.")
            return

        # Split by newlines, commas, semicolons, tabs, or pipes
        import re
        parts = [p.strip() for p in re.split(r"[,\n;\t|]+", raw) if p.strip()]

        # Keep items that look English (contain Latin letters)
        english_terms = [p for p in parts if re.search(r"[A-Za-z]", p)]
        if not english_terms:
            messagebox.showwarning(
                "No English terms found",
                "I couldn’t find English items. Put one per line or comma-separated."
            )
            return

        # Do the work in a thread
        def run():
            self._set_buttons_enabled(False)
            self._safe_after(0, self.log, f"Translating {len(english_terms)} English items…")
            try:
                # Reload the translator module so edits take effect without restarting the app
                import importlib
                import From_English_Translate as FET
                importlib.reload(FET)

                rows = FET.translate_english_terms_batch(
                    english_terms,
                    model=DEFAULT_MODEL,
                    batch_size=25,
                    retries=2,
                )
                if not rows:
                    self._safe_after(0, self.log, "Translator returned 0 rows.", False)
                    messagebox.showerror("Translation failed", "Got no rows back from the model.")
                    return

                # Merge into table (append behavior like your other flows)
                self.rows.extend(rows)
                self._safe_after(0, self.refresh_table)
                self._safe_after(0, self.log, f"Translated {len(rows)} rows from English input.")
            except Exception as e:
                msg = str(e)
                self._safe_after(0, self.log, f"[EN→JP batch] {msg}", False)
                messagebox.showerror("Translation failed", msg[:500])
            finally:
                self._safe_after(0, self._set_buttons_enabled, True)

        threading.Thread(target=run, daemon=True).start()

    # ----- Anki export -----
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
            filetypes=[("Anki package", "*.apkg"), ("All files", "*.*")],
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
    get_anthropic_client()   # ensures ANTHROPIC_API_KEY is set for TopicService / translator
    App().mainloop()
