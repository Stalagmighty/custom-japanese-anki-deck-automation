"""Anthropic (Claude) client resolution and the one call the app makes to Claude.

The key is resolved environment-first so that no credential needs to live in
the working tree. See README for the full lookup order.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

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


class ClaudeError(RuntimeError):
    """Claude returned something the app can't use (declined, truncated, unparseable)."""


def generate_json(
    system: str,
    payload: dict,
    schema: dict,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    effort: str = "low",
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Send `payload` (as JSON) and return Claude's reply parsed against `schema`.

    Structured outputs guarantee the reply is valid JSON matching the schema, so
    there is no lenient parsing or retry-on-bad-JSON here. max_tokens also covers
    adaptive thinking, so keep it generous. ``fallbacks="default"`` lets the API
    re-run a safety-declined request on a fallback model instead of failing.
    """
    client = client or get_anthropic_client()
    resp = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        category = getattr(getattr(resp, "stop_details", None), "category", None)
        raise ClaudeError(f"Claude declined the request (category: {category}).")
    if resp.stop_reason == "max_tokens":
        raise ClaudeError("Claude's reply was cut off (max_tokens). Try a smaller batch.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"Claude's reply wasn't valid JSON: {e}") from e
