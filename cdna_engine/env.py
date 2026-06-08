from __future__ import annotations

import os
from pathlib import Path

from .paths import REPO_ROOT


def load_env_local(path: Path | None = None) -> None:
    """Load simple KEY=VALUE entries from .env.local without adding a dependency."""
    env_path = path or REPO_ROOT / ".env.local"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)

    if "GEMINI_API_KEY" not in os.environ and "GOOGLE_GENERATIVE_AI_API_KEY" in os.environ:
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_GENERATIVE_AI_API_KEY"]
    if "GOOGLE_GENERATIVE_AI_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
        os.environ["GOOGLE_GENERATIVE_AI_API_KEY"] = os.environ["GEMINI_API_KEY"]
