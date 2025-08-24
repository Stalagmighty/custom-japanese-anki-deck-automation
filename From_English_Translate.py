import json, re, time, os
from typing import List
from openai import OpenAI

_client = None
def _client_once():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

def _extract_json_block(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"```json\s*(.+?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    a, b = text.find("["), text.rfind("]")
    if a != -1 and b != -1 and b > a:
        return text[a:b+1]
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b != -1 and b > a:
        return text[a:b+1]
    return text.strip()

def _chunk(seq, n):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf

def translate_english_terms_batch(
    english_terms: List[str],
    *,
    model: str = "gpt-4o-mini",
    batch_size: int = 25,
    retries: int = 2,
) -> List[List[str]]:
    """
    Returns rows: [term, reading, meaning, example, jlpt]
    """
    terms = [t.strip() for t in (english_terms or []) if t and t.strip()]
    if not terms:
        return []

    client = _client_once()
    out: List[List[str]] = []

    sys = (
        "You are a careful bilingual lexicographer and Japanese sentence writer.\n"
        "Given English seed terms, produce ONE Japanese vocabulary entry per seed as JSON.\n"
        "Fields:\n"
        "- term: Japanese headword (kanji or kana; NEVER the English seed)\n"
        "- reading: kana (hiragana/katakana)\n"
        "- meaning: may start with the original English seed in [brackets], then concise English gloss(es)\n"
        "- example: one natural Japanese sentence (~60–110 chars) that MUST include the exact JP term; no translations\n"
        "- jlpt: one of N5,N4,N3,N2,N1 or empty if unknown\n"
        "Constraints:\n"
        "- Output STRICT JSON only (no prose, no code fences).\n"
        "- Prefer the common Japanese equivalent over katakana unless the loanword is standard.\n"
        "- Keep register natural and context‑rich (news/academic/business if appropriate).\n"
        "- No duplicates within a batch.\n"
    )

    for group in _chunk(terms, batch_size):
        user_payload = {
            "instructions": (
                "Return STRICT JSON exactly like:\n"
                "{\"items\":[{"
                "\"term\":\"JP headword (kanji or kana)\","
                "\"reading\":\"kana\","
                "\"meaning\":\"[<seed>] concise English gloss(es)\","
                "\"example\":\"One Japanese sentence (~60–110 chars) that includes the exact JP term; no English\","
                "\"jlpt\":\"N5|N4|N3|N2|N1 or empty\""
                "} ... ]}\n"
                "Rules:\n"
                "- Produce exactly one item for each input seed, in the same order.\n"
                "- term MUST be Japanese; never output the English seed as the term.\n"
                "- Prefer native/common equivalents over katakana unless the loanword is standard.\n"
                "- example MUST be fully in Japanese and contain the exact term string.\n"
                "- reading MUST be kana.\n"
            ),
            "seeds": group,  # <-- use the current chunk, not the whole list
        }

        last_err = None
        raw_reply = ""
        for attempt in range(retries + 1):
            try:
                if model.startswith("gpt-5"):
                    resp = client.responses.create(
                        model=model,
                        input=[
                            {"role": "system", "content": sys},
                            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                        ],
                    )
                    raw_reply = getattr(resp, "output_text", "") or ""
                else:
                    resp = client.chat.completions.create(
                        model=model,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": "Respond ONLY with valid JSON."},
                            {"role": "user", "content":
                                "Produce an object {\"items\": [...]} where each element has "
                                "term, reading, meaning, example, jlpt. "
                                + json.dumps(user_payload, ensure_ascii=False)
                            },
                        ],
                        max_tokens=1200,
                        temperature=0.2,
                    )
                    raw_reply = (resp.choices[0].message.content or "").strip()

                # ---- Parse ----
                block = _extract_json_block(raw_reply)
                if not block:
                    raise ValueError("Empty reply")

                data = json.loads(block)  # DO NOT mutate the JSON string before this
                items = data.get("items", data) if isinstance(data, dict) else data
                if not isinstance(items, list):
                    items = [items]

                before = len(out)
                for item in items or []:
                    term    = (item.get("term") or "").strip()
                    reading = (item.get("reading") or "").strip()
                    meaning = (item.get("meaning") or "").strip()
                    example = (item.get("example") or "").strip()
                    jlpt    = (item.get("jlpt") or "").strip()
                    if not (term or reading):
                        continue
                    # optional: if example present but doesn't include headword, blank it (don't drop row)
                    head = term or reading
                    if example and head and head not in example:
                        example = ""
                    out.append([term or reading, reading, meaning, example, jlpt])

                if len(out) == before:
                    raise RuntimeError("Parsed zero items from this group")
                break  # success for this group

            except Exception as e:
                last_err = e
                time.sleep(0.4 + 0.4 * attempt)

        if last_err and (not out or len(out) == 0):
            snippet = (raw_reply[:260] + "…") if raw_reply else "<empty reply>"
            raise RuntimeError(f"Batch translation parsed zero items. Reply snippet: {snippet}")

        time.sleep(0.05)

    return out
