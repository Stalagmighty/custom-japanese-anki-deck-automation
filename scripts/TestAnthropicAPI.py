"""Check the Claude key works: makes one real (tiny) API call."""
import sys
from pathlib import Path

# Reuse the app's client so this script exercises the same key resolution.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic_client import generate_json  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"}},
    "required": ["greeting"],
    "additionalProperties": False,
}

if __name__ == "__main__":
    print(generate_json("Reply briefly.", {"request": "Say hello in Japanese."}, SCHEMA, max_tokens=2000))
