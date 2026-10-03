"""Tkinter front end, laid out as three steps: get words, review them, export.

The work itself lives in pipeline.py and friends. This file is layout, plus
running that work on a background thread: the thread only ever posts events to
a queue, and the Tk main loop drains it, so Tk is never touched off-thread.
"""
from __future__ import annotations

import csv
import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from tkinter import font as tkfont

import genanki

try:  # optional theming
    import ttkbootstrap as tb
except Exception:
    tb = None

import pipeline
import sheets
from anki_export import make_anki_deck
from jisho_lookup import parse_blob
from models import STORAGE_HEADERS, Row, merge_rows
from settings import Settings

# (Row attribute, heading, width) in display order
COLUMNS = [
    ("term", "Term", 95),
    ("reading", "Reading", 105),
    ("meaning", "Meaning", 150),
    ("example", "Example", 250),
    ("example_en", "Example (EN)", 150),
    ("jlpt", "JLPT", 45),
]
SOURCES = {
    "Jisho → Claude": pipeline.JISHO_THEN_CLAUDE,
    "Jisho only": pipeline.JISHO_ONLY,
    "Claude only": pipeline.CLAUDE_ONLY,
}
JP_FONTS = ("Yu Gothic UI", "Meiryo UI", "Meiryo", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP")
SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def split_terms(text: str) -> list[str]:
    """English terms from free text: one per line, or separated by , ; | or tabs."""
    parts = (p.strip() for p in re.split(r"[\n,;\t|]+", text))
    return [p for p in parts if p and re.search(r"[A-Za-z]", p)]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Japanese Vocab → Anki")
        self.geometry("1280x800")
        self.minsize(980, 620)

        self.settings = Settings.load()
        self.rows: list[Row] = []
        self._task: pipeline.Task | None = None
        self._events: queue.Queue = queue.Queue()
        self._log_lines: list[str] = []
        self._log_view: scrolledtext.ScrolledText | None = None
        self._busy_widgets: list[tk.Widget] = []
        self._closing = False

        self._setup_style()
        self._build_menu()
        self._build_status_bar()
        self._build_body()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-l>", lambda e: self.show_log())
        self.after(100, self._drain_events)
        self.refresh_table()

    # ================================================================ layout

    def _setup_style(self):
        if tb:
            tb.Style("darkly")
        else:
            ttk.Style().theme_use("clam")
        families = set(tkfont.families(self))
        family = next((f for f in JP_FONTS if f in families), None)
        base = tkfont.nametofont("TkDefaultFont")
        if family:
            base.configure(family=family)
        base.configure(size=10)
        for name in ("TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=base.cget("family"), size=10)
        style = ttk.Style()
        style.configure("Step.TLabel", font=(base.cget("family"), 13, "bold"))
        style.configure("Hint.TLabel", font=(base.cget("family"), 9))
        self._accent = "primary.TButton" if tb else "Accent.TButton"
        if not tb:
            style.configure("Accent.TButton", font=(base.cget("family"), 10, "bold"))

    def _build_menu(self):
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=False)
        file_menu.add_command(label="Open CSV…", command=self.on_open_csv)
        file_menu.add_command(label="Save CSV…", command=self.on_save_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Google Sheet settings…", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._on_close)
        bar.add_cascade(label="File", menu=file_menu)
        view_menu = tk.Menu(bar, tearoff=False)
        view_menu.add_command(label="Activity log", accelerator="Ctrl+L", command=self.show_log)
        bar.add_cascade(label="View", menu=view_menu)
        self.config(menu=bar)

    def _build_status_bar(self):
        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status = ttk.Label(bar, text="Ready", anchor="w")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100, length=260)
        self.progress.pack(side=tk.RIGHT, padx=8)

    def _build_body(self):
        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        left = ttk.Frame(panes, padding=(0, 0, 10, 0))
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)
        self._build_inputs(left)
        self._build_review(right)

    def _action(self, parent, text, command, accent=False) -> ttk.Button:
        """A button that is disabled while background work runs."""
        btn = ttk.Button(parent, text=text, command=command, style=self._accent if accent else "TButton")
        self._busy_widgets.append(btn)
        return btn

    def _text_box(self, parent, height=12) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, pady=6)
        text = tk.Text(frame, height=height, width=34, wrap="word", undo=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return text

    def _hint(self, parent, text):
        ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=280, justify="left").pack(anchor="w")

    def _build_inputs(self, left):
        ttk.Label(left, text="1  Get words", style="Step.TLabel").pack(anchor="w")
        self.inputs = ttk.Notebook(left)
        self.inputs.pack(fill=tk.BOTH, expand=True, pady=(6, 4))

        tab = ttk.Frame(self.inputs, padding=10)
        self.inputs.add(tab, text="Vocab list")
        self._hint(tab, "Paste entries like 一般的（いっぱんてき） general, common — "
                        "one per line, or an export from a vocab app.")
        self.list_text = self._text_box(tab)
        self._action(tab, "Add to table", self.on_parse, accent=True).pack(anchor="e")

        tab = ttk.Frame(self.inputs, padding=10)
        self.inputs.add(tab, text="English")
        self._hint(tab, "English words or phrases, one per line or comma-separated. "
                        "Leave this empty to translate the English rows already in the table "
                        "(e.g. a list read from your Google Sheet).")
        self.english_text = self._text_box(tab)
        self._action(tab, "Translate to Japanese", self.on_translate, accent=True).pack(anchor="e")

        tab = ttk.Frame(self.inputs, padding=10)
        self.inputs.add(tab, text="Article")
        self._hint(tab, "Paste Japanese text and the most useful words are picked out (no API calls).")
        self.article_text = self._text_box(tab)
        row = ttk.Frame(tab)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Max words").pack(side=tk.LEFT)
        self.max_words_var = tk.IntVar(value=40)
        ttk.Spinbox(row, from_=5, to=300, width=6, textvariable=self.max_words_var).pack(side=tk.LEFT, padx=6)
        self._action(row, "Extract words", self.on_extract, accent=True).pack(side=tk.RIGHT)

        tab = ttk.Frame(self.inputs, padding=10)
        self.inputs.add(tab, text="Topic")
        self._hint(tab, "Claude suggests new words for a topic, with readings, meanings and "
                        "example sentences. Words already in the table are avoided.")
        form = ttk.Frame(tab)
        form.pack(fill=tk.X, pady=8)
        ttk.Label(form, text="Topic").grid(row=0, column=0, sticky="w", pady=3)
        self.topic_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.topic_var).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(form, text="Words").grid(row=1, column=0, sticky="w", pady=3)
        self.topic_count_var = tk.IntVar(value=30)
        ttk.Spinbox(form, from_=5, to=200, width=6, textvariable=self.topic_count_var).grid(
            row=1, column=1, sticky="w", padx=6)
        form.columnconfigure(1, weight=1)
        self._action(tab, "Generate words", self.on_topic, accent=True).pack(anchor="e")

        tab = ttk.Frame(self.inputs, padding=10)
        self.inputs.add(tab, text="Google Sheet")
        self.sheet_label = ttk.Label(tab, justify="left", wraplength=280)
        self.sheet_label.pack(anchor="w", pady=(0, 8))
        self._action(tab, "Read from Google Sheet", self.on_read_sheet, accent=True).pack(anchor="w")
        ttk.Button(tab, text="Sheet settings…", command=self.open_settings).pack(anchor="w", pady=6)
        self._update_sheet_label()

        self.append_var = tk.BooleanVar(value=self.settings.append)
        ttk.Checkbutton(left, text="Add to the table (untick to replace it)",
                        variable=self.append_var).pack(anchor="w", pady=(2, 8))

    def _build_review(self, right):
        head = ttk.Frame(right)
        head.pack(fill=tk.X)
        ttk.Label(head, text="2  Review", style="Step.TLabel").pack(side=tk.LEFT)
        self.count_label = ttk.Label(head, text="")
        self.count_label.pack(side=tk.RIGHT)

        tools = ttk.Frame(right)
        tools.pack(fill=tk.X, pady=6)
        ttk.Label(tools, text="Fill in from").pack(side=tk.LEFT)
        source_name = next((k for k, v in SOURCES.items() if v == self.settings.example_source),
                           "Jisho → Claude")
        self.source_var = tk.StringVar(value=source_name)
        ttk.Combobox(tools, textvariable=self.source_var, values=list(SOURCES), state="readonly",
                     width=15).pack(side=tk.LEFT, padx=6)
        self.only_fill_empty_var = tk.BooleanVar(value=self.settings.only_fill_empty)
        ttk.Checkbutton(tools, text="Only fill empty fields", variable=self.only_fill_empty_var).pack(
            side=tk.LEFT, padx=6)
        self._action(tools, "Add details & examples", self.on_enrich, accent=True).pack(side=tk.LEFT, padx=6)

        table = ttk.Frame(right)
        table.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended")
        for key, heading, width in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="w", stretch=key in ("meaning", "example", "example_en"))
        self.tree.tag_configure("english", foreground="#e0a040")
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_editor())
        self.tree.bind("<Delete>", lambda e: self.on_delete_selected())
        self.tree.bind("<Double-1>", lambda e: self.editor_fields["term"].focus_set())
        # ttkbootstrap builds the Treeview style when the first one is created, so set this after
        ttk.Style().configure("Treeview", rowheight=28)

        under = ttk.Frame(right)
        under.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(under, text="Select a row to edit it below · Delete key removes selected rows",
                  style="Hint.TLabel").pack(side=tk.LEFT)
        self._action(under, "Clear table", self.on_clear).pack(side=tk.RIGHT)
        self._action(under, "Delete selected", self.on_delete_selected).pack(side=tk.RIGHT, padx=6)

        self._build_editor(right)
        self._build_export(right)

    def _build_editor(self, right):
        box = ttk.LabelFrame(right, text="Selected row", padding=8)
        box.pack(fill=tk.X, pady=(8, 0))
        self.editor_fields: dict[str, tk.Widget] = {}
        singles = [("term", "Term"), ("reading", "Reading"), ("jlpt", "JLPT")]
        for col, (key, label) in enumerate(singles):
            ttk.Label(box, text=label).grid(row=0, column=col * 2, sticky="w", padx=(0, 4))
            entry = ttk.Entry(box, width=8 if key == "jlpt" else 18)
            entry.grid(row=0, column=col * 2 + 1, sticky="w", padx=(0, 12), pady=2)
            self.editor_fields[key] = entry
        for r, (key, label) in enumerate([("meaning", "Meaning"), ("example", "Example"),
                                          ("example_en", "Example (EN)")], start=1):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="nw", pady=2)
            if key == "meaning":
                widget = ttk.Entry(box)
            else:
                widget = tk.Text(box, height=2, wrap="word")
            widget.grid(row=r, column=1, columnspan=6, sticky="we", pady=2)
            self.editor_fields[key] = widget
        box.columnconfigure(6, weight=1)
        btns = ttk.Frame(box)
        btns.grid(row=4, column=0, columnspan=7, sticky="e", pady=(4, 0))
        self._action(btns, "Save changes", self.on_save_row, accent=True).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Revert", command=self._load_editor).pack(side=tk.RIGHT, padx=6)

    def _build_export(self, right):
        box = ttk.Frame(right)
        box.pack(fill=tk.X, pady=10)
        ttk.Label(box, text="3  Export", style="Step.TLabel").pack(side=tk.LEFT, padx=(0, 12))
        self._action(box, "Make Anki deck…", self.on_make_anki, accent=True).pack(side=tk.LEFT)
        self.reverse_var = tk.BooleanVar(value=self.settings.reverse_cards)
        ttk.Checkbutton(box, text="Also English → Japanese cards", variable=self.reverse_var).pack(
            side=tk.LEFT, padx=8)
        self._action(box, "Save CSV…", self.on_save_csv).pack(side=tk.RIGHT)
        self._action(box, "Write to Google Sheet", self.on_write_sheet).pack(side=tk.RIGHT, padx=6)

    # ================================================================ table + editor

    def refresh_table(self, select: list[int] | None = None):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows):
            values = [getattr(row, key) for key, _, _ in COLUMNS]
            self.tree.insert("", "end", iid=str(i), values=values,
                             tags=("english",) if row.is_english else ())
        english = sum(r.is_english for r in self.rows)
        text = f"{len(self.rows)} rows"
        if english:
            text += f"  ·  {english} English (orange) — translate them on the English tab"
        self.count_label.config(text=text)
        if select:
            ids = [str(i) for i in select if 0 <= i < len(self.rows)]
            self.tree.selection_set(ids)
            if ids:
                self.tree.see(ids[0])
        self._load_editor()

    def _selected(self) -> list[int]:
        return sorted(int(i) for i in self.tree.selection())

    def _set_field(self, key: str, value: str):
        widget = self.editor_fields[key]
        if isinstance(widget, tk.Text):
            widget.delete("1.0", "end")
            widget.insert("1.0", value)
        else:
            widget.delete(0, "end")
            widget.insert(0, value)

    def _get_field(self, key: str) -> str:
        widget = self.editor_fields[key]
        if isinstance(widget, tk.Text):
            return widget.get("1.0", "end-1c")
        return widget.get()

    def _load_editor(self):
        sel = self._selected()
        row = self.rows[sel[0]] if len(sel) == 1 else Row()
        for key in self.editor_fields:
            self._set_field(key, getattr(row, key))

    def on_save_row(self):
        sel = self._selected()
        if len(sel) != 1:
            messagebox.showinfo("Select one row", "Select a single row in the table to edit it.")
            return
        self.rows[sel[0]] = Row(**{key: self._get_field(key) for key in self.editor_fields})
        self.refresh_table(select=sel)
        self.set_status("Row saved.")

    def on_delete_selected(self):
        if self._task:
            return
        sel = set(self._selected())
        if not sel:
            return
        self.rows = [r for i, r in enumerate(self.rows) if i not in sel]
        self.refresh_table()
        self.set_status(f"Deleted {len(sel)} rows.")

    def on_clear(self):
        if self.rows and not messagebox.askyesno("Clear table", f"Remove all {len(self.rows)} rows?"):
            return
        self.rows = []
        self.refresh_table()
        self.set_status("Table cleared.")

    def _add_rows(self, new: list[Row], what: str):
        if self.append_var.get():
            self.rows, added, updated = merge_rows(self.rows, new)
            msg = f"{what}: {added} new rows, {updated} existing rows filled in."
        else:
            self.rows = list(new)
            msg = f"{what}: table replaced with {len(new)} rows."
        self.refresh_table()
        self.set_status(msg)
        self.log(msg)

    # ================================================================ background work

    def run_task(self, label: str, work, on_done):
        """Run work(task) on a thread; on_done(result) runs on the Tk thread afterwards."""
        if self._task:
            return
        task = pipeline.Task(
            on_progress=lambda done, total: self._events.put(("progress", done, total)),
            on_log=lambda msg: self._events.put(("log", msg)),
        )
        self._task = task
        for w in self._busy_widgets:
            w.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress["value"] = 0
        self.set_status(label + "…")
        self.log(label + "…")

        def runner():
            try:
                self._events.put(("done", on_done, work(task), None))
            except Exception as e:  # reported on the Tk thread
                self._events.put(("done", on_done, None, e))

        threading.Thread(target=runner, daemon=True).start()

    def _drain_events(self):
        if self._closing:
            return
        try:
            while True:
                kind, *rest = self._events.get_nowait()
                if kind == "progress":
                    done, total = rest
                    self.progress["value"] = 100 * done / max(1, total)
                elif kind == "log":
                    self.log(rest[0])
                elif kind == "done":
                    self._finish_task(*rest)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _finish_task(self, on_done, result, error):
        self._task = None
        for w in self._busy_widgets:
            w.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.progress["value"] = 0
        if error is not None:
            self.set_status("Failed — see the activity log (Ctrl+L).")
            self.log(f"Error: {type(error).__name__}: {error}")
            messagebox.showerror("Something went wrong", str(error)[:800])
            return
        on_done(result)

    def on_cancel(self):
        if self._task:
            self._task.cancel()
            self.set_status("Stopping after the current step…")

    # ================================================================ step 1: inputs

    def on_parse(self):
        rows = parse_blob(self.list_text.get("1.0", "end-1c"))
        if not rows:
            messagebox.showwarning("Nothing found", "No entries found. Expected lines like: 語（ご） word")
            return
        self._add_rows(rows, "Vocab list")

    def on_translate(self):
        terms = split_terms(self.english_text.get("1.0", "end-1c"))
        if terms:
            def done(found):
                rows = [r for r in found if r]
                self._add_rows(rows, f"Translated {len(rows)}/{len(terms)}")
            self.run_task(f"Translating {len(terms)} English terms",
                          lambda task: pipeline.translate_english(terms, task), done)
            return
        english = sum(r.is_english for r in self.rows)
        if not english:
            messagebox.showinfo("Nothing to translate",
                                "Paste English words here, or read an English list from your Google Sheet.")
            return

        def done(result):
            self.rows, translated, failed = result
            self.refresh_table()
            msg = f"Translated {translated} English rows" + (f"; {failed} failed." if failed else ".")
            self.set_status(msg)
            self.log(msg)
        rows = list(self.rows)
        self.run_task(f"Translating {english} English rows in the table",
                      lambda task: pipeline.translate_english_rows(rows, task), done)

    def on_extract(self):
        text = self.article_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("No text", "Paste some Japanese text first.")
            return
        max_words = int(self.max_words_var.get() or 40)
        self.run_task("Extracting words",
                      lambda task: pipeline.extract_from_text(text, max_words),
                      lambda rows: self._add_rows(rows, "Article"))

    def on_topic(self):
        topic = self.topic_var.get().strip()
        if not topic:
            messagebox.showinfo("Topic needed", "Type a topic, e.g. 空港 or airport.")
            return
        count = int(self.topic_count_var.get() or 30)
        avoid = {r.term for r in self.rows}
        self.run_task(f"Generating {count} words for '{topic}'",
                      lambda task: pipeline.generate_topic_rows(topic, count, avoid, task),
                      lambda rows: self._add_rows(rows, f"Topic '{topic}'"))

    def _sheet_ready(self) -> bool:
        if self.settings.sheet_id and self.settings.tab:
            return True
        messagebox.showinfo("Google Sheet not set up", "Add your Sheet ID and tab name first.")
        self.open_settings()
        return bool(self.settings.sheet_id and self.settings.tab)

    def on_read_sheet(self):
        if not self._sheet_ready():
            return
        s = self.settings

        def work(task):
            svc = sheets.get_service(s.resolved_service_account())
            return sheets.read_from_sheet(svc, s.sheet_id, s.tab)
        self.run_task(f"Reading '{s.tab}'", work, lambda rows: self._add_rows(rows, f"Sheet '{s.tab}'"))

    # ================================================================ step 2: enrich

    def on_enrich(self, english_checked: bool = False):
        if not self.rows:
            messagebox.showinfo("Nothing to fill in", "Add some words first (step 1).")
            return
        english = sum(r.is_english for r in self.rows)
        if english and not english_checked:
            answer = messagebox.askyesnocancel(
                "English rows",
                f"{english} rows are English. Translate them to Japanese first?\n\n"
                "Yes: translate, then fill in.   No: skip them.")
            if answer is None:
                return
            if answer:
                rows = list(self.rows)

                def translated(result):
                    self.rows = result[0]
                    self.refresh_table()
                    self.on_enrich(english_checked=True)
                self.run_task(f"Translating {english} English rows",
                              lambda task: pipeline.translate_english_rows(rows, task), translated)
                return

        mode = SOURCES[self.source_var.get()]
        only_empty = self.only_fill_empty_var.get()
        rows = list(self.rows)

        def done(result):
            self.rows, summary = result
            self.refresh_table(select=self._selected())
            self.set_status(summary)
            self.log(summary)
        self.run_task(f"Filling in {len(rows)} rows ({self.source_var.get()})",
                      lambda task: pipeline.enrich(rows, mode, only_empty, task), done)

    # ================================================================ step 3: export

    def on_make_anki(self):
        if not self.rows:
            messagebox.showinfo("Nothing to export", "Add some words first.")
            return
        name = simpledialog.askstring("Deck name", "Anki deck name:", parent=self,
                                      initialvalue=f"JP Vocab ({datetime.today():%Y-%m-%d})")
        if not name:
            return
        path = filedialog.asksaveasfilename(title="Save Anki deck", defaultextension=".apkg",
                                            initialfile=f"{name}.apkg",
                                            filetypes=[("Anki package", "*.apkg")])
        if not path:
            return
        deck = make_anki_deck(self.rows, name, reverse_cards=self.reverse_var.get())
        genanki.Package(deck).write_to_file(path)
        self.set_status(f"Anki deck saved: {path}")
        self.log(f"Anki deck saved: {path} ({len(deck.notes)} notes)")

    def on_save_csv(self):
        if not self.rows:
            messagebox.showinfo("Nothing to save", "Add some words first.")
            return
        path = filedialog.asksaveasfilename(title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(STORAGE_HEADERS)
            writer.writerows(r.to_list() for r in self.rows)
        self.set_status(f"Saved {len(self.rows)} rows to {path}")

    def on_open_csv(self):
        if self._task:
            return
        path = filedialog.askopenfilename(title="Open CSV", filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        with open(path, encoding="utf-8-sig", newline="") as f:
            records = list(csv.reader(f))
        if records and records[0] and records[0][0].strip().lower() == "term":
            records = records[1:]
        rows = [r for r in (Row.from_list(rec) for rec in records) if not r.is_empty]
        self._add_rows(rows, "CSV")

    def _current_input_text(self) -> str:
        """Raw text of the selected input tab, for the Sheet backup."""
        boxes = {0: self.list_text, 1: self.english_text, 2: self.article_text}
        box = boxes.get(self.inputs.index(self.inputs.select()))
        return box.get("1.0", "end-1c").strip() if box else ""

    def on_write_sheet(self):
        if not self.rows or not self._sheet_ready():
            return
        s = self.settings
        if not messagebox.askyesno("Write to Google Sheet",
                                   f"Replace the contents of '{s.tab}' with {len(self.rows)} rows?"):
            return
        rows = list(self.rows)
        raw = self._current_input_text() if s.backup_raw else ""

        def work(task):
            svc = sheets.get_service(s.resolved_service_account())
            if raw:
                sheets.backup_raw(svc, s.sheet_id, s.backup_tab, raw)
            sheets.write_to_sheet(svc, s.sheet_id, s.tab, rows)
            return len(rows)
        self.run_task(f"Writing to '{s.tab}'", work,
                      lambda n: self.set_status(f"Wrote {n} rows to '{s.tab}'."))

    # ================================================================ settings + log

    def _update_sheet_label(self):
        s = self.settings
        if s.sheet_id:
            text = f"Sheet: …{s.sheet_id[-10:]}\nTab: {s.tab}"
        else:
            text = "No Google Sheet set up yet."
        self.sheet_label.config(text=text)

    def open_settings(self):
        SettingsDialog(self, self.settings)
        self._update_sheet_label()

    def show_log(self):
        if self._log_view and self._log_view.winfo_exists():
            self._log_view.winfo_toplevel().lift()
            return
        win = tk.Toplevel(self)
        win.title("Activity log")
        win.geometry("760x360")
        self._log_view = scrolledtext.ScrolledText(win, wrap="word")
        self._log_view.pack(fill=tk.BOTH, expand=True)
        self._log_view.insert("end", "".join(self._log_lines))
        self._log_view.see("end")

    def log(self, msg: str):
        line = f"[{datetime.now():%H:%M:%S}] {msg}\n"
        print(line, end="", flush=True)
        self._log_lines.append(line)
        if self._log_view and self._log_view.winfo_exists():
            self._log_view.insert("end", line)
            self._log_view.see("end")

    def set_status(self, text: str):
        self.status.config(text=text)

    def _save_preferences(self):
        s = self.settings
        s.example_source = SOURCES[self.source_var.get()]
        s.only_fill_empty = self.only_fill_empty_var.get()
        s.reverse_cards = self.reverse_var.get()
        s.append = self.append_var.get()
        try:
            s.save()
        except OSError:
            pass

    def _on_close(self):
        self._closing = True
        self._save_preferences()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """Modal editor for the Google Sheet settings."""

    def __init__(self, app: App, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self.title("Google Sheet settings")
        self.transient(app)
        self.resizable(True, False)
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)

        self.vars = {
            "service_account": tk.StringVar(value=settings.resolved_service_account()),
            "sheet_id": tk.StringVar(value=settings.sheet_id),
            "tab": tk.StringVar(value=settings.tab),
            "backup_tab": tk.StringVar(value=settings.backup_tab),
        }
        self.backup_var = tk.BooleanVar(value=settings.backup_raw)
        fields = [
            ("service_account", "Service account JSON"),
            ("sheet_id", "Sheet ID or URL"),
            ("tab", "Tab name"),
            ("backup_tab", "Backup tab"),
        ]
        for r, (key, label) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[key], width=60).grid(row=r, column=1, sticky="we", padx=6)
        ttk.Button(body, text="Browse…", command=self._browse).grid(row=0, column=2)
        ttk.Checkbutton(body, text="Back up the raw input text when writing", variable=self.backup_var).grid(
            row=len(fields), column=1, sticky="w", pady=6)
        ttk.Label(body, style="Hint.TLabel", wraplength=520, justify="left",
                  text="Keep the key file in the project's secrets/ folder (it's gitignored). "
                       "Share the Sheet with the client_email inside that file.").grid(
            row=len(fields) + 1, column=0, columnspan=3, sticky="w")
        body.columnconfigure(1, weight=1)
        btns = ttk.Frame(body)
        btns.grid(row=len(fields) + 2, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=6)

        self.grab_set()
        self.wait_window(self)

    def _browse(self):
        path = filedialog.askopenfilename(parent=self, title="Service account JSON",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.vars["service_account"].set(path)

    def _save(self):
        s = self.settings
        sheet = self.vars["sheet_id"].get().strip()
        m = SHEET_ID_RE.search(sheet)
        s.sheet_id = m.group(1) if m else sheet
        s.service_account = self.vars["service_account"].get().strip()
        s.tab = self.vars["tab"].get().strip()
        s.backup_tab = self.vars["backup_tab"].get().strip() or "Raw_Backup"
        s.backup_raw = self.backup_var.get()
        try:
            s.save()
        except OSError as e:
            messagebox.showwarning("Not saved", f"Couldn't save settings: {e}", parent=self)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
