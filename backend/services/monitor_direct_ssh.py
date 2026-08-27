"""Authorization and filesystem projection for opt-in Monitor direct SSH.

Direct SSH is intentionally separate from managed Task SSH grants.  It lets a
short-lived Monitor check use the service account's existing ``~/.ssh/config``
and keys, matching an unrestricted administrator Task turn.  The privilege is
frozen at Monitor creation and revalidated before every provider effect.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.log_entry import LogEntry
from backend.models.task import Task
from backend.models.user import User
from backend.services.skill_context import is_worker_managed_task_metadata
from backend.services.task_runtime_secrets import (
    PrivateRuntimeTempDir,
    TaskRuntimeSecretError,
)


_DIRECT_SSH_BIN_DIR = "ssh-bin"
_DIRECT_SSH_WRAPPER = "ssh"
_SYSTEM_SSH = "/usr/bin/ssh"


class MonitorDirectSSHError(ValueError):
    """Raised when a Monitor cannot safely inherit direct SSH authority."""


@dataclass(frozen=True, slots=True)
class MonitorDirectSSHPrincipal:
    user_id: int | None
    role: str
    execution_mode: str


def _direct_ssh_wrapper_payload() -> bytes:
    config_path = Path.home() / ".ssh" / "config"
    return (
        "#!/bin/sh\n"
        f"exec {_SYSTEM_SSH} -F {shlex.quote(str(config_path))} \"$@\"\n"
    ).encode("utf-8")


def validate_monitor_direct_ssh_wrapper(path: str | os.PathLike[str]) -> Path:
    """Prove one exact owner-only wrapper directory and immutable payload."""

    directory = Path(os.path.abspath(os.fspath(path)))
    wrapper = directory / _DIRECT_SSH_WRAPPER
    try:
        directory_info = directory.lstat()
        wrapper_info = wrapper.lstat()
        entries = {entry.name for entry in directory.iterdir()}
        system_ssh_info = Path(_SYSTEM_SSH).lstat()
    except OSError as exc:
        raise MonitorDirectSSHError(
            "Monitor direct SSH wrapper is unavailable"
        ) from exc
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if (
        stat.S_ISLNK(directory_info.st_mode)
        or not stat.S_ISDIR(directory_info.st_mode)
        or (effective_uid is not None and directory_info.st_uid != effective_uid)
        or stat.S_IMODE(directory_info.st_mode) != 0o500
        or entries != {_DIRECT_SSH_WRAPPER}
        or stat.S_ISLNK(wrapper_info.st_mode)
        or not stat.S_ISREG(wrapper_info.st_mode)
        or (effective_uid is not None and wrapper_info.st_uid != effective_uid)
        or wrapper_info.st_nlink != 1
        or stat.S_IMODE(wrapper_info.st_mode) != 0o500
        or stat.S_ISLNK(system_ssh_info.st_mode)
        or not stat.S_ISREG(system_ssh_info.st_mode)
        or not os.access(_SYSTEM_SSH, os.X_OK)
    ):
        raise MonitorDirectSSHError(
            "Monitor direct SSH wrapper is not an exact private executable"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(wrapper, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != wrapper_info.st_dev
            or opened.st_ino != wrapper_info.st_ino
        ):
            raise MonitorDirectSSHError(
                "Monitor direct SSH wrapper changed while opening"
            )
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > 4096:
                raise MonitorDirectSSHError(
                    "Monitor direct SSH wrapper payload is invalid"
                )
    finally:
        os.close(descriptor)
    if bytes(payload) != _direct_ssh_wrapper_payload():
        raise MonitorDirectSSHError(
            "Monitor direct SSH wrapper payload is invalid"
        )
    return directory


def materialize_monitor_direct_ssh_wrapper(
    runtime: PrivateRuntimeTempDir,
) -> Path:
    """Create a generation-private ``ssh`` that ignores system SSH config.

    OpenSSH rejects root-owned system snippets after the provider enters its
    unprivileged user namespace.  Prepending this directory to ``PATH`` also
    makes a nested ``ProxyCommand ssh ...`` use the same explicit user config.
    The complete directory is projected read-only into the provider sandbox.
    """

    runtime.assert_valid()
    directory = runtime.path / _DIRECT_SSH_BIN_DIR
    try:
        os.mkdir(directory, 0o700)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(directory, directory_flags)
        try:
            file_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(
                _DIRECT_SSH_WRAPPER,
                file_flags,
                0o500,
                dir_fd=directory_fd,
            )
            try:
                os.fchmod(descriptor, 0o500)
                view = memoryview(_direct_ssh_wrapper_payload())
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short Monitor direct SSH wrapper write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fchmod(directory_fd, 0o500)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        runtime.assert_valid()
        return validate_monitor_direct_ssh_wrapper(directory)
    except BaseException as exc:
        if isinstance(exc, (MonitorDirectSSHError, TaskRuntimeSecretError)):
            raise
        raise MonitorDirectSSHError(
            "Monitor direct SSH wrapper could not be materialized"
        ) from exc


def _validated_principal(
    *,
    user_id: object,
    role: object,
    execution_mode: object,
) -> MonitorDirectSSHPrincipal:
    if user_id is not None and (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise MonitorDirectSSHError("Monitor direct SSH principal is invalid")
    if role not in {"admin", "super_admin"} or execution_mode != "unrestricted":
        raise MonitorDirectSSHError(
            "Monitor direct SSH requires an unrestricted administrator turn"
        )
    return MonitorDirectSSHPrincipal(
        user_id=user_id,
        role=str(role),
        execution_mode=str(execution_mode),
    )


async def _revalidate_user(
    db: AsyncSession,
    principal: MonitorDirectSSHPrincipal,
) -> None:
    if principal.user_id is None:
        # Deployment-token/legacy-open administrators have no local User row.
        # The trusted API boundary is the authority for this frozen identity.
        return
    user = await db.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise MonitorDirectSSHError(
            "Monitor direct SSH administrator is no longer active"
        )
    expected_mode = (
        "unrestricted"
        if user.role in {"admin", "super_admin"}
        else "sandbox"
    )
    if user.role != principal.role or expected_mode != principal.execution_mode:
        raise MonitorDirectSSHError(
            "Monitor direct SSH administrator role changed after admission"
        )


def _require_local_task(task: Task) -> None:
    if (
        task.worker_id is not None
        or task.shared_from_id is not None
        or is_worker_managed_task_metadata(task.metadata_)
    ):
        raise MonitorDirectSSHError(
            "Monitor direct SSH is available only to local, unshared Manager Tasks"
        )


async def capture_monitor_direct_ssh_principal(
    db: AsyncSession,
    task: Task,
    *,
    internal_request: bool,
    request_user_id: int | None,
    request_user_role: str,
) -> MonitorDirectSSHPrincipal:
    """Freeze the trusted principal that explicitly requested direct SSH."""

    _require_local_task(task)
    if internal_request:
        source_id = task.turn_source_log_id
        if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
            raise MonitorDirectSSHError(
                "Monitor direct SSH requires a generation-bound user source"
            )
        source = await db.get(LogEntry, source_id)
        if (
            source is None
            or source.task_id != task.id
            or source.task_retry_count != task.retry_count
            or source.task_turn_generation != task.turn_generation
            or source.turn_scope != "source"
        ):
            raise MonitorDirectSSHError(
                "Monitor direct SSH Task source is stale or incomplete"
            )
        try:
            payload = json.loads(source.raw_json or "")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MonitorDirectSSHError(
                "Monitor direct SSH Task source principal is missing"
            ) from exc
        raw_principal = (
            payload.get("execution_principal")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(raw_principal, dict):
            raise MonitorDirectSSHError(
                "Monitor direct SSH Task source principal is missing"
            )
        principal = _validated_principal(
            user_id=raw_principal.get("user_id"),
            role=raw_principal.get("role"),
            execution_mode=raw_principal.get("mode"),
        )
    else:
        principal = _validated_principal(
            user_id=request_user_id,
            role=request_user_role,
            execution_mode=(
                "unrestricted"
                if request_user_role in {"admin", "super_admin"}
                else "sandbox"
            ),
        )
    await _revalidate_user(db, principal)
    return principal


async def revalidate_monitor_direct_ssh(
    db: AsyncSession,
    *,
    task: Task,
    monitor_session: object,
) -> bool:
    """Revalidate a frozen Monitor privilege immediately before launch."""

    if not bool(getattr(monitor_session, "direct_ssh", False)):
        return False
    _require_local_task(task)
    principal = _validated_principal(
        user_id=getattr(monitor_session, "initiating_user_id", None),
        role=getattr(monitor_session, "initiating_user_role", None),
        execution_mode=getattr(monitor_session, "execution_mode", None),
    )
    await _revalidate_user(db, principal)
    return True


def direct_ssh_filesystem_projection(
    protected_paths: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Re-open only the service user's existing ``~/.ssh`` tree for reads.

    The caller keeps every other Manager secret protected.  The SSH tree is
    removed from the deny set and returned as an explicit read-only path; the
    provider sandbox remains responsible for preventing local writes.
    """

    ssh_root = Path.home() / ".ssh"
    try:
        info = ssh_root.lstat()
    except OSError as exc:
        raise MonitorDirectSSHError(
            "Monitor direct SSH requires an existing ~/.ssh directory"
        ) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (hasattr(os, "geteuid") and info.st_uid != os.geteuid())
        or bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    ):
        raise MonitorDirectSSHError(
            "Monitor direct SSH requires a private, same-owner, non-symlink "
            "~/.ssh directory"
        )
    canonical_root = os.path.abspath(os.fspath(ssh_root))
    try:
        resolved_root = os.path.realpath(canonical_root)
    except OSError:
        resolved_root = canonical_root
    roots = tuple(sorted({canonical_root, resolved_root}))
    remaining: set[str] = set()
    for raw_path in protected_paths:
        path = os.path.abspath(os.path.expanduser(str(raw_path)))
        try:
            inside_ssh = any(
                os.path.commonpath((path, root)) == root
                for root in roots
            )
        except ValueError:
            inside_ssh = False
        if not inside_ssh:
            remaining.add(path)
    return tuple(sorted(remaining)), roots
