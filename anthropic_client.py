"""Anthropic (Claude) client resolution, a single text-completion helper, and
response parsing.

The key is resolved environment-first so that no credential needs to live in
the working tree. See README for the full lookup order.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

import anthropic

DEFAULT_MODEL = "claude-sonnet-5-5"

_client = None


def get_anthropic_client() -> anthropic.Anthropic:
    global _client
    if _client is not None:
        return _client

    # 1) Already in env?
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()

    # 2) Otherwise, search common locations in THIS project
    if not api_key:
        here = Path(__file__).resolve().parent             # folder of this .py
        root = here                                        # assume script is at project root
        # If this file is inside a subfolder (e.g., src/ or app/), try its parent as root:
        # root = here.parent

        candidates = [
            root / "ANTHROPIC_API_KEY.txt",
        ]

        # Optional: honour a .env file if present
        dot_env = root / ".env"
        if dot_env.exists():
            # simple manual read; avoids python-dotenv dependency
            for line in dot_env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

        if not api_key:
            for p in candidates:
                if p.exists():
                    api_key = p.read_text(encoding="utf-8").strip()
                    break

        if not api_key:
            tried = "\n  - " + "\n  - ".join(str(p) for p in candidates + ([dot_env] if dot_env.exists() else []))
            raise FileNotFoundError(
                "Could not find ANTHROPIC_API_KEY. Set it in the environment or create one of:\n"
                f"{tried}"
            )

    # 3) Validate + export to env so other modules (TopicService, translators) can see it
    if not api_key.startswith("sk-ant-"):
        raise ValueError("ANTHROPIC_API_KEY looks malformed (expected to start with 'sk-ant-').")
    if any(c in api_key for c in (" ", "\t", "\n", "\r")):
        raise ValueError("ANTHROPIC_API_KEY contains whitespace/newlines; remove trailing characters.")

    os.environ["ANTHROPIC_API_KEY"] = api_key  # make visible process-wide
    _client = anthropic.Anthropic(api_key=api_key)
    return _client


def complete_text(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    effort: str = "low",
    client: anthropic.Anthropic | None = None,
) -> str:
    """Single Claude call; returns the concatenated text of the reply.

    max_tokens also covers any adaptive thinking, so keep it generous.
    Sonnet 5.5 rejects non-default sampling params, so temperature is not
    exposed. ``fallbacks="default"`` lets the API re-run a safety-declined
    request on a fallback model instead of returning nothing.
    """
    client = client or get_anthropic_client()
    resp = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        category = getattr(getattr(resp, "stop_details", None), "category", None)
        raise RuntimeError(f"Claude declined the request (category: {category})")
    return "".join(b.text for b in resp.content if b.type == "text")


def _extract_json(text: str) -> str:
    """
    Try to pull a JSON array/object from a reply that might contain extra text
    or be wrapped in ```json code fences.
    """
    if not text:
        return "[]"
    # Pull fenced ```json blocks first
    m = re.search(r"```json\s*(.+?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Otherwise try to find first JSON-looking segment
    start = text.find("[")
    if start != -1:
        end = text.rfind("]")
        if end != -1 and end > start:
            return text[start:end + 1]
    start = text.find("{")
    if start != -1:
        end = text.rfind("}")
        if end != -1 and end > start:
            return text[start:end + 1]
    return text.strip()


def _chunked(seq: Iterable, n: int):
    """Yield lists of size n from seq."""
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
