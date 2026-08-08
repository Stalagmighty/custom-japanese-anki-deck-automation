import requests
from urllib.parse import quote_plus
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv

# --------------------- Jisho API helpers ---------------------

def jisho_search(keyword: str, page: int = 1):
    url = f"https://jisho.org/api/v1/search/words?keyword={quote_plus(keyword)}&page={page}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

POS_I_ADJ = "i-adjective (keiyoushi)"
POS_NA_ADJ = "na-adjective (keiyodoshi)"
POS_PRENOUN = "pre-noun adjectival (rentaishi)"

def passes_pos_filter(item, want_i, want_na, want_prenoun):
    """Return True if the item's senses include at least one POS the user asked for.
       If no POS box is ticked, treat as 'no filtering'."""
    if not (want_i or want_na or want_prenoun):
        return True
    target = set()
    if want_i: target.add(POS_I_ADJ)
    if want_na: target.add(POS_NA_ADJ)
    if want_prenoun: target.add(POS_PRENOUN)

    # Jisho returns POS strings with specific casing; compare case‑insensitively.
    target_lower = {p.lower() for p in target}

    for sense in item.get("senses", []):
        for pos in sense.get("parts_of_speech", []):
            if pos.lower() in target_lower:
                return True
    return False

def fetch_words(keyword: str, common_only: bool, max_pages: int,
                want_i: bool, want_na: bool, want_prenoun: bool):
    """Fetch and filter results. Returns list of dict rows."""
    results = []
    for page in range(1, max_pages + 1):
        data = jisho_search(keyword, page)
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            if common_only and not item.get("is_common"):
                continue
            if not passes_pos_filter(item, want_i, want_na, want_prenoun):
                continue

            jap = item["japanese"][0]
            word = jap.get("word") or jap.get("reading")
            reading = jap.get("reading") or ""
            # Take the first sense for a concise line; you could join all if you prefer.
            senses0 = item["senses"][0]
            meaning = "; ".join(senses0.get("english_definitions", []))
            pos = ", ".join(senses0.get("parts_of_speech", []))
            jlpt = ", ".join(item.get("jlpt", [])) or ""
            results.append({
                "word": word,
                "reading": reading,
                "meaning": meaning,
                "pos": pos,
                "jlpt": jlpt,
            })
    return results

def to_legacy_text(rows, sep="\n"):
    """漢字（かな） meaning; meaning…  — for your existing parser."""
    parts = []
    for r in rows:
        word = r.get("word") or r.get("reading") or ""
        reading = r.get("reading") or ""
        meaning = r.get("meaning") or ""
        parts.append(f"{word}（{reading}） {meaning}")
    return sep.join(parts)

# --------------------- Tkinter GUI ---------------------

class JishoGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jisho Search")
        self.geometry("820x540")
        self.minsize(720, 480)

        # Top controls
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Query (supports * and ?):").grid(row=0, column=0, sticky="w")
        self.query_var = tk.StringVar(value="*的")
        self.query_entry = ttk.Entry(top, textvariable=self.query_var, width=36)
        self.query_entry.grid(row=0, column=1, sticky="we", padx=(6, 10))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Pages:").grid(row=0, column=2, sticky="e")
        self.pages_var = tk.StringVar(value="2")
        ttk.Entry(top, textvariable=self.pages_var, width=4).grid(row=0, column=3, padx=(6, 12))

        self.common_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Common only", variable=self.common_var).grid(row=0, column=4)

        # POS filter row
        posf = ttk.Frame(self, padding=(10, 0, 10, 6))
        posf.pack(fill="x")
        ttk.Label(posf, text="Filter POS:").grid(row=0, column=0, sticky="w")
        self.i_var = tk.BooleanVar(value=False)
        self.na_var = tk.BooleanVar(value=False)
        self.prenoun_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(posf, text="i‑adj (形容詞)", variable=self.i_var).grid(row=0, column=1, padx=(6, 0))
        ttk.Checkbutton(posf, text="na‑adj (形容動詞)", variable=self.na_var).grid(row=0, column=2, padx=(6, 0))
        ttk.Checkbutton(posf, text="pre‑noun (連体詞)", variable=self.prenoun_var).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(posf, text="(leave all unticked to show any POS)").grid(row=0, column=4, padx=10, sticky="w")

        # Buttons
        btns = ttk.Frame(self, padding=(10, 0, 10, 6))
        btns.pack(fill="x")
        ttk.Button(btns, text="Search", command=self.run_search).pack(side="left")
        ttk.Button(btns, text="Save .txt", command=self.save_txt).pack(side="left", padx=6)
        ttk.Button(btns, text="Save .csv", command=self.save_csv).pack(side="left")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(btns, textvariable=self.status).pack(side="right")

        # Output area
        out_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        out_frame.pack(fill="both", expand=True)
        self.output = tk.Text(out_frame, wrap="word")
        self.output.pack(fill="both", expand=True)
        self.output.configure(font=("Consolas", 11))

    def run_search(self):
        q = self.query_var.get().strip()
        if not q:
            messagebox.showwarning("Missing", "Please enter a query.")
            return
        try:
            pages = int(self.pages_var.get())
            if pages < 1: raise ValueError
        except ValueError:
            messagebox.showwarning("Pages", "Pages must be a positive integer.")
            return

        self.status.set("Searching…")
        self.update_idletasks()
        try:
            rows = fetch_words(
                keyword=q,
                common_only=self.common_var.get(),
                max_pages=pages,
                want_i=self.i_var.get(),
                want_na=self.na_var.get(),
                want_prenoun=self.prenoun_var.get(),
            )
            self.rows = rows  # store for saving
            text = to_legacy_text(rows, sep="\n")
            self.output.delete("1.0", tk.END)
            self.output.insert(tk.END, text)
            self.status.set(f"{len(rows)} results.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status.set("Error.")

    def save_txt(self):
        if not hasattr(self, "rows") or not self.rows:
            messagebox.showinfo("Nothing to save", "Run a search first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                                            initialfile="jisho_results.txt")
        if not path: return
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_legacy_text(self.rows, sep="\n"))
        messagebox.showinfo("Saved", f"Saved to:\n{path}")

    def save_csv(self):
        if not hasattr(self, "rows") or not self.rows:
            messagebox.showinfo("Nothing to save", "Run a search first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                                            initialfile="jisho_results.csv")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["word", "reading", "meaning", "pos", "jlpt"])
            writer.writeheader()
            writer.writerows(self.rows)
        messagebox.showinfo("Saved", f"Saved to:\n{path}")

if __name__ == "__main__":
    JishoGUI().mainloop()
