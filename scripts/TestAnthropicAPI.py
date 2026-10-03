import sys
from pathlib import Path

# Reuse the app's client so this script exercises the same key resolution.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic_client import DEFAULT_MODEL, complete_text  # noqa: E402

if __name__ == "__main__":
    print(complete_text("Reply briefly.", "Say hello in Japanese.", model=DEFAULT_MODEL, max_tokens=2000))
