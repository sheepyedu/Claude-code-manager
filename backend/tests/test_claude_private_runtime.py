"""Claude startup preparation must stay in the configured provider home."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "existing-session"])
async def test_pool_disabled_resume_uses_configured_home(tmp_path, monkeypatch, session_id):
    from backend.services.dispatcher import GlobalDispatcher

    monkeypatch.setattr(settings, "claude_config_dir", str(tmp_path / "private"))
    dispatcher = GlobalDispatcher(MagicMock(), MagicMock(), MagicMock())
    dispatcher.pool = None
    assert await dispatcher._resolve_resume_config_dir(session_id) == str(tmp_path / "private")


@pytest.mark.parametrize("invalid_state", ["symlink", "invalid-json", "array"])
def test_onboarding_preserves_unsafe_or_unparseable_state(tmp_path, invalid_state):
    from backend.services.claude_runtime import prepare_claude_onboarding

    directory = tmp_path / "private"
    directory.mkdir()
    state = directory / ".claude.json"
    target = tmp_path / "original.json"
    target.write_text('{"keep":"original"}')
    if invalid_state == "symlink":
        state.symlink_to(target)
    else:
        state.write_text("not-json" if invalid_state == "invalid-json" else "[]")
    before = state.read_bytes()
    with pytest.raises(ValueError):
        prepare_claude_onboarding(str(directory))
    assert state.read_bytes() == before
    assert target.read_text() == '{"keep":"original"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("use_pool", [False, True])
async def test_warmup_prepares_selected_home_without_model_call(
    tmp_path, monkeypatch, use_pool,
):
    from backend.main import _ensure_claude_warmup

    user_home = tmp_path / "user"
    original = user_home / ".claude" / ".claude.json"
    original.parent.mkdir(parents=True)
    original.write_text('{"keep":"original-user-state"}')
    configured = tmp_path / "private"
    selected = tmp_path / "pool-account" if use_pool else configured
    selected.mkdir()
    state = selected / ".claude.json"
    state.write_text('{"keep":"private-user-state"}')
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    monkeypatch.setattr(settings, "claude_config_dir", str(configured), raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))
    monkeypatch.setattr(settings, "pool_enabled", use_pool)
    monkeypatch.setattr(settings, "claude_binary", "/private/bin/claude-ccswitch")
    monkeypatch.setattr(
        "backend.services.claude_pool.ClaudePool",
        MagicMock(return_value=SimpleNamespace(_accounts=[
            SimpleNamespace(enabled=True, config_dir=str(selected)),
            SimpleNamespace(enabled=False, config_dir=str(tmp_path / "disabled")),
        ])),
    )
    launch = MagicMock()
    monkeypatch.setattr("subprocess.run", launch)

    await _ensure_claude_warmup()

    launch.assert_not_called()
    assert original.read_text() == '{"keep":"original-user-state"}'
    prepared = json.loads(state.read_text())
    assert prepared["keep"] == "private-user-state"
    assert prepared["hasCompletedOnboarding"] is True
    assert state.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "disabled").exists()
    before = state.stat().st_mtime_ns
    await _ensure_claude_warmup()
    assert state.stat().st_mtime_ns == before
