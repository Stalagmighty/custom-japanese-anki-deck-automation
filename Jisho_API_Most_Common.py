import time, requests
from urllib.parse import quote_plus

API = "https://jisho.org/api/v1/search/words?keyword={q}&page={p}"

POS_WHITELIST = {
    "i-adjective (keiyoushi)",
    "na-adjective (keiyodoshi)",
    "pre-noun adjectival (rentaishi)",
    "adjectival noun or quasi-adjective (keiyodoshi)",  # legacy label
}

def jisho_search(keyword, page=1, timeout=15):
    r = requests.get(API.format(q=quote_plus(keyword), p=page), timeout=timeout)
    r.raise_for_status()
    return r.json()

def is_adjective_item(item) -> bool:
    for sense in item.get("senses", []):
        for pos in sense.get("parts_of_speech", []):
            if pos.lower() in {p.lower() for p in POS_WHITELIST}:
                return True
    return False

def fetch_adjectives_batch(jobs, common_only=True, delay=0.0, start_page=1):
    total = sum(max(pages - start_page + 1, 0) for _, pages in jobs)
    done = 0
    start = time.time()
    lines, seen = [], set()

    for keyword, pages in jobs:
        for page in range(start_page, pages + 1):
            done += 1
            elapsed = time.time() - start
            eta = (total - done) * (elapsed / max(done, 1))
            print(f"[{done}/{total}] {keyword!r} p{page}  elapsed {elapsed:.1f}s  ETA {eta:.1f}s")

            try:
                items = jisho_search(keyword, page).get("data", []) or []
            except Exception as e:
                print(f"  ! error: {e}")
                continue

            if not items:
                print("  (no items)")
                continue

            for item in items:
                if common_only and not item.get("is_common"):
                    continue
                if not is_adjective_item(item):
                    continue
                jap = (item.get("japanese") or [{}])[0]
                word = jap.get("word") or jap.get("reading") or ""
                reading = jap.get("reading") or ""
                sense0 = (item.get("senses") or [{}])[0]
                meaning = "; ".join(sense0.get("english_definitions", []))
                line = f"{word}（{reading}） {meaning}"
                if word and reading and meaning and line not in seen:
                    seen.add(line)
                    lines.append(line)

            if delay:
                time.sleep(delay)

    return lines


if __name__ == "__main__":
    JOBS = [
        ("*い", 10),    # up to page 10
        ("*しい", 10),   # up to page 5
    ]
    results = fetch_adjectives_batch(JOBS, common_only=True, delay=0.0, start_page=1)
    with open("common_adjectives.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(results))


    out = "common_adjectives.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(results))
    print(f"\nWrote {len(results)} lines to {out}. Tip: set common_only=False for a bigger list.")
