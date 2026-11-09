"""Create .env from .env.example, replacing every `replace-me` with a fresh local-only secret."""

import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
target = root / ".env"
if target.exists():
    sys.exit(".env already exists; not overwriting")

lines = []
for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
    key, sep, value = line.partition("=")
    if sep and value.strip() == "replace-me":
        line = f"{key}={secrets.token_hex(32)}"  # hex: safe in URLs, shells and Makefiles
    lines.append(line)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {target}")
