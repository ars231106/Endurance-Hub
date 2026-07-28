import os
from pathlib import Path


def load_env():
    """Reads backend/.env (KEY=VALUE lines) into environment variables.
    Real values already set in the environment always win (setdefault),
    and the file is optional - no .env means dev-mode defaults."""
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
