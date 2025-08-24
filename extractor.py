# extractor.py
# ------------------------------------------------------
# Unstructured JP text → ranked vocab/phrases extractor
# Returns (term, reading) pairs you can drop into your table.
# ------------------------------------------------------
from __future__ import annotations
from collections import Counter, defaultdict
from typing import List, Tuple
from fugashi import Tagger

# ----- lazy Tagger (unidic-lite) -----
_TOK: Tagger | None = None
def _get_tagger() -> Tagger:
    global _TOK
    if _TOK is None:
        _TOK = Tagger()
    return _TOK

# ----- helpers -----
_PUNCT = set("。．、，・！？!?（）()［］[]{}：；「」『』…—- \n\t\r")
_STOP_TERMS = {
    "する","ある","いる","こと","もの","これ","それ","あれ","ため","よう",
    "さん","できる","なる","及び","また","など","ようだ","ように","そして",
}

def katakana_to_hiragana(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        out.append(chr(o - 0x60) if 0x30A1 <= o <= 0x30F6 else ch)
    return "".join(out)

def _tok(node):
    f = node.feature
    surface = node.surface

    # use attributes instead of .get()
    lemma = getattr(f, "lemma", None) or surface
    pos = getattr(f, "pos1", None) or getattr(f, "pos", "")
    if pos:
        pos = pos.split("-")[0]  # 名詞-普通名詞 → 名詞

    reading = getattr(f, "reading", None) or getattr(f, "orth", None) or surface
    reading = katakana_to_hiragana(reading)

    return surface, lemma, pos, reading


def _score_phrase(items: List[Tuple[str, str]]) -> Tuple[str, str, float]:
    term = "".join(s for s, _ in items)
    reading = "".join(r for _, r in items)
    score = 2.0 + 0.4 * len(items) + min(2, len(term) // 3)
    return term, reading, score

# ----- public API -----
def extract_candidates_from_japanese_text(
    text: str,
    *,
    top_k: int = 80,
    min_freq: int = 2,
    allow_phrases: bool = True,
    max_ngram_len: int = 3,
) -> List[Tuple[str, str]]:
    """
    Parse raw JP text and return ranked (term, reading).
    - Words: 名詞/動詞/形容詞  (verbs/adjectives use lemma as the headword)
    - Phrases (optional): noun compounds, Adj+Noun, short n-grams
    """
    tagger = _get_tagger()
    toks = list(tagger(text))
    if not toks:
        return []

    # ---- single words ----
    singles = []
    for t in toks:
        surf, lemma, pos, read = _tok(t)
        if surf in _PUNCT:
            continue
        if pos in ("名詞", "動詞", "形容詞"):
            head = lemma if pos in ("動詞", "形容詞") else surf
            if head and head not in _STOP_TERMS:
                singles.append((head, read, pos))

    # freq + best reading
    from collections import Counter, defaultdict
    freq = Counter([w for w, _, _ in singles])
    readings = defaultdict(Counter)
    for w, r, _ in singles:
        readings[w][r] += 1
    best_reading = {w: cnt.most_common(1)[0][0] for w, cnt in readings.items()}

    ranked = []
    for w, c in freq.items():
        if c < min_freq:
            continue
        score = c + min(2, len(w) // 2)  # simple freq+length boost
        ranked.append((w, best_reading[w], score))
    ranked.sort(key=lambda x: x[2], reverse=True)

    # ---- phrases ----
    phrase_ranked = []
    if allow_phrases:
        seq = [_tok(t) for t in toks if t.surface not in _PUNCT]

        # noun compounds
        run = []
        for surf, lemma, pos, read in seq:
            if pos == "名詞" and surf not in _PUNCT:
                run.append((surf, read))
            else:
                if len(run) >= 2:
                    phrase_ranked.append(_score_phrase(run))
                run = []
        if len(run) >= 2:
            phrase_ranked.append(_score_phrase(run))

        # Adj + Noun
        for i in range(len(seq) - 1):
            s1, l1, p1, r1 = seq[i]
            s2, l2, p2, r2 = seq[i + 1]
            if p1 == "形容詞" and p2 == "名詞":
                phrase_ranked.append(_score_phrase([(s1, r1), (s2, r2)]))

        # n-grams (mostly nouns)
        for n in range(2, max(2, max_ngram_len) + 1):
            for i in range(len(seq) - n + 1):
                chunk = seq[i : i + n]
                if any(s in _PUNCT for s, _, _, _ in chunk):
                    continue
                if sum(1 for _, _, p, _ in chunk if p == "名詞") >= 2:
                    phrase_ranked.append(_score_phrase([(s, r) for s, _, _, r in chunk]))

    # ---- combine & trim ----
    seen = set()
    combined = []
    for t, r, sc in ranked:
        if t not in seen:
            seen.add(t)
            combined.append((t, r, sc))
    for t, r, sc in phrase_ranked:
        if not t or t in _STOP_TERMS:
            continue
        if t not in seen:
            seen.add(t)
            combined.append((t, r, sc + 0.5))  # tiny phrase bonus

    combined.sort(key=lambda x: x[2], reverse=True)

    out: List[Tuple[str, str]] = []
    for t, r, _ in combined:
        if len(t) >= 2:
            out.append((t, r))
        if len(out) >= top_k:
            break
    return out

def build_rows_from_text(
    text: str,
    *,
    top_k: int = 80,
    min_freq: int = 2,
    allow_phrases: bool = True,
    max_ngram_len: int = 3,
) -> List[List[str]]:
    """
    Convenience: returns rows [Term, Reading, Meaning, Example, JLPT]
    (Meaning/Example/JLPT left empty for downstream enrichment.)
    """
    pairs = extract_candidates_from_japanese_text(
        text,
        top_k=top_k,
        min_freq=min_freq,
        allow_phrases=allow_phrases,
        max_ngram_len=max_ngram_len,
    )
    return [[t, r, "", "", ""] for t, r in pairs]

if __name__ == "__main__":
    # quick smoke test
    sample = "日本の文化は地域によって多様で、伝統芸能や祭りが各地で行われています。新幹線は主要都市を結び、経済活動の中心として機能しています。"
    rows = build_rows_from_text(sample, top_k=10, min_freq=1)
    for r in rows:
        print(r[:2])
