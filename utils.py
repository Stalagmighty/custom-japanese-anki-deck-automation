# utils.py
from typing import List, Tuple, Set

def _norm(s: str) -> str:
    return (s or "").strip()

def _norm_jlpt(s: str) -> str:
    s = _norm(s).upper().replace(" ", "")
    # Normalise things like "n-2", "JLPT N2", "n２"
    s = s.replace("JLPT", "")
    s = s.replace("Ｎ", "N")  # full-width to ASCII just for N
    return s if s in {"N5","N4","N3","N2","N1"} else ""

def merge_rows(
    existing: List[List[str]],
    incoming: List[List[str]],
    *,
    fill_blank_only: bool = True,
    prefer_incoming: bool = False,
) -> Tuple[List[List[str]], int, int, Set[tuple]]:
    """
    Merge 5-column rows [term, reading, meaning, example, jlpt] by (term, reading).

    Params:
      fill_blank_only  -> if True, only fill empty existing fields from incoming.
      prefer_incoming  -> if True, incoming fields overwrite existing (wins ties).
                          (Ignored if fill_blank_only=True)

    Returns:
      merged_rows, added_count, updated_count, added_keys
    """
    # Make shallow copies and ensure 5 columns
    def _canon_row(r):
        base = (r + ["", "", "", "", ""])[:5]
        return [ _norm(base[0]),
                 _norm(base[1]),
                 _norm(base[2]),
                 _norm(base[3]),
                 _norm_jlpt(base[4]) ]

    merged: List[List[str]] = []
    index: dict[tuple, int] = {}
    added = updated = 0
    added_keys: Set[tuple] = set()

    # Seed with existing
    for i, r in enumerate(existing or []):
        row = _canon_row(r)
        merged.append(row)
        term, reading = row[0], row[1]
        if term:
            index[(term, reading)] = i

    # Merge incoming
    for r in incoming or []:
        row_in = _canon_row(r)
        term, reading, meaning, example, jlpt = row_in
        if not term:
            continue
        key = (term, reading)

        if key in index:
            i = index[key]
            old = merged[i]
            if fill_blank_only:
                new = [
                    old[0] or term,
                    old[1] or reading,
                    old[2] or meaning,
                    old[3] or example,
                    old[4] or jlpt,
                ]
            elif prefer_incoming:
                new = [
                    term or old[0],
                    reading or old[1],
                    meaning or old[2],
                    example or old[3],
                    jlpt or old[4],
                ]
            else:
                # default: keep existing unless incoming is non-empty & different
                new = [
                    term if (term and term != old[0]) else old[0],
                    reading if (reading and reading != old[1]) else old[1],
                    meaning if (meaning and meaning != old[2]) else old[2],
                    example if (example and example != old[3]) else old[3],
                    jlpt if (jlpt and jlpt != old[4]) else old[4],
                ]

            if new != old:
                merged[i] = new
                updated += 1
        else:
            merged.append(row_in)
            idx = len(merged) - 1
            index[key] = idx
            added += 1
            added_keys.add(key)

    return merged, added, updated, added_keys
