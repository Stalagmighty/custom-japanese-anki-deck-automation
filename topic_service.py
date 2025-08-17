# topic_service.py
from __future__ import annotations
from typing import Callable, Iterable, List, Tuple, Set, Dict
import json
import time
import os
from openai import OpenAI

def _norm(s: str) -> str:
    return (s or "").strip()

def _key(term: str, reading: str) -> Tuple[str, str]:
    return (_norm(term), _norm(reading))

class TopicGeneratorService:
    """
    Generates Japanese vocab rows for a topic, guaranteeing *exactly* `count`
    unique items that do NOT overlap `existing_keys` (term, reading).
    Optionally enriches via Jisho.
    """
    def __init__(
        self,
        augment_row_with_jisho: Callable[[str, str | None], List[str]] | None = None,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.4,
        client: OpenAI | None = None,
    ):
        self.augment_row_with_jisho = augment_row_with_jisho
        self.model = model
        self.temperature = temperature
        self.client = client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        """
        # keep avoid list compact
        avoid_str = "; ".join([f"{t}|{r}" for t, r in avoid_pairs[:250]])

        guidance = [
            "Return unique items only; no duplicates within output.",
            "Do not include any pair present in 'avoid_pairs'.",
            "reading must be kana (hiragana/katakana).",
            "example must be 1 short-medium, natural Japanese sentence using the term.",
            "meaning should be brief English. Can have two or more meanings, or subtly different words "
            "e.g., Harm, damage.",
            "jlpt is one of N5,N4,N3,N2,N1 or empty if unknown.",
            "Try to create list of words based on knowledge base, however, can reference web articles and other"
            "reputable sources if needed for inspiration.",
            "If user states that they want a list of phrases, they will start topic with Phrases about."
        ]
        if attempt_index >= 3:
            guidance.append("Choose more specific or less common items still clearly within the topic.")
        if attempt_index >= 6:
            guidance.append("Consider adjacent subtopics to avoid repeats while staying relevant.")
        if attempt_index >= 9:
            guidance.append("Avoid very high-frequency duplicates; diversify parts of speech.")

        user_payload = {
            "instruction": "Generate Japanese vocabulary strictly as JSON with key 'items'.",
            "topic": topic,
            "count": n,
            "avoid_pairs": avoid_str,
            "rules": guidance,
            "schema": {
                "term": "Kanji or kana headword",
                "reading": "Hiragana or katakana reading",
                "meaning": "Short English gloss",
                "example": "One short Japanese sentence using the term",
                "jlpt": "N5|N4|N3|N2|N1 or empty"
            }
        }

        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system",
                 "content": "You are a Japanese vocabulary generator. Respond ONLY with valid JSON."},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        )
        content = resp.choices[0].message.content or "{}"

        items: List[List[str]] = []
        try:
            data = json.loads(content)
            raw_items = data.get("items") or data.get("words") or []
            for it in raw_items:
                term = _norm(it.get("term"))
                reading = _norm(it.get("reading"))
                meaning = _norm(it.get("meaning"))
                example = _norm(it.get("example"))
                jlpt = _norm(it.get("jlpt"))
                if term:
                    items.append([term, reading, meaning, example, jlpt])
        except Exception as e:
            print(f"[TopicService JSON parse error] {e}")

        # local de-dupe within this batch
        uniq: Dict[Tuple[str, str], List[str]] = {}
        for row in items:
            k = _key(row[0], row[1])
            if k[0] and k not in uniq:
                uniq[k] = row
        return list(uniq.values())
