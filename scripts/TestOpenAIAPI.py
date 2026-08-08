from openai import OpenAI
from pathlib import Path
import os

_client = None  # don't create yet!

def get_openai_client() -> OpenAI:
    """Lazy-load the OpenAI client (uses env var or key file)."""
    global _client
    if _client is not None:
        return _client

    # 1️⃣ Try environment variable
    api_key = os.getenv("OPENAI_API_KEY")

    # 2️⃣ Fallback to key file in project root
    if not api_key:
        key_file = Path(__file__).resolve().parent.parent / "OPENAI_API_KEY.txt"
        if not key_file.exists():
            raise FileNotFoundError(f"❌ API key file not found at {key_file}")
        api_key = key_file.read_text(encoding="utf-8").strip()

    # 3️⃣ Sanity check
    if not api_key.startswith(("sk-", "sk-proj-")):
        raise ValueError("❌ Malformed API key: should start with 'sk-' or 'sk-proj-'")
    if any(c in api_key for c in (" ", "\t", "\n", "\r")):
        raise ValueError("❌ API key contains whitespace or newline characters")

    # 4️⃣ Create client only once
    _client = OpenAI(api_key=api_key)
    return _client
