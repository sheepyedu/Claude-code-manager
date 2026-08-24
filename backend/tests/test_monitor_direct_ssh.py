import json
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.log_entry import LogEntry
from backend.models.monitor_session import MonitorSession
from backend.models.task import Task
from backend.models.user import User
from backend.services.monitor_direct_ssh import (
    MonitorDirectSSHError,
    capture_monitor_direct_ssh_principal,
    direct_ssh_filesystem_projection,
    revalidate_monitor_direct_ssh,
)


@pytest.mark.asyncio
async def test_internal_direct_ssh_freezes_generation_bound_admin(
    session_factory,
):
    async with session_factory() as db:
        user = User(
            email="monitor-admin@example.com",
            name="Monitor Admin",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        task = Task(
            title="parent",
            description="d",
            status="executing",
            provider="claude",
            target_repo="/tmp",
            retry_count=2,
            turn_generation=7,
        )
        db.add_all([user, task])
        await db.flush()
        source = LogEntry(
            instance_id=1,
            task_id=task.id,
            task_retry_count=2,
            task_turn_generation=7,
            turn_scope="source",
            event_type="user_message",
            role="user",
            raw_json=json.dumps({
                "execution_principal": {
                    "user_id": user.id,
                    "role": "admin",
                    "mode": "unrestricted",
                }
            }),
        )
        db.add(source)
        await db.flush()
        task.turn_source_log_id = source.id
        await db.commit()

        principal = await capture_monitor_direct_ssh_principal(
            db,
            task,
            internal_request=True,
            request_user_id=None,
            request_user_role="member",
        )

    assert principal.user_id == user.id
    assert principal.role == "admin"
    assert principal.execution_mode == "unrestricted"


@pytest.mark.asyncio
async def test_internal_direct_ssh_rejects_stale_or_member_source(
    session_factory,
):
    async with session_factory() as db:
        task = Task(
            title="parent",
            description="d",
            status="executing",
            provider="claude",
            target_repo="/tmp",
            retry_count=0,
            turn_generation=1,
        )
        db.add(task)
        await db.flush()
        source = LogEntry(
            instance_id=1,
            task_id=task.id,
            task_retry_count=0,
            task_turn_generation=0,
            turn_scope="source",
            event_type="user_message",
            role="user",
            raw_json=json.dumps({
                "execution_principal": {
                    "user_id": None,
                    "role": "member",
                    "mode": "sandbox",
                }
            }),
        )
        db.add(source)
        await db.flush()
        task.turn_source_log_id = source.id
        await db.commit()

        with pytest.raises(MonitorDirectSSHError, match="stale or incomplete"):
            await capture_monitor_direct_ssh_principal(
                db,
                task,
                internal_request=True,
                request_user_id=None,
                request_user_role="member",
            )


@pytest.mark.asyncio
async def test_direct_ssh_revalidation_detects_role_revocation(session_factory):
    async with session_factory() as db:
        user = User(
            email="revoked-monitor@example.com",
            name="Revoked",
            password_hash="x",
            role="member",
            is_active=True,
        )
        task = Task(
            title="parent",
            description="d",
            status="executing",
            provider="claude",
            target_repo="/tmp",
        )
        db.add_all([user, task])
        await db.flush()
        session = type("Monitor", (), {
            "direct_ssh": True,
            "initiating_user_id": user.id,
            "initiating_user_role": "admin",
            "execution_mode": "unrestricted",
        })()

        with pytest.raises(MonitorDirectSSHError, match="role changed"):
            await revalidate_monitor_direct_ssh(
                db,
                task=task,
                monitor_session=session,
            )


@pytest.mark.asyncio
async def test_external_direct_ssh_rejects_member_request(session_factory):
    async with session_factory() as db:
        task = Task(
            title="parent",
            description="d",
            status="executing",
            provider="claude",
            target_repo="/tmp",
        )
        db.add(task)
        await db.flush()

        with pytest.raises(
            MonitorDirectSSHError,
            match="unrestricted administrator",
        ):
            await capture_monitor_direct_ssh_principal(
                db,
                task,
                internal_request=False,
                request_user_id=7,
                request_user_role="member",
            )


def test_direct_ssh_projection_reopens_only_same_owner_ssh_tree(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    ssh_root = home / ".ssh"
    ssh_root.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    protected, readable = direct_ssh_filesystem_projection((
        str(ssh_root),
        str(ssh_root / "id_ed25519"),
        str(home / ".ccm"),
        "/proc",
    ))

    assert str(ssh_root) in readable
    assert str(ssh_root) not in protected
    assert str(ssh_root / "id_ed25519") not in protected
    assert str(home / ".ccm") in protected
    assert "/proc" in protected


def test_direct_ssh_projection_rejects_symlinked_ssh_root(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    real_ssh = tmp_path / "real-ssh"
    home.mkdir()
    real_ssh.mkdir()
    (home / ".ssh").symlink_to(real_ssh, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    with pytest.raises(MonitorDirectSSHError, match="non-symlink"):
        direct_ssh_filesystem_projection((str(home / ".ssh"),))


@pytest.mark.asyncio
async def test_direct_ssh_principal_shape_is_database_enforced(session_factory):
    async with session_factory() as db:
        task = Task(
            title="parent",
            description="d",
            status="executing",
            provider="claude",
            target_repo="/tmp",
        )
        db.add(task)
        await db.flush()
        db.add(MonitorSession(
            task_id=task.id,
            description="invalid direct SSH principal",
            direct_ssh=True,
            initiating_user_role="member",
            execution_mode="sandbox",
        ))
        with pytest.raises(IntegrityError):
            await db.commit()
