"""Resolve Claude's default home and prepare local onboarding without inference."""
import json
import os
from pathlib import Path
import tempfile

from backend.config import settings


def configured_claude_home() -> str | None:
    """Return the deployment override, leaving native defaults intact if unset."""
    value = settings.claude_config_dir.strip()
    if not value:
        return None
    return str(Path(os.path.expandvars(value)).expanduser().resolve())


def prepare_claude_onboarding(config_dir: str) -> None:
    """Seed the same onboarding state as PTY, without launching a model process."""
    directory = Path(config_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = directory / ".claude.json"
    if state.is_symlink():
        raise ValueError("Claude onboarding state must not be a symlink")
    document = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    if not isinstance(document, dict):
        raise ValueError("Claude onboarding state must be a JSON object")
    if document.get("hasCompletedOnboarding"):
        return
    document["hasCompletedOnboarding"] = True
    document.setdefault("theme", "dark")
    document.setdefault("numStartups", 1)
    fd, temporary = tempfile.mkstemp(prefix=".claude-onboarding-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
