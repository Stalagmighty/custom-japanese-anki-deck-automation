"""GPT-backed enrichment: natural example sentences for vocabulary terms."""
from __future__ import annotations

import json
import time
from typing import Dict, Iterable, List

from openai_client import _extract_json, get_openai_client
from text_utils import remove_furigana


def generate_examples_with_gpt_batch(
    terms: List[str],
    *,
    model: str = "gpt-4o-mini",
    batch_size: int = 40,
    max_tokens_per_batch: int = 900,   # longer sentences need a bit more room
    retries: int = 2,
) -> Dict[str, str]:
    """
    For each JP term, generate EXACTLY ONE medium-length, natural Japanese sentence
    that *includes the exact term string*. Returns a dict {normalized_term: example}.
    """
    if not terms:
        return {}

    client = get_openai_client()
    result: Dict[str, str] = {}

    # Tighter, shared guidance (no English, ensure term presence, longer sentence)
    sys_prompt = (
        "You are a Japanese sentence generator. For each vocabulary item, "
        "produce EXACTLY ONE natural Japanese sentence in Japanese that includes the term. "
        "Target 60–110 Japanese characters (not words). Prefer context‑rich usage (news/academic/professional). "
        "Return STRICT JSON only."
    )

    def _extract_text_any(resp) -> str:
        """
        Works for both Responses API and Chat Completions.
        Tries: .output_text → responses.output[].content[].text.value → choices[0].message.content
        """
        # 1) New SDK convenience
        t = getattr(resp, "output_text", None)
        if t:
            return t

        # 2) Responses API canonical path
        try:
            out_chunks = []
            for item in getattr(resp, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    tv = getattr(getattr(c, "text", None), "value", None)
                    if tv:
                        out_chunks.append(tv)
            if out_chunks:
                return "".join(out_chunks)
        except Exception:
            pass

        # 3) Chat Completions
        try:
            return resp.choices[0].message.content or ""
        except Exception:
            return ""

    def _chunked(seq: Iterable, n: int):
        buf = []
        for x in seq:
            buf.append(x)
            if len(buf) >= n:
                yield buf
                buf = []
        if buf:
            yield buf

    for group in _chunked(terms, batch_size):
        payload = {
            "instructions": (
                "Return a JSON array of objects with keys 'term' and 'example'. "
                "Rules: the example MUST include the exact JP term string and be about 60–110 JP characters."
            ),
            "terms": group,
        }

        last_err = None
        raw_reply = ""
        for attempt in range(retries + 1):
            try:
                if model.startswith("gpt-5"):
                    # Responses API (token arg name differs by SDK version).
                    try:
                        resp = client.responses.create(
                            model=model,
                            input=[
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                            ],
                            max_output_tokens=max_tokens_per_batch,
                        )
                    except TypeError:
                        resp = client.responses.create(
                            model=model,
                            input=[
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                            ],
                        )
                else:
                    # Chat Completions
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        max_tokens=max_tokens_per_batch,
                    )

                raw_reply = _extract_text_any(resp)  # <-- capture reply text
                raw_json = _extract_json(raw_reply)
                try:
                    data = json.loads(raw_json)
                except json.JSONDecodeError:
                    data = []

                # accept either a list or {"items":[...]} or a single object
                if isinstance(data, dict):
                    data = data.get("items", data)
                if not isinstance(data, list):
                    data = [data]

                wrote_any = False
                for item in data:
                    t = (item.get("term") or "").strip()
                    ex = (item.get("example") or "").strip()
                    if not t or not ex:
                        continue
                    # Enforce “example contains term” at client-side too.
                    print(f"[DEBUG] t={t!r}  ex={ex!r}  found={t in ex}")
                    if t not in ex:
                        continue
                    result[remove_furigana(t)] = ex
                    wrote_any = True
                if wrote_any:
                    break
                else:
                    # Force retry path if nothing usable parsed
                    raise ValueError("Parsed zero usable items from batch.")
            except Exception as e:
                last_err = e
                time.sleep(0.4 + 0.4 * attempt)

        if last_err and not any(remove_furigana(t) in result for t in group):
            snippet = (raw_reply[:240] + "…") if raw_reply else ""
            # Don’t raise here — just continue so other batches still run
            print(f"[GPT batch warn] group produced no items. reply snippet: {snippet}")

        time.sleep(0.08)

    return result
