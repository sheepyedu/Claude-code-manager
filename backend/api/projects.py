import asyncio
import fnmatch
import os
import pathlib
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db, async_session
from backend.services.agent_docs import inject_agents_md
from backend.models.discussion import Discussion
from backend.models.project import Project
from backend.api.deps import (
    get_current_user_id,
    get_current_user_role,
    require_project_access,
    require_worker_target_access,
)
from backend.models.project_todo import ProjectTodo
from backend.models.tag import Tag
from backend.models.global_settings import GlobalSettings
from backend.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectReorderItem
from backend.services.git_config import merge_git_config, settings_to_dict
from backend.services.dispatcher import _build_git_env

router = APIRouter(prefix="/api/projects", tags=["projects"])
_project_setup_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


_DELIVERY_PROJECT_IDENTITY_FIELDS = frozenset(
    {"git_url", "has_remote", "default_branch"}
)


async def _delivery_run_reference(
    db: AsyncSession,
    *,
    project_id: int,
    active_only: bool,
) -> int | None:
    """Return one Delivery Run whose durable scope depends on this Project."""

    # Keep this import local so the ordinary Project API does not make the
    # Delivery model part of module-import initialization order.
    from backend.models.delivery import DeliveryRun

    statement = select(DeliveryRun.id).where(
        DeliveryRun.project_id == project_id,
    )
    if active_only:
        statement = statement.where(DeliveryRun.activity != "terminal")
    return (await db.execute(statement.limit(1).with_for_update())).scalar_one_or_none()


async def _reject_active_delivery_project_mutation(
    db: AsyncSession,
    *,
    project_id: int,
) -> None:
    run_id = await _delivery_run_reference(
        db,
        project_id=project_id,
        active_only=True,
    )
    if run_id is not None:
        raise HTTPException(
            409,
            f"Project is frozen while Delivery Run {run_id} is active",
        )

async def _require_project_access(request: Request, project_id: int, db: AsyncSession):
    """Backward-compatible local alias for the shared Project ACL."""
    await require_project_access(request, project_id, db)



