# topic_service.py
from __future__ import annotations
from typing import Callable, Iterable, List, Tuple, Set, Dict
import json
import time
import os
from openai import OpenAI
import re, json


def _norm(s: str) -> str:
    return (s or "").strip()

def _key(term: str, reading: str) -> Tuple[str, str]:
    return (_norm(term), _norm(reading))

import re, json

def _extract_json_block(text: str) -> str:
    """Prefer fenced ```json blocks; else return the largest {...} or [...] found."""
    if not text:
        return "{}"
    m = re.search(r"```json\s*(.+?)\s*```", text, flags=re.DOTALL|re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # else grab biggest object/array
    obj = (text.find("{"), text.rfind("}"))
    arr = (text.find("["), text.rfind("]"))
    if obj[0] != -1 and obj[1] > obj[0] and (arr[1]-arr[0] <= obj[1]-obj[0]):
        return text[obj[0]:obj[1]+1]
    if arr[0] != -1 and arr[1] > arr[0]:
        return text[arr[0]:arr[1]+1]
    return text.strip()

def _lenient_json_loads(s: str):
    """
    Coerce near‑JSON into JSON: fixes code fences, smart quotes, single quotes,
    unquoted keys, trailing commas, and wraps top‑level arrays into {"items":...}.
    """
    if not s:
        return {}
    # normalize smart quotes / NBSP
    s = s.replace("“","\"").replace("”","\"").replace("‘","'").replace("’","'")
    s = re.sub(r"\u00A0", " ", s)

    ss = s.strip()
    # if the model returned a raw array, wrap as {"items":[...]}
    if ss.startswith("[") and ss.endswith("]"):
        s = "{\"items\":"+ss+"}"

    # quote bare keys: items: [...] -> "items": [...]
    s = re.sub(r'(?m)(^|\s)([A-Za-z_][A-Za-z0-9_-]*)\s*:', r'\1"\2":', s)

    # convert 'single-quoted strings' to "double-quoted"
    def _flip(m):
        inner = m.group(1)
        return '"' + inner.replace('"', '\\"') + '"'
    s = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", _flip, s)

    # remove trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)

    try:
        return json.loads(s)
    except Exception:
        # last resort: try the largest object/array again
        block = _extract_json_block(s)
        if block.startswith("["):
            block = "{\"items\":"+block+"}"
        return json.loads(block)


class TopicGeneratorService:
    """
    Generates Japanese vocab rows for a topic, guaranteeing *exactly* `count`
    unique items that do NOT overlap `existing_keys` (term, reading).
    Optionally enriches via Jisho.
    """

    def __init__(self, augment_row_with_jisho=None, *, model="gpt-4o-mini", temperature=1.0, client=None):
        self.augment_row_with_jisho = augment_row_with_jisho
        self.model = model
        self.temperature = temperature
        self.client = client or OpenAI()  # ← fine, but won’t be used if you inject

    # ---------- PUBLIC -------------------------------------------------------
    def generate_rows(
        self,
        topic: str,
        count: int = 30,
        gpt_only: bool = False,
        existing_keys: Iterable[Tuple[str, str]] | None = None,
    ) -> List[List[str]]:
        """
        Returns exactly `count` rows: [term, reading, meaning, example, jlpt].
        Avoids any (term, reading) in `existing_keys`. Will loop and ask GPT for
        more until we have enough uniques (with a sensible safety cap).
        """
        avoid: Set[Tuple[str, str]] = set(existing_keys or [])
        collected: Dict[Tuple[str, str], List[str]] = {}

        needed = count
        attempt = 0
        # Safety cap: plenty of chances, gradually broadening scope
        while len(collected) < count and attempt < 12:
            attempt += 1
            remaining = count - len(collected)

            # Ask for a bit more than we still need to compensate for duplicates
            ask_for = min(remaining + 8, max(remaining + 4, 24))

            batch = self._ask_gpt_for_topic_batch(
                topic=topic,
                n=ask_for,
                avoid_pairs=list(avoid | set(collected.keys())),
                attempt_index=attempt,
            )

            # Filter + collect uniques
            for row in batch:
                term, reading, meaning, example, jlpt = (row + ["", "", "", "", ""])[:5]
                k = _key(term, reading)
                if not k[0] or k in avoid or k in collected:
                    continue
                collected[k] = [term, reading, meaning, example, jlpt]
                if len(collected) >= count:
                    break

            # gentle backoff if still short (helps with rate limits)
            if len(collected) < count:
                time.sleep(0.2 * attempt)

        # If still short (rare), force another final pass asking for exactly the remainder
        if len(collected) < count:
            final_need = count - len(collected)
            batch = self._ask_gpt_for_topic_batch(
                topic=topic,
                n=final_need + 10,
                avoid_pairs=list(avoid | set(collected.keys())),
                attempt_index=attempt + 1,
            )
            for row in batch:
                term, reading, meaning, example, jlpt = (row + ["", "", "", "", ""])[:5]
                k = _key(term, reading)
                if not k[0] or k in avoid or k in collected:
                    continue
                collected[k] = [term, reading, meaning, example, jlpt]
                if len(collected) >= count:
                    break

        rows = list(collected.values())

        # Optional Jisho enrichment
        if not gpt_only and self.augment_row_with_jisho:
            enriched: List[List[str]] = []
            for term, reading, meaning, example, jlpt in rows:
                try:
                    enriched.append(self.augment_row_with_jisho(term, reading or None))
                except Exception:
                    enriched.append([term, reading, meaning, example, jlpt])
            rows = enriched

        # Guarantee exact count (truncate in the astronomically rare case we got more)
        return rows[:count]

    # ---------- PRIVATE ------------------------------------------------------
    def _ask_gpt_for_topic_batch(
            self,
            topic: str,
            n: int,
            avoid_pairs: List[Tuple[str, str]],
            attempt_index: int,
    ) -> List[List[str]]:
        """
        Returns a list of rows: [term, reading, meaning, example, jlpt]
        GPT is instructed to avoid `avoid_pairs` and produce unique items.
        Uses lenient JSON extraction so minor format hiccups don't fail the batch.
        """
        import re, json

        # --- helpers -----------------------------------------------------------
        def _extract_json_block(text: str) -> str:
            """Extract fenced JSON or largest {...} or [...] block."""
            if not text:
                return "{}"
            m = re.search(r"```json\s*(.+?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
            obj = (text.find("{"), text.rfind("}"))
            arr = (text.find("["), text.rfind("]"))
            if obj[0] != -1 and obj[1] > obj[0] and (arr[1] - arr[0] <= obj[1] - obj[0]):
                return text[obj[0]: obj[1] + 1]
            if arr[0] != -1 and arr[1] > arr[0]:
                return text[arr[0]: arr[1] + 1]
            return text.strip()

        def _lenient_json_loads(s: str):
            """Coerce near-JSON into JSON: fix quotes, trailing commas, wrap arrays."""
            if not s:
                return {}
            s = s.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
            s = re.sub(r"\u00A0", " ", s)

            ss = s.strip()
            if ss.startswith("[") and ss.endswith("]"):
                s = "{\"items\":" + ss + "}"

            # quote bare keys
            s = re.sub(r'(?m)(^|\s)([A-Za-z_][A-Za-z0-9_-]*)\s*:', r'\1"\2":', s)

            # convert 'strings' → "strings"
            def _flip(m):
                inner = m.group(1)
                return '"' + inner.replace('"', '\\"') + '"'

            s = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", _flip, s)

            # remove trailing commas
            s = re.sub(r",\s*([}\]])", r"\1", s)

            try:
                return json.loads(s)
            except Exception:
                block = _extract_json_block(s)
                if block.startswith("["):
                    block = "{\"items\":" + block + "}"
                return json.loads(block)

        def _norm(x: str) -> str:
            return (x or "").strip()

        def _is_kana(s: str) -> bool:
            return bool(s) and all(
                ('\u3040' <= ch <= '\u309f') or ('\u30a0' <= ch <= '\u30ff') or ch in ('・', 'ー')
                for ch in s
            )

        def _has_ascii_letters(s: str) -> bool:
            return bool(re.search(r"[A-Za-z]", s or ""))

        # --- guidance rules ----------------------------------------------------
        avoid_str = "; ".join([f"{t}|{r}" for t, r in avoid_pairs[:250]])
        guidance = [
            "Return unique items only; no duplicates within output.",
            "Do not include any pair present in 'avoid_pairs'.",
            "term MUST be Japanese (kanji/kana). Never output the English seed as term.",
            "reading must be kana (hiragana/katakana).",
            "meaning should be brief English gloss(es).",
            "example must be ONE natural Japanese sentence (約60–110文字) that includes the exact JP term string, "
            "and reads context‑rich (news/academic/professional tone).",
            "jlpt is one of N5,N4,N3,N2,N1 or empty if unknown.",
            "Prefer common equivalents over katakana unless the loanword is standard.",
            "Sentences should feel context-rich (news, academic, professional, thoughtful).",
        ]
        if attempt_index >= 3:
            guidance.append("Choose more specific or less common items still clearly within the topic.")
        if attempt_index >= 6:
            guidance.append("Consider adjacent subtopics to avoid repeats while staying relevant.")
        if attempt_index >= 9:
            guidance.append("Avoid very high-frequency duplicates; diversify parts of speech.")

        # --- prompts -----------------------------------------------------------
        sys_prompt = (
            "You are a meticulous Japanese vocabulary generator. "
            "Respond with STRICT JSON only. No prose, no comments."
        )
        user_payload = {
            "instruction": "Generate Japanese vocabulary strictly as JSON with key 'items'.",
            "topic": topic,
            "count": n,
            "avoid_pairs": avoid_str,
            "rules": guidance,
            "schema": {
                "term": "Kanji or kana headword (JP only).",
                "reading": "Hiragana or katakana reading.",
                "meaning": "Short English gloss.",
                "example": "One medium-length Japanese sentence that includes the JP term.",
                "jlpt": "N5|N4|N3|N2|N1 or empty.",
            },
        }

        # --- API call ----------------------------------------------------------
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                max_tokens=1800,
            )
            content = resp.choices[0].message.content or "{}"
        except Exception as e:
            print(f"[TopicService request error] {e}")
            return []

        # --- parse & validate --------------------------------------------------
        try:
            raw_json = _extract_json_block(content)
            data = _lenient_json_loads(raw_json)
        except Exception as e:
            print(f"[TopicService JSON parse error] {e} | snippet: {content[:400]}")
            return []

        raw_items = (data.get("items") if isinstance(data, dict) else None) or []
        if not isinstance(raw_items, list):
            raw_items = [raw_items] if raw_items else []

        rows: List[List[str]] = []
        for it in raw_items:
            term = _norm(it.get("term"))
            reading = _norm(it.get("reading"))
            meaning = _norm(it.get("meaning"))
            example = _norm(it.get("example"))
            jlpt = _norm(it.get("jlpt"))

            if not term or _has_ascii_letters(term):
                continue
            if reading and not _is_kana(reading):
                continue
            if example and term not in example:
                continue

            rows.append([term, reading, meaning, example, jlpt])

        # de-dupe
        uniq: Dict[Tuple[str, str], List[str]] = {}
        for row in rows:
            k = (row[0], row[1])
            if k not in uniq:
                uniq[k] = row
        return list(uniq.values())

        # if still failing
        snippet = (raw_reply[:200] + "…") if raw_reply else ""
        raise RuntimeError(f"[Topic batch] failed after retries: {last_err} | reply snippet: {snippet}")