@router.get("")
async def list_projects(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = get_current_user_id(request)
    user_role = get_current_user_role(request)
    stmt = select(Project).order_by(Project.sort_order.asc(), Project.name.asc())
    if user_role not in ("admin", "super_admin") and user_id:
        from backend.models.team_share import TeamProjectShare
        from backend.models.worker import Worker
        from backend.models.user_group import UserGroupMember
        user_group_ids = select(UserGroupMember.group_id).where(UserGroupMember.user_id == user_id)
        shared_project_ids = select(TeamProjectShare.project_id).where(
            ((TeamProjectShare.target_type == "user") & (TeamProjectShare.target_id == user_id))
            | ((TeamProjectShare.target_type == "group") & TeamProjectShare.target_id.in_(user_group_ids))
        )
        owned_worker_ids = select(Worker.id).where(Worker.owner_user_id == user_id)
        stmt = stmt.where(
            Project.id.in_(shared_project_ids)
            | Project.worker_id.in_(owned_worker_ids)
        )
    result = await db.execute(stmt)
    projects = list(result.scalars().all())

    # Annotate each project with its location using project.worker_id
    from backend.models.worker import Worker as WorkerModel
    worker_name_map: dict[int, str] = {}
    worker_ids = {p.worker_id for p in projects if p.worker_id}
    if worker_ids:
        wr = await db.execute(select(WorkerModel.id, WorkerModel.name).where(WorkerModel.id.in_(worker_ids)))
        worker_name_map = {wid: wname for wid, wname in wr}

    out = []
    for p in projects:
        d = ProjectResponse.model_validate(p).model_dump()
        d["location"] = worker_name_map.get(p.worker_id, "local") if p.worker_id else "local"
        out.append(d)
    return out


@router.get("/tags", response_model=list[str])
async def list_project_tags(request: Request, db: AsyncSession = Depends(get_db)):
    """Return unique tags from projects the user can see."""
    user_id = get_current_user_id(request)
    user_role = get_current_user_role(request)
    stmt = select(Project.tags)
    if user_role not in ("admin", "super_admin") and user_id:
        from backend.models.team_share import TeamProjectShare
        from backend.models.worker import Worker
        from backend.models.user_group import UserGroupMember
        user_group_ids = select(UserGroupMember.group_id).where(UserGroupMember.user_id == user_id)
        shared_project_ids = select(TeamProjectShare.project_id).where(
            ((TeamProjectShare.target_type == "user") & (TeamProjectShare.target_id == user_id))
            | ((TeamProjectShare.target_type == "group") & TeamProjectShare.target_id.in_(user_group_ids))
        )
        owned_worker_ids = select(Worker.id).where(Worker.owner_user_id == user_id)
        stmt = stmt.where(
            Project.id.in_(shared_project_ids)
            | Project.worker_id.in_(owned_worker_ids)
        )
    result = await db.execute(stmt)
    all_tags: set[str] = set()
    for (tags,) in result:
        if tags:
            all_tags.update(tags)
    return sorted(all_tags)


@router.put("/reorder", response_model=list[ProjectResponse])
async def reorder_projects(
    body: list[ProjectReorderItem],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Bulk-update sort_order for a list of projects."""
    for item in body:
        project = await db.get(Project, item.id)
        if project is None:
            raise HTTPException(404, "Project not found")
        await require_project_access(request, item.id, db)
        await db.execute(
            update(Project).where(Project.id == item.id).values(sort_order=item.sort_order)
        )
    await db.commit()
    # Reuse the canonical visibility filter.  Returning the whole Project table
    # here used to disclose credentials even when the request body was empty.
    return await list_projects(request, db)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(request: Request, body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    await require_worker_target_access(request, body.worker_id, db)
    # Check duplicate name
    existing = await db.execute(select(Project).where(Project.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Project '{body.name}' already exists")

    workspace = os.path.expanduser(settings.workspace_dir)
    local_path = os.path.join(workspace, body.name)
    has_remote = body.git_url is not None and body.git_url.strip() != ""

    project = Project(
        name=body.name,
        worker_id=body.worker_id,
        git_url=body.git_url if has_remote else None,
        has_remote=has_remote,
        default_branch=body.default_branch,
        local_path=local_path,
        status="pending",
        sort_order=body.sort_order,
        tags=body.tags,
        env_files=body.env_files,
        git_author_name=body.git_author_name,
        git_author_email=body.git_author_email,
        git_credential_type=body.git_credential_type,
        git_ssh_key_path=body.git_ssh_key_path,
        git_https_username=body.git_https_username,
        git_https_token=body.git_https_token,
    )
    db.add(project)

    # Auto-create Tag records for any new tag names
    if body.tags:
        existing = await db.execute(select(Tag.name))
        existing_names = {row[0] for row in existing}
        for tag_name in body.tags:
            if tag_name not in existing_names:
                db.add(Tag(name=tag_name))
                existing_names.add(tag_name)

    await db.commit()
    await db.refresh(project)

    # Worker projects: skip local clone — worker_proxy handles remote clone when first task runs
    if not body.worker_id:
        global_cfg = await db.get(GlobalSettings, 1)
        git_config = merge_git_config(_extract_git_config(project), settings_to_dict(global_cfg))
        if has_remote:
            asyncio.create_task(_clone_repo(project.id, body.git_url, local_path, body.name, body.default_branch, git_config))
        else:
            asyncio.create_task(_init_local_repo(project.id, local_path, body.name, body.default_branch, git_config))
    else:
        # Mark as ready immediately — actual clone happens on Worker
        project.status = "ready"
        await db.commit()
        await db.refresh(project)

    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await _require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int, body: ProjectUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    await _require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.rollback()
    locked = await db.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(id=Project.id)
    )
    if locked.rowcount != 1:
        raise HTTPException(404, "Project not found")
    project = await db.get(Project, project_id, populate_existing=True)
    updates = body.model_dump(exclude_unset=True)
    # Auto-sync has_remote when git_url is set
    if "git_url" in updates and updates["git_url"] and "has_remote" not in updates:
        updates["has_remote"] = True
    changed_delivery_identity = {
        field
        for field in _DELIVERY_PROJECT_IDENTITY_FIELDS & updates.keys()
        if updates[field] != getattr(project, field)
    }
    if changed_delivery_identity:
        await _reject_active_delivery_project_mutation(
            db,
            project_id=project_id,
        )
    for key, value in updates.items():
        setattr(project, key, value)

    # Auto-create Tag records for any new tag names
    if "tags" in updates and updates["tags"]:
        existing = await db.execute(select(Tag.name))
        existing_names = {row[0] for row in existing}
        for tag_name in updates["tags"]:
            if tag_name not in existing_names:
                db.add(Tag(name=tag_name))
                existing_names.add(tag_name)

    await db.commit()
    await db.refresh(project)

    # Apply git config to local repo immediately if any git fields changed
    git_fields = {"git_author_name", "git_author_email", "git_credential_type",
                  "git_ssh_key_path", "git_https_username", "git_https_token"}
    if git_fields & updates.keys() and project.local_path and os.path.isdir(project.local_path):
        await _apply_git_config(project.local_path, _extract_git_config(project))

    return project


@router.delete("/{project_id}")
async def delete_project(project_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    from backend.api.deps import require_admin
    from backend.services.project_share_admission import (
        ProjectShareAdmissionError,
        lock_project_share_authority,
    )

    require_admin(request)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.rollback()
    try:
        project = await lock_project_share_authority(db, project_id)
    except ProjectShareAdmissionError as exc:
        await db.rollback()
        raise HTTPException(
            409,
            "Could not establish the Project deletion fence; retry",
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(404, "Project not found") from exc

    discussion = (
        await db.execute(
            select(Discussion.id, Discussion.status)
            .where(Discussion.project_id == project_id)
            .order_by(Discussion.id)
            .limit(1)
            .with_for_update()
        )
    ).first()
    if discussion is not None:
        discussion_id, discussion_status = discussion
        if discussion_status in {"active", "closing"}:
            detail = (
                "Cannot delete a Project with an active or closing Discussion "
                f"{discussion_id}"
            )
        else:
            detail = (
                f"Delete Discussion {discussion_id} before deleting its Project"
            )
        raise HTTPException(
            409,
            detail,
        )
    run_id = await _delivery_run_reference(
        db,
        project_id=project_id,
        active_only=False,
    )
    if run_id is not None:
        raise HTTPException(
            409,
            f"Cannot delete a Project referenced by Delivery Run {run_id}",
        )
    # project_todos declares ON DELETE CASCADE, but SQLite does not enforce FKs
    # (no `PRAGMA foreign_keys=ON` in database.py), so the DB won't cascade.
    # Delete the todos explicitly so this works on SQLite too.
    # Sharing tables also need explicit cleanup: TeamProjectShare has no FK,
    # and relying on ProjectShare's FK would leave stale grants on SQLite.
    from backend.models.task_share import ProjectShare
    from backend.models.team_share import TeamProjectShare

    await db.execute(delete(ProjectTodo).where(ProjectTodo.project_id == project_id))
    await db.execute(delete(ProjectShare).where(ProjectShare.project_id == project_id))
    await db.execute(delete(TeamProjectShare).where(
        TeamProjectShare.project_id == project_id
    ))
    await db.delete(project)
    await db.commit()
    return {"ok": True}


@router.post("/{project_id}/reclone")
async def reclone_project(project_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await _require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.rollback()
    project = (
        await db.execute(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(404, "Project not found")
    if not project.has_remote:
        raise HTTPException(400, "Cannot reclone a local project")
    await _reject_active_delivery_project_mutation(
        db,
        project_id=project_id,
    )
    project.status = "pending"
    project.error_message = None
    await db.commit()
    global_cfg = await db.get(GlobalSettings, 1)
    git_config = merge_git_config(_extract_git_config(project), settings_to_dict(global_cfg))
    asyncio.create_task(_clone_repo(project_id, project.git_url, project.local_path, project.name, project.default_branch, git_config))
    return {"ok": True}


def _extract_git_config(project) -> dict:
    """Extract git config fields from a Project instance into a plain dict."""
    return {
        "git_author_name": project.git_author_name,
        "git_author_email": project.git_author_email,
        "git_credential_type": project.git_credential_type,
        "git_ssh_key_path": project.git_ssh_key_path,
        "git_https_username": project.git_https_username,
        "git_https_token": project.git_https_token,
    }


async def _apply_git_config(local_path: str, git_config: dict):
    """Write per-repo git config after clone/init so commits use the correct identity."""
    async def _git_config(key: str, value: str):
        proc = await asyncio.create_subprocess_exec(
            "git", "config", key, value,
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    if git_config.get("git_author_name"):
        await _git_config("user.name", git_config["git_author_name"])
    if git_config.get("git_author_email"):
        await _git_config("user.email", git_config["git_author_email"])

    ctype = git_config.get("git_credential_type")
    if ctype == "ssh" and git_config.get("git_ssh_key_path"):
        key_path = git_config["git_ssh_key_path"]
        ssh_cmd = f"ssh -i {key_path} -o StrictHostKeyChecking=no"
        await _git_config("core.sshCommand", ssh_cmd)
    elif ctype == "https" and git_config.get("git_https_token"):
        # Store credentials in the repo's local credential store so git push/pull can auth.
        # We write a plaintext .git/credentials file and point credential.helper at it.
        import pathlib
        from urllib.parse import urlparse
        creds_path = pathlib.Path(local_path) / ".git" / "credentials"
        username = git_config.get("git_https_username") or "oauth2"
        token = git_config["git_https_token"]
        # Extract host from remote URL; fall back to wildcard if not available
        host = ""
        try:
            remote_proc = await asyncio.create_subprocess_exec(
                "git", "remote", "get-url", "origin",
                cwd=local_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, _ = await remote_proc.communicate()
            remote_url = stdout_data.decode().strip()
            if remote_url.startswith("http"):
                parsed = urlparse(remote_url)
                host = parsed.hostname or ""
            elif ":" in remote_url and "@" in remote_url:
                # git@github.com:user/repo.git format
                host = remote_url.split("@")[1].split(":")[0]
        except Exception:
            pass
        if not host:
            host = "github.com"
        # Build credential lines for both https and http schemes
        creds_content = f"https://{username}:{token}@{host}\nhttp://{username}:{token}@{host}\n"
        creds_path.write_text(creds_content)
        # Reset credential helper chain first — an empty string clears all inherited
        # helpers (e.g. macOS osxkeychain) so they don't take priority over our store.
        await _git_config("credential.helper", "")
        await _git_config("credential.helper", f"store --file {creds_path}")


def _generate_claude_md(project_name: str, git_url: str | None, default_branch: str) -> str:
    """Generate a CLAUDE.md template for a new project."""
    remote_info = git_url if git_url else "无（纯本地项目）"
    return f"""# {project_name} — 项目指南

> **重要：Claude 必须自主维护本文件。** 架构或约定变化时更新，保持简洁。

## Git 信息

- Remote: {remote_info}
- 默认分支: {default_branch}

## 任务生命周期

你收到任务后，按以下 9 步流程自主完成：

1. **领取任务** — 你已被分配任务，阅读本文件和项目代码理解上下文
2. **创建工作区**:
   - `git fetch origin`（如有 remote）
   - 远端已有 `origin/{default_branch}` 时从它创建 worktree；首次发布前从本地 `{default_branch}` 创建
   - 进入 worktree 目录工作（后续所有操作在 worktree 中）
   - 如果 worktree 创建失败，直接在当前分支工作
3. **实现功能** — 编写代码，确保可运行
4. **提交代码** — `git add` + `git commit`，commit message 简洁描述改动
5. **Merge + 测试**:
   - `git fetch origin` 后仅在远端分支存在时合并 `origin/{default_branch}`
   - 运行测试（如有测试命令）
6. **自动合并到 {default_branch}**（如有 remote）:
   - `git fetch origin` 后若远端分支存在，则 `git rebase origin/{default_branch}` 并处理冲突
   - 若 `git ls-remote --refs origin` 成功且没有任何 refs，则以本地初始提交为基线，普通 push 创建远端分支
   - 如果成功：`git checkout {default_branch} && git merge <task-branch> && git push origin {default_branch}`
   - 如果这一步有任何失败，退回到步骤 5 重试
   - （纯本地项目跳过本步）
7. **标记完成** — 更新文档（必须在清理之前，防止进程被杀时状态丢失）
8. **清理** — 回到项目根目录:
   - `git worktree remove .claude-manager/worktrees/<worktree名>`
   - `git branch -D <task-branch>`
   - 如有 remote: `git push origin --delete <task-branch>`
9. **经验沉淀** — 在 PROGRESS.md 记录经验教训（可选）

### 冲突处理

rebase 发生冲突时：
1. 查看冲突文件: `git diff --name-only --diff-filter=U`
2. 逐个解决冲突
3. `git add <resolved-files> && git rebase --continue`
4. 如果无法解决: `git rebase --abort`，退回步骤 5

### 状态判断

- 通过 `git remote -v` 判断是否有 remote
- 有 remote → 必须完成步骤 6（merge + push）
- 无 remote → 跳过步骤 5 的 fetch、步骤 6 和步骤 8 的远程分支删除

## 文件维护规则

> **以下文件都由 Claude Code 自主维护，每次功能变更后必须同步更新。**

- **CLAUDE.md**（本文件）：架构、约定、关键路径变化时更新，只改变化的部分，保持简洁
- **AGENTS.md**（Codex 读取）：**与 CLAUDE.md 保持关键内容同步**——这是 CC/Codex coding 时的行为纪律：往其中一个写新内容时，把相同的意思也写进另一个（不要求逐字一致）。正常状态它是指向本文件的 symlink（改一处即同步），不要改成独立文件；若两者已是独立文件，不要用 symlink 覆盖已有内容，逐次同步意思即可
- **README.md**：面向用户的文档，功能、使用流程变化时同步更新，保持与实际代码一致
- **TEST.md**：测试指南，新增功能时同步添加测试用例和文档
- **PROGRESS.md**：见下方「经验教训沉淀」

## 测试规范

**开发时必须主动使用测试，不是事后补充！**

- **改代码前**：先跑测试，确认基线全绿
- **改代码后**：再跑一遍确认无回归
- **新增功能**：同步新增测试用例，更新 TEST.md
- **修 bug**：先写复现 bug 的测试（红），修复后确认变绿

## 经验教训沉淀

每次遇到问题或完成重要改动后，要在 PROGRESS.md 中记录：
- 遇到了什么问题
- 如何解决的
- 以后如何避免
- **必须附上 git commit ID**

**同样的问题不要犯两次！**

## 注意事项

- 在 worktree 中工作时，不要切换到其他分支
- 完成任务后确保代码可运行、测试通过
"""


# 实现移到 services/agent_docs.py（dispatcher 惰性补齐存量项目时复用）
_inject_agents_md = inject_agents_md


async def _project_git(
    local_path: str, *args: str, env: dict[str, str] | None = None,
    check: bool = True,
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=local_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if check and proc.returncode:
        raise RuntimeError(
            f"git {args[0]} failed: {stderr.decode(errors='replace').strip()}"
        )
    return proc.returncode, stdout.decode(errors="replace").strip()


async def _check_project_git_root(local_path: str) -> None:
    _, root = await _project_git(local_path, "rev-parse", "--show-toplevel")
    if pathlib.Path(root).resolve() != pathlib.Path(local_path).resolve():
        raise RuntimeError("Git checkout root does not match the Project workspace")


async def _prepare_project_head(
    local_path: str, default_branch: str, *, remote: bool,
    env: dict[str, str],
) -> bool:
    """Return whether a first local commit is required."""
    status, _ = await _project_git(
        local_path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False,
    )
    if status == 0:
        return False
    # An unresolved or detached HEAD is not proof of an empty repository.
    await _project_git(local_path, "symbolic-ref", "--quiet", "HEAD")
    if remote:
        _, remote_refs = await _project_git(
            local_path, "ls-remote", "--refs", "origin", env=env,
        )
        if remote_refs:
            raise RuntimeError(
                "Remote repository already contains refs but has no checked-out "
                "commit. Set its default branch to an existing branch and retry."
            )
    _, local_refs = await _project_git(local_path, "for-each-ref")
    if local_refs:
        raise RuntimeError("Project already has local refs but no checked-out commit")

    first_ref = f"refs/heads/{default_branch}"
    await _project_git(local_path, "check-ref-format", first_ref)
    await _project_git(local_path, "symbolic-ref", "HEAD", first_ref)
    return True


def _create_project_docs(
    local_path: str, project_name: str, git_url: str | None, default_branch: str,
) -> list[str]:
    files = []
    claude_md = pathlib.Path(local_path) / "CLAUDE.md"
    try:
        with claude_md.open("x", encoding="utf-8") as file:
            file.write(_generate_claude_md(project_name, git_url, default_branch))
        files.append("CLAUDE.md")
    except FileExistsError:
        pass
    if _inject_agents_md(local_path):
        files.append("AGENTS.md")
    return files


async def _commit_project_docs(
    local_path: str, files: list[str], *, first_commit: bool,
) -> None:
    if not files and not first_commit:
        return

    git_options = []
    if first_commit:
        # Use the configured identity when Git can resolve it. Keep the
        # service identity scoped to the initial commit otherwise.
        for ident in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            status, _ = await _project_git(local_path, "var", ident, check=False)
            if status:
                git_options = [
                    "-c", "user.name=Claude Code Manager",
                    "-c", "user.email=ccm@localhost",
                ]
                break

    try:
        if files:
            await _project_git(local_path, "add", "--", *files)
        message = (
            f"Add {' + '.join(files)} for Claude Code Manager"
            if files else "Initialize Claude Code Manager project"
        )
        # --only excludes any unrelated files already staged by the user.
        commit_args = ["commit", "--only", "-m", message]
        if files:
            commit_args.extend(["--", *files])
        else:
            commit_args.append("--allow-empty")
        await _project_git(local_path, *git_options, *commit_args)
    except RuntimeError:
        if first_commit:
            raise
        # Projects with an existing HEAD can still be used when a
        # best-effort documentation commit fails.


async def _setup_project_repo(
    project_id: int, git_url: str | None, local_path: str,
    project_name: str, default_branch: str, git_config: dict | None,
) -> None:
    lock = _project_setup_locks.setdefault(os.path.realpath(local_path), asyncio.Lock())
    async with lock:
        await _prepare_and_record_project_repo(
            project_id, git_url, local_path, project_name, default_branch, git_config,
        )


async def _prepare_and_record_project_repo(
    project_id: int, git_url: str | None, local_path: str,
    project_name: str, default_branch: str, git_config: dict | None,
) -> None:
    remote = git_url is not None
    async with async_session() as db:
        await db.execute(
            update(Project).where(Project.id == project_id).values(
                status="cloning" if remote else "initializing", error_message=None,
            )
        )
        await db.commit()

    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        if os.path.islink(local_path):
            raise RuntimeError("Project workspace cannot be a symbolic link")
        existing_checkout = os.path.lexists(os.path.join(local_path, ".git"))
        env = {**os.environ, **_build_git_env(git_config or {}), "GIT_TERMINAL_PROMPT": "0"}
        if existing_checkout:
            await _check_project_git_root(local_path)
            if remote:
                await _project_git(local_path, "fetch", "--all", env=env)
        elif remote:
            await _project_git(
                os.path.dirname(local_path), "clone", "--", git_url, local_path, env=env,
            )
        else:
            os.makedirs(local_path, exist_ok=True)
            await _project_git(local_path, "init", "-b", default_branch)

        first_commit = await _prepare_project_head(
            local_path, default_branch, remote=remote, env=env,
        )
        if remote or not existing_checkout or first_commit:
            if git_config:
                await _apply_git_config(local_path, git_config)
            files = _create_project_docs(local_path, project_name, git_url, default_branch)
            await _commit_project_docs(local_path, files, first_commit=first_commit)
        await _project_git(local_path, "rev-parse", "--verify", "HEAD^{commit}")
        values = {"status": "ready", "env_files": _scan_env_files(local_path)}
    except Exception as exc:
        values = {"status": "error", "error_message": str(exc)[:1000]}

    async with async_session() as db:
        await db.execute(update(Project).where(Project.id == project_id).values(**values))
        await db.commit()


async def _clone_repo(
    project_id: int, git_url: str, local_path: str, project_name: str,
    default_branch: str, git_config: dict | None = None,
) -> None:
    """Clone a remote repository and prepare a usable local baseline."""
    await _setup_project_repo(
        project_id, git_url, local_path, project_name, default_branch, git_config,
    )


async def _init_local_repo(
    project_id: int, local_path: str, project_name: str,
    default_branch: str, git_config: dict | None = None,
) -> None:
    """Initialize a local repository with a usable baseline."""
    await _setup_project_repo(
        project_id, None, local_path, project_name, default_branch, git_config,
    )


# ── Env files helpers ─────────────────────────────────────────────────────────

# Patterns to match when auto-scanning for .env files
_ENV_FILE_PATTERNS = [".env", ".env.*", "*.env"]
# Directories to skip during scan
_SCAN_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".claude-manager",
}


def _scan_env_files(local_path: str) -> list[str]:
    """Walk project tree and return relative paths of .env-style files."""
    found: list[str] = []
    root = pathlib.Path(local_path)
    for dirpath, dirnames, filenames in os.walk(local_path):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        for fname in filenames:
            if any(fnmatch.fnmatch(fname, pat) for pat in _ENV_FILE_PATTERNS):
                rel = str(pathlib.Path(dirpath, fname).relative_to(root))
                found.append(rel)
    return sorted(found)


def _safe_resolve(local_path: str, rel_path: str) -> pathlib.Path:
    """Resolve rel_path under local_path, raising 400 on path traversal."""
    root = pathlib.Path(local_path).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise HTTPException(400, "Invalid path")
    return target


# ── Env files endpoints ───────────────────────────────────────────────────────

class EnvFileInfo(BaseModel):
    path: str
    exists: bool


class EnvFilesListResponse(BaseModel):
    files: list[EnvFileInfo]


class EnvFileContent(BaseModel):
    content: str


class ScanEnvFilesResponse(BaseModel):
    tracked: list[str]    # already in env_files
    discovered: list[str] # found in repo but not yet tracked


@router.get("/{project_id}/env-files", response_model=EnvFilesListResponse)
async def list_env_files(
    project_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all configured env file paths and whether each exists on disk."""
    await require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.local_path:
        raise HTTPException(400, "Project has no local path")
    files = []
    for rel in (project.env_files or []):
        target = _safe_resolve(project.local_path, rel)
        files.append(EnvFileInfo(path=rel, exists=target.exists()))
    return EnvFilesListResponse(files=files)


@router.get("/{project_id}/env-files/{filepath:path}", response_model=EnvFileContent)
async def get_env_file(
    project_id: int,
    filepath: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Read content of a configured env file. Returns empty string if not yet created."""
    await require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.local_path:
        raise HTTPException(400, "Project has no local path")
    if filepath not in (project.env_files or []):
        raise HTTPException(403, "Path not in project env_files list")
    target = _safe_resolve(project.local_path, filepath)
    if not target.exists():
        return EnvFileContent(content="")
    return EnvFileContent(content=target.read_text(encoding="utf-8"))


@router.put("/{project_id}/env-files/{filepath:path}", response_model=EnvFileContent)
async def update_env_file(
    project_id: int,
    filepath: str,
    body: EnvFileContent,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Write content to a configured env file. Creates the file (and dirs) if needed."""
    await require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.local_path:
        raise HTTPException(400, "Project has no local path")
    if filepath not in (project.env_files or []):
        raise HTTPException(403, "Path not in project env_files list")
    target = _safe_resolve(project.local_path, filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return EnvFileContent(content=body.content)


@router.post("/{project_id}/scan-env-files", response_model=ScanEnvFilesResponse)
async def scan_env_files(
    project_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Scan the project repo for .env-style files and return discovered paths."""
    await require_project_access(request, project_id, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.local_path or not os.path.isdir(project.local_path):
        raise HTTPException(400, "Project has no local path or directory does not exist")
    tracked = list(project.env_files or [])
    tracked_set = set(tracked)
    all_found = _scan_env_files(project.local_path)
    discovered = [p for p in all_found if p not in tracked_set]
    return ScanEnvFilesResponse(tracked=tracked, discovered=discovered)
